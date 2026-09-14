import json
import time
from flask import Blueprint, render_template, request, jsonify

from ..db import get_db
from ..utils import _norm_genero, _genero_sql_conds, AROMA_FAMILIES, _classify_family

store_bp = Blueprint('store', __name__)

_NAV_CACHE = None
_NAV_CACHE_TIME = 0

def inject_nav_data():
    global _NAV_CACHE, _NAV_CACHE_TIME
    if request.path.startswith('/static/'):
        return {}
        
    now = time.time()
    if _NAV_CACHE is not None and (now - _NAV_CACHE_TIME) < 300:
        return dict(nav_marcas=_NAV_CACHE)
        
    try:
        db = get_db()
        marcas = db.cursor(dictionary=True)
        # Optimized: image is now in products.imagen
        marcas.execute('''
            SELECT linea, COUNT(*) AS n, MAX(imagen) AS imagen
            FROM products
            WHERE is_active = 1 AND stock > 0 AND linea IS NOT NULL AND linea != '' AND imagen IS NOT NULL
            GROUP BY linea
            ORDER BY n DESC LIMIT 6
        ''')
        marcas = marcas.fetchall()
        _NAV_CACHE = marcas
        _NAV_CACHE_TIME = now
        return dict(nav_marcas=marcas)
    except Exception:
        return dict(nav_marcas=[])


def _get_margen(db):
    from ..db import _get_margen as get_m
    return get_m(db)


def _tienda_sql():
    return '''SELECT p.sku, p.nombre, p.linea, p.genero, p.formato, p.aroma_family, p.stock,
                     p.precio_mayorista, p.precio_venta, p.imagen, p.aromas
              FROM products p
              WHERE p.is_active = 1 AND p.stock > 0'''


def _enrich_products(products):
    for p in products:
        p['genero_norm'] = _norm_genero(p.get('genero'))
        p['notas'] = {}
        if p.get('aromas'):
            try:
                p['notas'] = json.loads(p['aromas'])
            except (json.JSONDecodeError, TypeError):
                p['notas'] = {}
        familias = _classify_family(p['notas']) if p['notas'] else []
        if not familias and p.get('aroma_family'):
            familias = [p['aroma_family']]
        p['familias'] = familias
    return products


@store_bp.route('/')
def tienda_home():
    db = get_db()
    cur = db.cursor(dictionary=True)
    
    cur.execute(_tienda_sql() + '''
        ORDER BY (imagen IS NOT NULL) DESC, precio_venta DESC
        LIMIT 8''')
    destacados = cur.fetchall()
    _enrich_products(destacados)

    cur.execute('SELECT COUNT(*) AS cnt FROM products WHERE is_active = 1 AND stock > 0')
    total = cur.fetchone()['cnt']

    def _count_genero(genero_norm):
        conds, pats = _genero_sql_conds(genero_norm)
        if not conds: return 0
        sql = 'SELECT COUNT(*) AS cnt FROM products p WHERE is_active = 1 AND stock > 0 AND (' + ' OR '.join(conds) + ')'
        cur.execute(sql, pats)
        return cur.fetchone()['cnt']

    n_hombre = _count_genero('Hombre')
    n_mujer = _count_genero('Mujer')
    n_unisex = max(0, total - n_hombre - n_mujer)

    # Optimized brands query
    cur.execute('''
        SELECT linea, COUNT(*) AS n, MAX(imagen) AS imagen
        FROM products
        WHERE is_active = 1 AND stock > 0
        GROUP BY linea 
        ORDER BY n DESC LIMIT 16
    ''')
    marcas = cur.fetchall()

    cur.execute('''SELECT aroma_family AS fam, COUNT(*) AS n FROM products
                   WHERE is_active = 1 AND stock > 0 AND aroma_family IS NOT NULL
                   GROUP BY aroma_family ORDER BY n DESC''')
    familias = cur.fetchall()
    
    db.close()
    return render_template('tienda/home.html', destacados=destacados,
                           total=total, n_hombre=n_hombre, n_mujer=n_mujer,
                           n_unisex=n_unisex, marcas=marcas, familias=familias)


_CATALOGO_CACHE = {}

def _catalogo_response(preset_genero=None):
    global _CATALOGO_CACHE
    now = time.time()
    if len(_CATALOGO_CACHE) > 500:
        _CATALOGO_CACHE.clear()
        
    cache_key = request.full_path
    if preset_genero:
        cache_key = f"preset_{preset_genero}_{cache_key}"
        
    if cache_key in _CATALOGO_CACHE and (now - _CATALOGO_CACHE[cache_key]['time']) < 300:
        return _CATALOGO_CACHE[cache_key]['html']

    q = request.args.get('q', '').strip()
    genero_raw = preset_genero or (request.args.get('genero', '').strip() or None)
    genero = _norm_genero(genero_raw) if genero_raw else None
    linea = request.args.get('linea', '').strip()
    familia = request.args.get('familia', '').strip()
    sort = request.args.get('sort', 'destacados').strip()
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = 24

    def _int_arg(name):
        try:
            v = int(float(request.args.get(name, '') or 0))
            return v if v > 0 else None
        except ValueError:
            return None

    pmin = _int_arg('pmin')
    pmax = _int_arg('pmax')

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute('''SELECT DISTINCT linea FROM products
                   WHERE is_active = 1 AND stock > 0
                   ORDER BY linea''')
    lineas = [r['linea'] for r in cur.fetchall()]

    sql = _tienda_sql()
    params = []
    conditions = []
    if q:
        conditions.append('(p.nombre LIKE %s OR p.linea LIKE %s OR p.sku LIKE %s)')
        like = f'%{q}%'
        params.extend([like, like, like])
    if linea:
        conditions.append('p.linea = %s')
        params.append(linea)
    if genero:
        conds, pats = _genero_sql_conds(genero)
        if conds:
            conditions.append('(' + ' OR '.join(conds) + ')')
            params.extend(pats)
    if familia and familia in AROMA_FAMILIES:
        fam_conds = ['p.aroma_family = %s']
        params.append(familia)
        for kw in AROMA_FAMILIES[familia]:
            fam_conds.append('p.aromas LIKE %s')
            params.append(f'%{kw}%')
        conditions.append('(' + ' OR '.join(fam_conds) + ')')
    if pmin:
        conditions.append('p.precio_venta >= %s')
        params.append(pmin)
    if pmax:
        conditions.append('p.precio_venta <= %s')
        params.append(pmax)
    if conditions:
        sql += ' AND ' + ' AND '.join(conditions)

    sort_map = {
        'destacados': '(p.imagen IS NOT NULL) DESC, p.precio_venta DESC',
        'precio_asc': 'p.precio_venta ASC',
        'precio_desc': 'p.precio_venta DESC',
        'nombre': 'p.nombre ASC',
        'marca': 'p.linea ASC, p.nombre ASC',
    }
    sql += f' ORDER BY {sort_map.get(sort, sort_map["destacados"])}'

    cur.execute(f'SELECT COUNT(*) AS cnt FROM ({sql}) AS t', params)
    total = cur.fetchone()['cnt']
    offset = (page - 1) * per_page
    
    cur.execute(f'{sql} LIMIT %s OFFSET %s', params + [per_page, offset])
    products = cur.fetchall()
    total_pages = (total + per_page - 1) // per_page if total else 1
    _enrich_products(products)

    cur.execute('''SELECT MIN(precio_venta) AS pmin, MAX(precio_venta) AS pmax
                   FROM products
                   WHERE is_active = 1 AND stock > 0''')
    bounds = cur.fetchone()
    db.close()

    html = render_template('tienda/catalogo.html', products=products, q=q,
                           genero=genero, linea=linea, familia=familia, sort=sort,
                           pmin=pmin, pmax=pmax,
                           bounds_min=int(bounds['pmin'] or 0) if bounds['pmin'] else 0,
                           bounds_max=int(bounds['pmax'] or 0) if bounds['pmax'] else 0,
                           lineas=lineas, familias=sorted(AROMA_FAMILIES.keys()),
                           total=total, page=page, per_page=per_page,
                           total_pages=total_pages, preset=preset_genero or '')
    _CATALOGO_CACHE[cache_key] = {'time': now, 'html': html}
    return html

@store_bp.route('/perfumes')
def tienda_catalogo():
    return _catalogo_response(None)

@store_bp.route('/hombre')
def tienda_hombre():
    return _catalogo_response('Hombre')

@store_bp.route('/mujer')
def tienda_mujer():
    return _catalogo_response('Mujer')

@store_bp.route('/producto/<sku>')
def tienda_producto(sku):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute(_tienda_sql() + ' AND p.sku = %s', [sku])
    product = cur.fetchone()
    if not product:
        db.close()
        return render_template('tienda/404.html'), 404
    _enrich_products([product])

    cur.execute(_tienda_sql() + ' AND p.sku != %s AND p.linea = %s '
                + 'ORDER BY (p.imagen IS NOT NULL) DESC, p.precio_venta DESC LIMIT 4',
                [sku, product['linea']])
    relacionados = cur.fetchall()
    if len(relacionados) < 4:
        conds, pats = _genero_sql_conds(product['genero_norm'])
        extra_sql = _tienda_sql() + ' AND p.sku != %s AND p.linea != %s'
        extra_params = [sku, product['linea']]
        if conds:
            extra_sql += ' AND (' + ' OR '.join(conds) + ')'
            extra_params.extend(pats)
        extra_sql += ' ORDER BY (p.imagen IS NOT NULL) DESC, p.precio_venta DESC LIMIT %s'
        extra_params.append(4 - len(relacionados))
        cur.execute(extra_sql, extra_params)
        relacionados.extend(cur.fetchall())
    _enrich_products(relacionados)
    db.close()

    return render_template('tienda/producto.html', p=product, relacionados=relacionados)

@store_bp.route('/api/pedido', methods=['POST'])
def api_pedido():
    data = request.get_json(force=True, silent=True) or {}
    nombre = (data.get('nombre') or '').strip()
    telefono = (data.get('telefono') or '').strip()
    items = data.get('items') or []

    if not nombre or not telefono:
        return jsonify({'ok': False, 'error': 'Nombre y teléfono son obligatorios'}), 400
    if not isinstance(items, list) or not items:
        return jsonify({'ok': False, 'error': 'El carrito está vacío'}), 400

    db = get_db()
    autocommit = db.autocommit
    db.autocommit = False
    try:
        cur = db.cursor(dictionary=True)
        final_items = []
        errors = []
        for it in items:
            sku = str(it.get('sku') or '').strip()
            try:
                qty = min(99, max(1, int(it.get('qty') or 1)))
            except (TypeError, ValueError):
                qty = 1
            if not sku:
                continue
            cur.execute('''SELECT sku, nombre, linea, stock, precio_venta
                           FROM products
                           WHERE sku = %s AND is_active = 1 AND stock > 0''',
                        [sku])
            row = cur.fetchone()
            if not row:
                errors.append(f'{sku}: producto no disponible')
                continue
            if int(row['stock']) < qty:
                errors.append(f"{row['nombre']}: quedan {row['stock']} unidades")
                continue
            final_items.append({'sku': row['sku'], 'nombre': row['nombre'],
                                'linea': row['linea'], 'precio': int(row['precio_venta']),
                                'qty': qty})

        if errors:
            db.rollback()
            return jsonify({'ok': False, 'error': ' · '.join(errors)}), 400
        if not final_items:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Carrito vacío o sin stock'}), 400

        total = sum(i['precio'] * i['qty'] for i in final_items)
        items_json = json.dumps(final_items, ensure_ascii=False)

        cur.execute('''INSERT INTO orders (link_token, nombre, telefono, items_json, total, status)
                       VALUES (%s, %s, %s, %s, %s, 'nuevo')''',
                    ['WEB', nombre, telefono, items_json, total])
        
        for i in final_items:
            cur.execute('UPDATE products SET stock = stock - %s WHERE sku = %s', [i['qty'], i['sku']])
            
        db.commit()
        return jsonify({'ok': True, 'msg': 'Pedido registrado correctamente'})
    except Exception as e:
        db.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        db.autocommit = autocommit
        db.close()
