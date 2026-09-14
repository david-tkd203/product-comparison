import os
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, make_response
from ..db import get_db, rebuild_storefront_cache, set_setting
from ..utils import require_login, _norm_genero, parse_excel, AROMA_FAMILIES, _classify_family

admin_bp = Blueprint('admin', __name__)

@admin_bp.route('/admin')
@admin_bp.route('/admin/')
@require_login
def admin_redirect():
    return redirect(url_for('admin.admin_productos'))

@admin_bp.route('/admin/productos')
@require_login
def admin_productos():
    query = request.args.get('q', '').strip()
    linea = request.args.get('linea', '').strip()
    genero = request.args.get('genero', '').strip()
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = 50

    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute('SELECT DISTINCT linea FROM products ORDER BY linea')
    lineas = [r['linea'] for r in cur.fetchall()]
    cur.execute('SELECT DISTINCT genero FROM products ORDER BY genero')
    generos = [r['genero'] for r in cur.fetchall()]
    
    # Optimized: Removed JOIN prices
    sql = '''SELECT p.sku, p.nombre, p.linea, p.ean, p.genero, p.formato, p.precio_mayorista AS precio
             FROM products p'''
    params = []
    conditions = []
    if query:
        conditions.append('(p.nombre LIKE %s OR p.sku LIKE %s OR p.linea LIKE %s)')
        like = f'%{query}%'
        params.extend([like, like, like])
    if linea:
        conditions.append('p.linea = %s')
        params.append(linea)
    if genero:
        conditions.append('p.genero = %s')
        params.append(genero)
    if conditions:
        sql += ' WHERE ' + ' AND '.join(conditions)
    sql += ' ORDER BY p.linea, p.nombre'

    cur.execute(f'SELECT COUNT(*) AS cnt FROM ({sql}) AS t', params)
    total = cur.fetchone()['cnt']
    offset = (page - 1) * per_page
    cur.execute(f'{sql} LIMIT %s OFFSET %s', params + [per_page, offset])
    products = cur.fetchall()
    total_pages = (total + per_page - 1) // per_page if total else 1
    
    cur.execute('SELECT MAX(import_date) as d FROM imports')
    latest_row = cur.fetchone()
    latest = latest_row['d'] if latest_row else None
    
    db.close()
    return render_template('admin_productos.html', products=products, query=query,
                          linea=linea, genero=genero, lineas=lineas,
                          generos=generos, latest=latest, total=total,
                          page=page, per_page=per_page, total_pages=total_pages)

@admin_bp.route('/upload', methods=['GET', 'POST'])
@require_login
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
            data = parse_excel(filepath)
        except Exception as e:
            flash(f'Error al leer el Excel: {e}', 'error')
            return render_template('upload.html')

        db = get_db()
        cur = db.cursor(dictionary=True)
        try:
            cur.execute('SELECT id FROM imports WHERE import_date = %s', [import_date])
            if cur.fetchone():
                flash(f'Ya existe una importación con fecha {import_date}.', 'error')
                db.close()
                return render_template('upload.html')

            count = 0
            for _, row in data.iterrows():
                fams = _classify_family({'salida': [str(row['nombre'])]})
                aroma = fams[0] if fams else None
                cur.execute('''INSERT INTO products (sku, nombre, linea, ean, genero, formato, aroma_family)
                               VALUES (%s, %s, %s, %s, %s, %s, %s)
                               ON DUPLICATE KEY UPDATE nombre=VALUES(nombre), linea=VALUES(linea),
                               ean=VALUES(ean), genero=VALUES(genero), formato=VALUES(formato),
                               aroma_family=COALESCE(aroma_family, VALUES(aroma_family))''',
                       [row['sku'], row['nombre'], row['linea'], row['ean'],
                        _norm_genero(row['genero']), row['formato'], aroma])
                cur.execute('INSERT INTO prices (sku, import_date, precio) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE precio = VALUES(precio)',
                       [row['sku'], import_date, row['precio']])
                count += 1

            cur.execute('INSERT INTO imports (filename, import_date, product_count) VALUES (%s, %s, %s)',
                   [file.filename, import_date, count])
            db.commit()
            
            # OPTIMIZATION: Materialize prices for storefront automatically
            rebuild_storefront_cache(db)
            
            flash(f'Importados {count} productos con fecha {import_date}', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error: {e}', 'error')
        finally:
            cur.close()
            db.close()
        return redirect(url_for('admin.admin_productos'))
    return render_template('upload.html')

@admin_bp.route('/compare')
@require_login
def compare():
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute('SELECT import_date, filename, product_count FROM imports ORDER BY import_date DESC')
    imports = cur.fetchall()
    a_date = request.args.get('a', '')
    b_date = request.args.get('b', '')
    diffs = []
    if a_date and b_date:
        cur.execute('''
            SELECT p.sku, p.nombre, p.linea, p.genero, p.formato,
                   pa.precio as precio_a, pb.precio as precio_b,
                   (pb.precio - pa.precio) as diff,
                   CASE WHEN pa.precio > 0 THEN ROUND((pb.precio - pa.precio) * 100.0 / pa.precio, 1) END as pct
            FROM products p
            JOIN prices pa ON p.sku = pa.sku AND pa.import_date = %s
            JOIN prices pb ON p.sku = pb.sku AND pb.import_date = %s
        ''', [a_date, b_date])
        diffs = cur.fetchall()
    db.close()
    return render_template('compare.html', imports=imports, a=a_date, b=b_date, diffs=diffs)

@admin_bp.route('/admin/inventario')
@require_login
def admin_inventario():
    query = request.args.get('q', '').strip()
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = 50

    db = get_db()
    from ..db import _get_margen
    margen = _get_margen(db)
    cur = db.cursor(dictionary=True)

    # Optimized: Removed JOIN prices
    sql = '''SELECT p.sku, p.nombre, p.linea, p.genero, p.stock, p.is_active, p.aroma_family,
                    p.precio_mayorista AS precio_mayor,
                    p.precio_venta
             FROM products p'''
    params = []
    if query:
        sql += ' WHERE (p.nombre LIKE %s OR p.sku LIKE %s OR p.linea LIKE %s)'
        like = f'%{query}%'
        params.extend([like, like, like])
    sql += ' ORDER BY p.linea, p.nombre'

    cur.execute(f'SELECT COUNT(*) AS cnt FROM ({sql}) AS t', params)
    total = cur.fetchone()['cnt']
    offset = (page - 1) * per_page
    cur.execute(f'{sql} LIMIT %s OFFSET %s', params + [per_page, offset])
    products = cur.fetchall()
    total_pages = (total + per_page - 1) // per_page if total else 1
    for p in products:
        p['genero_norm'] = _norm_genero(p.get('genero'))

    cur.execute('SELECT COUNT(*) AS cnt FROM products WHERE is_active = 1 AND stock > 0')
    activos = cur.fetchone()['cnt']
    db.close()

    return render_template('inventario.html', products=products, query=query,
                           total=total, page=page, per_page=per_page, total_pages=total_pages,
                           margen_tienda=margen, activos=activos,
                           familias=sorted(AROMA_FAMILIES.keys()))

@admin_bp.route('/api/update_inventory', methods=['POST'])
@require_login
def update_inventory():
    data = request.get_json()
    sku = data.get('sku')
    stock = data.get('stock')
    is_active = data.get('is_active')
    aroma_family = data.get('aroma_family')

    db = get_db()
    cur = db.cursor()
    try:
        cur.execute('UPDATE products SET stock=%s, is_active=%s, aroma_family=%s WHERE sku=%s',
               [stock, 1 if is_active else 0, aroma_family, sku])
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.rollback()
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        cur.close()
        db.close()

@admin_bp.route('/admin/config', methods=['POST'])
@require_login
def admin_config():
    margen = request.form.get('margen_tienda', '').strip().replace(',', '.')
    try:
        m = float(margen)
        if not (0 <= m <= 500):
            raise ValueError()
    except ValueError:
        flash('El margen debe ser un número entre 0 y 500', 'error')
        return redirect(url_for('admin.admin_inventario'))
    db = get_db()
    set_setting(db, 'margen_pct', int(m) if m == int(m) else m)
    rebuild_storefront_cache(db) # Important: rebuild cache when margin changes
    db.close()
    flash(f'Margen de la tienda actualizado a {int(m) if m == int(m) else m}%', 'success')
    return redirect(url_for('admin.admin_inventario'))

@admin_bp.route('/pedidos')
@require_login
def pedidos():
    db = get_db()
    cur = db.cursor(dictionary=True)
    status_filter = request.args.get('status', '').strip()
    sql = 'SELECT * FROM orders o'
    params = []
    if status_filter:
        sql += ' WHERE o.status = %s'
        params.append(status_filter)
    sql += ' ORDER BY o.created_at DESC LIMIT 200'
    cur.execute(sql, params)
    orders_list = cur.fetchall()

    import json
    for o in orders_list:
        try:
            o['items'] = json.loads(o['items_json'])
        except (json.JSONDecodeError, TypeError):
            o['items'] = []

    cur.execute('SELECT status, COUNT(*) as cnt FROM orders GROUP BY status')
    counts = cur.fetchall()
    db.close()
    return render_template('pedidos.html', orders=orders_list, counts=counts, status_filter=status_filter)

@admin_bp.route('/pedidos/<int:order_id>/status', methods=['POST'])
@require_login
def update_pedido_status(order_id):
    status = request.form.get('status')
    if status not in ['nuevo', 'procesando', 'completado', 'cancelado']:
        flash('Estado inválido', 'error')
        return redirect(url_for('admin.pedidos'))
    
    db = get_db()
    cur = db.cursor()
    cur.execute('UPDATE orders SET status = %s WHERE id = %s', [status, order_id])
    db.commit()
    db.close()
    flash(f'Pedido #{order_id} marcado como {status}', 'success')
    return redirect(url_for('admin.pedidos'))

@admin_bp.route('/export/xlsx')
@require_login
def export_xlsx():
    skus = request.args.get('skus', '').split(',')
    if not skus or skus == ['']:
        flash('Seleccioná al menos un producto', 'error')
        return redirect(url_for('admin.admin_productos'))

    db = get_db()
    cur = db.cursor(dictionary=True)
    placeholders = ','.join(['%s'] * len(skus))
    # Optimized: Removed JOIN prices
    cur.execute(f'''
        SELECT p.sku, p.nombre, p.linea, p.ean, p.genero, p.formato, p.precio_mayorista AS precio
        FROM products p
        WHERE p.sku IN ({placeholders})
        ORDER BY p.linea, p.nombre
    ''', skus)
    rows = cur.fetchall()
    db.close()

    import pandas as pd
    from io import BytesIO
    df = pd.DataFrame(rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Precios')
    output.seek(0)
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    resp.headers['Content-Disposition'] = 'attachment; filename=lista_precios.xlsx'
    return resp

from .admin_extra import *
from .admin_retail import *
