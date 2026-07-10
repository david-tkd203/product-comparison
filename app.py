import sqlite3
import os
import json
import re
import urllib.request
from datetime import datetime
from difflib import SequenceMatcher
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import pandas as pd

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'cambiar-en-produccion')

DB = os.environ.get('DB_PATH', 'precios.db')


@app.context_processor
def inject_tunnel_url():
    url = ''
    path = os.environ.get('TUNNEL_URL_FILE', '')
    if path:
        try:
            with open(path) as f:
                url = f.read().strip()
        except (FileNotFoundError, OSError):
            pass
    if not url:
        url = os.environ.get('TUNNEL_URL', '')
    return dict(tunnel_url=url)

HEADER_ROW = 6  # 0-indexed row where column headers are (after metadata rows)
DATA_START = 7  # 0-indexed row where data begins
COLUMNS = ['linea', 'nombre', 'sku', 'ean', 'genero', 'formato', 'precio']


def get_db():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=30000')
    db.execute('''CREATE TABLE IF NOT EXISTS products (
        sku TEXT PRIMARY KEY,
        nombre TEXT,
        linea TEXT,
        ean TEXT,
        genero TEXT,
        formato TEXT
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT NOT NULL,
        import_date DATE NOT NULL,
        precio INTEGER NOT NULL,
        FOREIGN KEY (sku) REFERENCES products(sku)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        import_date DATE UNIQUE,
        product_count INTEGER
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS cosmetic_products (
        sku TEXT PRIMARY KEY,
        nombre TEXT,
        precio_retail INTEGER,
        precio_ref INTEGER,
        imagen TEXT,
        url TEXT,
        last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    # ponytail: add imagen column if DB was created before this migration
    try:
        db.execute('ALTER TABLE cosmetic_products ADD COLUMN imagen TEXT')
    except sqlite3.OperationalError:
        pass
    db.execute('''CREATE TABLE IF NOT EXISTS silk_products (
        sku TEXT PRIMARY KEY,
        nombre TEXT,
        precio_retail INTEGER,
        precio_ref INTEGER,
        imagen TEXT,
        url TEXT,
        last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS silk_matches (
        sku_wholesale TEXT PRIMARY KEY,
        sku_silk TEXT,
        confidence REAL
    )''')
    return db


def parse_excel(filepath, filename):
    df = pd.read_excel(filepath, engine='openpyxl', header=None)
    headers = df.iloc[HEADER_ROW].tolist()
    data = df.iloc[DATA_START:].copy()
    data.columns = headers

    # Normalize column names
    col_map = {
        'Linea': 'linea', 'Nombre': 'nombre', 'SKU': 'sku',
        'EAN': 'ean', 'GÉNERO': 'genero', 'G�NERO': 'genero',
        'Formato': 'formato', 'FORMATO': 'formato',
        'Precio': 'precio', 'PRECIO': 'precio'
    }
    data = data.rename(columns=col_map)
    data = data[['linea', 'nombre', 'sku', 'ean', 'genero', 'formato', 'precio']]
    data = data.dropna(subset=['sku'])
    data['sku'] = data['sku'].astype(str).str.strip()
    data['precio'] = pd.to_numeric(data['precio'], errors='coerce').fillna(0).astype(int)
    data['nombre'] = data['nombre'].astype(str).str.strip()
    data['linea'] = data['linea'].astype(str).str.strip()
    data['ean'] = data['ean'].astype(str).str.strip()
    data['genero'] = data['genero'].astype(str).str.strip()
    data['formato'] = data['formato'].astype(str).str.strip()

    return data


@app.route('/')
def index():
    query = request.args.get('q', '').strip()
    linea = request.args.get('linea', '').strip()
    genero = request.args.get('genero', '').strip()

    db = get_db()
    # Get available brands and genders for filters
    lineas = [r[0] for r in db.execute('SELECT DISTINCT linea FROM products ORDER BY linea')]
    generos = [r[0] for r in db.execute('SELECT DISTINCT genero FROM products ORDER BY genero')]

    # Get latest import date
    latest = db.execute('SELECT MAX(import_date) FROM imports').fetchone()[0]

    sql = '''SELECT p.sku, p.nombre, p.linea, p.ean, p.genero, p.formato, pr.precio
             FROM products p
             JOIN prices pr ON p.sku = pr.sku'''
    params = []

    conditions = []
    if latest:
        conditions.append('pr.import_date = ?')
        params.append(latest)
    if query:
        conditions.append('(p.nombre LIKE ? OR p.sku LIKE ? OR p.linea LIKE ?)')
        like = f'%{query}%'
        params.extend([like, like, like])
    if linea:
        conditions.append('p.linea = ?')
        params.append(linea)
    if genero:
        conditions.append('p.genero = ?')
        params.append(genero)

    if conditions:
        sql += ' WHERE ' + ' AND '.join(conditions)

    sql += ' ORDER BY p.linea, p.nombre LIMIT 500'
    products = db.execute(sql, params).fetchall()
    db.close()

    return render_template('index.html', products=products, query=query,
                          linea=linea, genero=genero, lineas=lineas,
                          generos=generos, latest=latest, total=len(products))


@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        file = request.files.get('file')
        import_date_str = request.form.get('import_date', '').strip()

        if not file or not file.filename.endswith('.xlsx'):
            flash('Subí un archivo .xlsx válido', 'error')
            return render_template('upload.html')

        if import_date_str:
            try:
                import_date = datetime.strptime(import_date_str, '%Y-%m-%d').date()
            except ValueError:
                flash('Fecha inválida. Usá YYYY-MM-DD', 'error')
                return render_template('upload.html')
        else:
            import_date = datetime.today().date()

        filepath = os.path.join('uploads', file.filename)
        os.makedirs('uploads', exist_ok=True)
        file.save(filepath)

        try:
            data = parse_excel(filepath, file.filename)
        except Exception as e:
            flash(f'Error al leer el Excel: {e}', 'error')
            return render_template('upload.html')

        db = get_db()
        try:
            # Check if this date already imported
            existing = db.execute('SELECT id FROM imports WHERE import_date = ?', [import_date]).fetchone()
            if existing:
                flash(f'Ya existe una importación con fecha {import_date}. Borrala primero si querés reemplazarla.', 'error')
                db.close()
                return render_template('upload.html')

            count = 0
            for _, row in data.iterrows():
                db.execute('''INSERT OR REPLACE INTO products (sku, nombre, linea, ean, genero, formato)
                              VALUES (?, ?, ?, ?, ?, ?)''',
                           [row['sku'], row['nombre'], row['linea'], row['ean'], row['genero'], row['formato']])
                db.execute('INSERT INTO prices (sku, import_date, precio) VALUES (?, ?, ?)',
                           [row['sku'], import_date, row['precio']])
                count += 1

            db.execute('INSERT INTO imports (filename, import_date, product_count) VALUES (?, ?, ?)',
                       [file.filename, import_date, count])
            db.commit()
            flash(f'Importados {count} productos con fecha {import_date}', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error en la base de datos: {e}', 'error')
        finally:
            db.close()

        return redirect(url_for('index'))

    return render_template('upload.html')


@app.route('/compare')
def compare():
    db = get_db()
    imports = db.execute('SELECT import_date, filename, product_count FROM imports ORDER BY import_date DESC').fetchall()

    a_date = request.args.get('a', '')
    b_date = request.args.get('b', '')

    diffs = []
    summary = None
    if a_date and b_date:
        diffs = db.execute('''
            SELECT p.sku, p.nombre, p.linea, p.genero, p.formato,
                   pa.precio as precio_a, pb.precio as precio_b,
                   (pb.precio - pa.precio) as diff,
                   CASE WHEN pa.precio > 0 THEN ROUND((pb.precio - pa.precio) * 100.0 / pa.precio, 1) ELSE NULL END as pct
            FROM products p
            JOIN prices pa ON p.sku = pa.sku AND pa.import_date = ?
            JOIN prices pb ON p.sku = pb.sku AND pb.import_date = ?
        ''', [a_date, b_date]).fetchall()

        total = len(diffs)
        subieron = sum(1 for d in diffs if d['diff'] > 0)
        bajaron = sum(1 for d in diffs if d['diff'] < 0)
        iguales = sum(1 for d in diffs if d['diff'] == 0)
        nuevos = db.execute('''
            SELECT COUNT(*) FROM prices pb
            WHERE pb.import_date = ?
            AND pb.sku NOT IN (SELECT sku FROM prices WHERE import_date = ?)
        ''', [b_date, a_date]).fetchone()[0]
        bajas = db.execute('''
            SELECT COUNT(*) FROM prices pa
            WHERE pa.import_date = ?
            AND pa.sku NOT IN (SELECT sku FROM prices WHERE import_date = ?)
        ''', [a_date, b_date]).fetchone()[0]

        summary = {
            'total': total, 'subieron': subieron, 'bajaron': bajaron,
            'iguales': iguales, 'nuevos': nuevos, 'bajas': bajas,
            'a_date': a_date, 'b_date': b_date
        }

    db.close()
    return render_template('compare.html', imports=imports, diffs=diffs,
                          summary=summary, a_date=a_date, b_date=b_date)


@app.route('/sync-cosmetic')
def sync_cosmetic():
    """Sync all products from cosmetic.cl (Shopify API)."""
    db = get_db()
    added = 0
    page = 1
    while True:
        url = f'https://cosmetic.cl/products.json?limit=250&page={page}'
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            flash(f'Error en página {page}: {e}', 'error')
            break

        products = data.get('products', [])
        if not products:
            break

        for p in products:
            for v in p.get('variants', []):
                sku = (v.get('sku') or '').strip()
                if not sku:
                    continue
                precio = int(v.get('price', 0))
                compare_at = int(v.get('compare_at_price') or 0)
                nombre = p.get('title', '')
                url_slug = f'https://cosmetic.cl/products/{p.get("handle", "")}'
                imagen = (p.get('images') or [{}])[0].get('src', '') if p.get('images') else ''
                db.execute('''INSERT OR REPLACE INTO cosmetic_products (sku, nombre, precio_retail, precio_ref, imagen, url)
                              VALUES (?, ?, ?, ?, ?, ?)''',
                           [sku, nombre, precio, compare_at, imagen, url_slug])
                added += 1

        page += 1

    db.commit()
    db.close()
    flash(f'Sincronizados {added} productos de cosmetic.cl', 'success')
    return redirect(url_for('retail'))


@app.route('/sync-silk')
def sync_silk():
    """Sync all products from silkperfumes.cl (Shopify API)."""
    db = get_db()
    added = 0
    page = 1
    while True:
        url = f'https://silkperfumes.cl/products.json?limit=250&page={page}'
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            flash(f'Error en página {page}: {e}', 'error')
            break

        products = data.get('products', [])
        if not products:
            break

        for p in products:
            for v in p.get('variants', []):
                sku = (v.get('sku') or '').strip()
                if not sku:
                    continue
                precio = int(v.get('price', 0))
                compare_at = int(v.get('compare_at_price') or 0)
                nombre = p.get('title', '')
                url_slug = f'https://silkperfumes.cl/products/{p.get("handle", "")}'
                imagen = (p.get('images') or [{}])[0].get('src', '') if p.get('images') else ''
                db.execute('''INSERT OR REPLACE INTO silk_products (sku, nombre, precio_retail, precio_ref, imagen, url)
                              VALUES (?, ?, ?, ?, ?, ?)''',
                           [sku, nombre, precio, compare_at, imagen, url_slug])
                added += 1

        page += 1
    db.commit()

    # Run fuzzy name matching after sync
    _match_silk_by_name(db)
    db.commit()

    db.close()
    flash(f'Sincronizados {added} productos de silkperfumes.cl', 'success')
    return redirect(url_for('retail'))


def _normalize(n):
    """Normalize perfume name for comparison."""
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', '', n.lower().replace('á','a').replace('é','e').replace('í','i').replace('ó','o').replace('ú','u'))).strip()


def _match_silk_by_name(db):
    """Fuzzy match wholesale products to silkperfumes by brand-filtered name matching."""
    db.execute('DELETE FROM silk_matches')
    wholesale = db.execute('SELECT sku, nombre, linea FROM products').fetchall()
    silk = db.execute('SELECT sku, nombre FROM silk_products').fetchall()

    # Index silk by first normalized token (usually brand)
    silk_by_brand = {}
    for s in silk:
        sn = _normalize(s['nombre'])
        token = sn.split()[0] if sn.split() else sn
        silk_by_brand.setdefault(token, []).append((sn, s['sku']))

    matched = 0
    for w in wholesale:
        w_norm = _normalize(w['nombre'])
        w_brand = _normalize(w['linea'])
        w_token = w_norm.split()[0] if w_norm.split() else w_norm

        # Try brand match first, then first-token match
        candidates = silk_by_brand.get(w_brand, []) + silk_by_brand.get(w_token, [])
        if not candidates:
            continue

        best_score = 0
        best_silk_sku = None
        for sn, ssku in candidates:
            # Quick pre-check: if lengths differ by >50%, skip
            if abs(len(w_norm) - len(sn)) > len(w_norm) * 0.5:
                continue
            score = SequenceMatcher(None, w_norm, sn).ratio()
            if score > best_score:
                best_score = score
                best_silk_sku = ssku

        if best_score >= 0.72:
            db.execute('INSERT INTO silk_matches (sku_wholesale, sku_silk, confidence) VALUES (?, ?, ?)',
                       [w['sku'], best_silk_sku, round(best_score, 3)])
            matched += 1

    return matched


@app.route('/retail')
def retail():
    """Compare wholesale prices vs cosmetic.cl retail."""
    query = request.args.get('q', '').strip()
    linea = request.args.get('linea', '').strip()
    sort = request.args.get('sort', 'diff_desc')

    db = get_db()
    latest = db.execute('SELECT MAX(import_date) FROM imports').fetchone()[0]
    lineas = [r[0] for r in db.execute('SELECT DISTINCT linea FROM products ORDER BY linea')]

    synced = db.execute('SELECT COUNT(*), MAX(last_synced) FROM cosmetic_products').fetchone()

    sql = '''SELECT p.sku, p.nombre, p.linea, p.genero, p.formato,
                    pr.precio as precio_mayorista,
                    cp.precio_retail as precio_cosmetic, cp.precio_ref as ref_cosmetic,
                    cp.imagen as img_cosmetic, cp.url as url_cosmetic,
                    sp.precio_retail as precio_silk, sp.precio_ref as ref_silk,
                    sp.imagen as img_silk, sp.url as url_silk,
                    (cp.precio_retail - pr.precio) as diff_cosmetic,
                    (sp.precio_retail - pr.precio) as diff_silk,
                    CASE WHEN cp.precio_retail > 0 THEN ROUND((cp.precio_retail - pr.precio) * 100.0 / cp.precio_retail, 1) ELSE NULL END as margen_cosmetic,
                    CASE WHEN sp.precio_retail > 0 THEN ROUND((sp.precio_retail - pr.precio) * 100.0 / sp.precio_retail, 1) ELSE NULL END as margen_silk
             FROM products p
             JOIN prices pr ON p.sku = pr.sku AND pr.import_date = ?
             JOIN cosmetic_products cp ON p.sku = cp.sku
             LEFT JOIN silk_matches sm ON p.sku = sm.sku_wholesale
             LEFT JOIN silk_products sp ON sm.sku_silk = sp.sku'''
    params = [latest] if latest else [None]

    conditions = []
    if query:
        conditions.append('(p.nombre LIKE ? OR p.sku LIKE ? OR p.linea LIKE ?)')
        like = f'%{query}%'
        params.extend([like, like, like])
    if linea:
        conditions.append('p.linea = ?')
        params.append(linea)

    if conditions:
        sql += ' AND ' + ' AND '.join(conditions)

    sort_map = {
        'diff_cosmetic_desc': '(cp.precio_retail - pr.precio) DESC',
        'diff_silk_desc': '(sp.precio_retail - pr.precio) DESC NULLS LAST',
        'margen_cosmetic': 'CASE WHEN cp.precio_retail > 0 THEN (cp.precio_retail - pr.precio) * 100.0 / cp.precio_retail ELSE 0 END DESC',
        'margen_silk': 'CASE WHEN sp.precio_retail > 0 THEN (sp.precio_retail - pr.precio) * 100.0 / sp.precio_retail ELSE 0 END DESC NULLS LAST',
        'linea': 'p.linea, p.nombre',
    }
    sql += f' ORDER BY {sort_map.get(sort, sort_map["diff_cosmetic_desc"])} LIMIT 500'

    products = db.execute(sql, params).fetchall()
    db.close()

    total = len(products)
    con_cosmetic = sum(1 for p in products if p['precio_cosmetic'] > 0)
    con_silk = sum(1 for p in products if p['precio_silk'] and p['precio_silk'] > 0)

    return render_template('retail.html', products=products, query=query,
                          linea=linea, lineas=lineas, latest=latest,
                          synced=synced, total=total,
                          con_cosmetic=con_cosmetic, con_silk=con_silk,
                          sort=sort)


@app.route('/retail/export')
def retail_export():
    """Export selected products as CSV."""
    skus = request.args.get('skus', '').split(',')
    if not skus or skus == ['']:
        flash('Seleccioná al menos un producto', 'error')
        return redirect(url_for('retail'))

    db = get_db()
    latest = db.execute('SELECT MAX(import_date) FROM imports').fetchone()[0]
    placeholders = ','.join('?' * len(skus))
    rows = db.execute(f'''
        SELECT p.sku, p.nombre, p.linea, p.genero, p.formato,
               pr.precio as mayorista,
               cp.precio_retail as cosmetic, cp.url as url_cosmetic, cp.imagen as img_cosmetic,
               sp.precio_retail as silk, sp.url as url_silk, sp.imagen as img_silk
        FROM products p
        JOIN prices pr ON p.sku = pr.sku AND pr.import_date = ?
        JOIN cosmetic_products cp ON p.sku = cp.sku
        LEFT JOIN silk_matches sm ON p.sku = sm.sku_wholesale
        LEFT JOIN silk_products sp ON sm.sku_silk = sp.sku
        WHERE p.sku IN ({placeholders})
        GROUP BY p.sku
        ORDER BY p.linea, p.nombre
    ''', [latest] + skus).fetchall()
    db.close()

    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['SKU', 'Marca', 'Nombre', 'Genero', 'Formato', 'Mayorista',
                     'cosmetic.cl', 'silkperfumes.cl',
                     'URL cosmetic', 'URL silk',
                     'Imagen cosmetic', 'Imagen silk'])
    for r in rows:
        writer.writerow([
            r['sku'], r['linea'], r['nombre'], r['genero'], r['formato'],
            r['mayorista'], r['cosmetic'] or '', r['silk'] or '',
            r['url_cosmetic'] or '', r['url_silk'] or '',
            r['img_cosmetic'] or '', r['img_silk'] or ''
        ])

    output.seek(0)
    from flask import make_response
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename=comparativa.csv'
    return resp


@app.route('/estudio')
def estudio():
    db = get_db()
    latest = db.execute('SELECT MAX(import_date) FROM imports').fetchone()[0]

    # SKU overlap stats
    total_wholesale = db.execute('SELECT COUNT(*) FROM products').fetchone()[0]
    total_cosmetic = db.execute('SELECT COUNT(*) FROM cosmetic_products').fetchone()[0]
    total_silk = db.execute('SELECT COUNT(*) FROM silk_products').fetchone()[0]

    matched_cosmetic = db.execute('SELECT COUNT(*) FROM products p JOIN cosmetic_products cp ON p.sku = cp.sku').fetchone()[0]
    matched_silk = db.execute('SELECT COUNT(*) FROM silk_matches').fetchone()[0]
    match_rate_cosmetic = round(matched_cosmetic * 100.0 / total_wholesale, 1) if total_wholesale else 0
    match_rate_silk = round(matched_silk * 100.0 / total_wholesale, 1) if total_wholesale else 0

    # Price gap analysis
    avg_gap = db.execute('''
        SELECT ROUND(AVG(cp.precio_retail - pr.precio)), ROUND(AVG((cp.precio_retail - pr.precio) * 100.0 / cp.precio_retail), 1)
        FROM products p
        JOIN prices pr ON p.sku = pr.sku AND pr.import_date = ?
        JOIN cosmetic_products cp ON p.sku = cp.sku
        WHERE cp.precio_retail > 0
    ''', [latest]).fetchone()

    # Brand performance: top brands by avg margin
    brand_stats = db.execute('''
        SELECT p.linea, COUNT(*) as n,
               ROUND(AVG(pr.precio)) as avg_costo,
               ROUND(AVG(cp.precio_retail)) as avg_retail,
               ROUND(AVG((cp.precio_retail - pr.precio) * 100.0 / cp.precio_retail), 1) as avg_margin
        FROM products p
        JOIN prices pr ON p.sku = pr.sku AND pr.import_date = ?
        JOIN cosmetic_products cp ON p.sku = cp.sku
        WHERE cp.precio_retail > 0
        GROUP BY p.linea
        HAVING COUNT(*) >= 3
        ORDER BY avg_margin DESC
        LIMIT 20
    ''', [latest]).fetchall()

    # Top opportunities: best margin products
    opportunities = db.execute('''
        SELECT p.sku, p.nombre, p.linea, pr.precio as costo,
               cp.precio_retail, cp.url, cp.imagen,
               (cp.precio_retail - pr.precio) as diff,
               ROUND((cp.precio_retail - pr.precio) * 100.0 / cp.precio_retail, 1) as margen
        FROM products p
        JOIN prices pr ON p.sku = pr.sku AND pr.import_date = ?
        JOIN cosmetic_products cp ON p.sku = cp.sku
        WHERE cp.precio_retail > 0
        ORDER BY margen DESC
        LIMIT 30
    ''', [latest]).fetchall()

    # Most expensive wholesale products
    top_cost = db.execute('''
        SELECT p.sku, p.nombre, p.linea, pr.precio, cp.imagen
        FROM products p
        JOIN prices pr ON p.sku = pr.sku AND pr.import_date = ?
        JOIN cosmetic_products cp ON p.sku = cp.sku
        ORDER BY pr.precio DESC LIMIT 10
    ''', [latest]).fetchall()

    db.close()

    return render_template('estudio.html',
                          total_wholesale=total_wholesale,
                          total_cosmetic=total_cosmetic,
                          total_silk=total_silk,
                          matched_cosmetic=matched_cosmetic,
                          matched_silk=matched_silk,
                          match_rate_cosmetic=match_rate_cosmetic,
                          match_rate_silk=match_rate_silk,
                          avg_gap=avg_gap,
                          brand_stats=brand_stats,
                          opportunities=opportunities,
                          top_cost=top_cost,
                          latest=latest)


if __name__ == '__main__':
    app.run(debug=True)
