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


_HOME_CACHE = {'time': 0, 'html': None}

@store_bp.route('/')
def tienda_home():
    global _HOME_CACHE
    now = time.time()
    if _HOME_CACHE['html'] and (now - _HOME_CACHE['time']) < 60:
        return _HOME_CACHE['html']

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
        cur.execute('SELECT COUNT(*) AS cnt FROM products WHERE is_active = 1 AND stock > 0 AND genero = %s', [genero_norm])
        cnt = cur.fetchone()['cnt']
        if cnt > 0:
            return cnt
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
    
    html = render_template('tienda/home.html', destacados=destacados,
                           total=total, n_hombre=n_hombre, n_mujer=n_mujer,
                           n_unisex=n_unisex, marcas=marcas, familias=familias)
    _HOME_CACHE = {'time': now, 'html': html}
    return html


_CATALOGO_CACHE = {}

def _catalogo_response(preset_genero=None):
    global _CATALOGO_CACHE
    now = time.time()
    if len(_CATALOGO_CACHE) > 200:
        # Remove oldest 50 entries to avoid full cache miss spikes
        oldest = sorted(_CATALOGO_CACHE.keys(), key=lambda k: _CATALOGO_CACHE[k]['time'])[:50]
        for k in oldest:
            _CATALOGO_CACHE.pop(k, None)
        
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

@store_bp.route('/nosotros')
def tienda_nosotros():
    return render_template('tienda/nosotros.html')

@store_bp.route('/catalogo')
def tienda_catalogo_alias():
    return _catalogo_response(None)

@store_bp.route('/unisex')
def tienda_unisex():
    return _catalogo_response('Unisex')

@store_bp.route('/c/<path:subpath>')
def legacy_catalog(subpath=None):
    return _catalogo_response(None)

@store_bp.route('/producto/<path:sku>')
def tienda_producto(sku):
    clean_sku = (sku or '').strip()
    db = get_db()
    cur = db.cursor(dictionary=True)
    # Search for product in catalog (case-insensitive)
    cur.execute('''SELECT p.sku, p.nombre, p.linea, p.genero, p.formato, p.aroma_family, p.stock,
                          p.precio_mayorista, p.precio_venta, p.imagen, p.aromas, p.is_active
                   FROM products p
                   WHERE LOWER(p.sku) = LOWER(%s)
                   LIMIT 1''', [clean_sku])
    product = cur.fetchone()
    if not product:
        return render_template('tienda/404.html'), 404
    _enrich_products([product])

    cur.execute(_tienda_sql() + ' AND p.sku != %s AND p.linea = %s '
                + 'ORDER BY (p.imagen IS NOT NULL) DESC, p.precio_venta DESC LIMIT 4',
                [product['sku'], product['linea']])
    relacionados = cur.fetchall()
    if len(relacionados) < 4:
        conds, pats = _genero_sql_conds(product['genero_norm'])
        extra_sql = _tienda_sql() + ' AND p.sku != %s AND p.linea != %s'
        extra_params = [product['sku'], product['linea']]
        if conds:
            extra_sql += ' AND (' + ' OR '.join(conds) + ')'
            extra_params.extend(pats)
        extra_sql += ' ORDER BY (p.imagen IS NOT NULL) DESC, p.precio_venta DESC LIMIT %s'
        extra_params.append(4 - len(relacionados))
        cur.execute(extra_sql, extra_params)
        relacionados.extend(cur.fetchall())
    _enrich_products(relacionados)

    return render_template('tienda/producto.html', p=product, relacionados=relacionados)

@store_bp.route('/api/search')
def api_search():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify([])
    
    db = get_db()
    cur = db.cursor(dictionary=True)
    
    sql = """
        SELECT sku, nombre, linea, precio_venta, imagen 
        FROM products 
        WHERE is_active = 1 AND stock > 0 
        AND (nombre LIKE %s OR linea LIKE %s OR sku LIKE %s)
        ORDER BY (imagen IS NOT NULL) DESC, precio_venta DESC
        LIMIT 10
    """
    like = f'%{q}%'
    cur.execute(sql, [like, like, like])
    results = cur.fetchall()
    
    return jsonify(results)

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


@store_bp.route('/api/checkout-shopify', methods=['POST'])
def api_checkout_shopify():
    import os, urllib.request, json
    data = request.get_json(force=True, silent=True) or {}
    items = data.get('items') or []
    if not items or not isinstance(items, list):
        return jsonify({'ok': False, 'error': 'El carrito está vacío'}), 400

    db = get_db()
    cur = db.cursor(dictionary=True)
    
    # Get Shopify shop domain
    cur.execute("SELECT setting_value FROM settings WHERE setting_key = 'shopify_shop'")
    s_row = cur.fetchone()
    shop = (s_row['setting_value'] if s_row else os.environ.get('SHOPIFY_SHOP', 'dcw11w-0p.myshopify.com')).replace('https://', '').replace('http://', '').strip('/')
    if not shop:
        shop = 'dcw11w-0p.myshopify.com'
    
    # Get Access Token to fetch variants or create products if needed
    cur.execute("SELECT setting_value FROM settings WHERE setting_key = 'shopify_access_token'")
    t_row = cur.fetchone()
    token = (t_row['setting_value'] if t_row else None) or os.environ.get('SHOPIFY_ACCESS_TOKEN')

    # Collect variant IDs
    cart_lines = []
    missing_skus = []
    sku_to_qty = {}
    
    for it in items:
        sku = str(it.get('sku') or '').strip()
        try:
            qty = max(1, min(99, int(it.get('qty') or 1)))
        except (TypeError, ValueError):
            qty = 1
            
        sku_to_qty[sku] = qty
        cur.execute("SELECT sku, nombre, stock, shopify_variant_id FROM products WHERE sku = %s", [sku])
        p = cur.fetchone()
        if not p:
            continue
            
        variant_id = p.get('shopify_variant_id')
        if variant_id:
            cart_lines.append(f"{variant_id}:{qty}")
        else:
            missing_skus.append(sku)

    # 1. If any SKUs are missing variant_id, query Shopify's public products.json (works without token!)
    if missing_skus:
        sku_map = {}
        for page in (1, 2, 3, 4):
            try:
                pub_url = f"https://{shop}/products.json?limit=250&page={page}"
                req = urllib.request.Request(pub_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    res_data = json.loads(resp.read().decode())
                    prods = res_data.get('products', [])
                    if not prods:
                        break
                    for prod in prods:
                        for v in prod.get('variants', []):
                            v_sku = v.get('sku')
                            if v_sku:
                                sku_map[v_sku.strip().upper()] = str(v.get('id'))
            except Exception:
                break

        for sku in list(missing_skus):
            vid = sku_map.get(sku.upper())
            if vid:
                cur.execute("UPDATE products SET shopify_variant_id = %s WHERE sku = %s", [vid, sku])
                db.commit()
                cart_lines.append(f"{vid}:{sku_to_qty.get(sku, 1)}")
                missing_skus.remove(sku)

    # 2. If SKUs are STILL missing and we have an Admin API token, auto-create the missing products in Shopify
    if missing_skus and token:
        for sku in list(missing_skus):
            cur.execute("SELECT sku, nombre, linea, precio_venta, imagen FROM products WHERE sku = %s", [sku])
            p = cur.fetchone()
            if not p or not p.get('precio_venta'):
                continue
            try:
                payload = {
                    "product": {
                        "title": p['nombre'],
                        "vendor": p['linea'] or "Siyash",
                        "product_type": "Perfume",
                        "status": "active",
                        "published_scope": "global",
                        "variants": [{
                            "sku": sku,
                            "price": str(p['precio_venta']),
                            "requires_shipping": True
                        }]
                    }
                }
                if p.get('imagen') and str(p['imagen']).startswith('http'):
                    payload["product"]["images"] = [{"src": p['imagen']}]
                post_req = urllib.request.Request(
                    f"https://{shop}/admin/api/2024-01/products.json",
                    data=json.dumps(payload).encode('utf-8'),
                    headers={
                        "Content-Type": "application/json",
                        "X-Shopify-Access-Token": token
                    }
                )
                with urllib.request.urlopen(post_req, timeout=6) as post_resp:
                    created = json.loads(post_resp.read().decode())
                    new_vid = str(created['product']['variants'][0]['id'])
                    cur.execute("UPDATE products SET shopify_variant_id = %s WHERE sku = %s", [new_vid, sku])
                    db.commit()
                    cart_lines.append(f"{new_vid}:{sku_to_qty.get(sku, 1)}")
                    missing_skus.remove(sku)
            except Exception:
                pass

    if not cart_lines:
        return jsonify({
            'ok': False,
            'error': 'Los productos de tu selección aún se están sincronizando con Shopify. Por favor utiliza la opción de WhatsApp temporalmente mientras finaliza la sincronización.'
        }), 400

    checkout_url = f"https://{shop}/cart/{','.join(cart_lines)}"
    return jsonify({'ok': True, 'checkout_url': checkout_url})
