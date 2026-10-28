import json
import os
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, make_response, session
from ..db import get_db, rebuild_storefront_cache, set_setting
from ..utils import require_login, _norm_genero, parse_excel, AROMA_FAMILIES, _classify_family

admin_bp = Blueprint('admin', __name__)

@admin_bp.route('/admin')
@admin_bp.route('/admin/')
def admin_redirect():
    code = request.args.get('code')
    if code:
        import urllib.request, json
        shop = request.args.get('shop', 'dcw11w-0p.myshopify.com')
        url = f"https://{shop}/admin/oauth/access_token"
        client_id = os.environ.get('SHOPIFY_CLIENT_ID') or 'c7c86c013557a82ada1098651381f1fb'
        client_secret = os.environ.get('SHOPIFY_CLIENT_SECRET', '')
        if not client_secret:
            cur_s = get_db().cursor(dictionary=True)
            cur_s.execute("SELECT setting_value FROM settings WHERE setting_key = 'shopify_client_secret'")
            r_s = cur_s.fetchone()
            client_secret = r_s['setting_value'] if r_s else ''
        payload = json.dumps({
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                res_data = json.loads(resp.read().decode())
                token = res_data.get('access_token')
                if token:
                    db = get_db()
                    set_setting(db, 'shopify_access_token', token)
                    set_setting(db, 'shopify_shop', shop)
                    flash('¡Shopify conectado exitosamente! Token de acceso guardado.', 'success')
        except Exception as e:
            flash(f'Error al conectar con Shopify: {e}', 'error')

    if 'user_id' not in session:
        return redirect(url_for('admin.login'))
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

@admin_bp.route('/admin/activar-todo')
@require_login
def admin_activar_todo():
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE products SET is_active = 1, stock = CASE WHEN stock <= 0 THEN 5 ELSE stock END WHERE precio_venta > 0")
    db.commit()
    rebuild_storefront_cache(db)
    flash('Todo el catálogo con precio ha sido activado y publicado.', 'success')
    return redirect(url_for('admin.admin_inventario'))

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
    cur = db.cursor(dictionary=True)
    cur.execute('SELECT * FROM orders WHERE id = %s', [order_id])
    order = cur.fetchone()

    cur.execute('UPDATE orders SET status = %s WHERE id = %s', [status, order_id])
    db.commit()

    # Si se marca como completado, enviar confirmación con boleta PDF adjunta si tiene email
    if order and status == 'completado':
        customer_email = None
        if order.get('customer_id'):
            cur.execute("SELECT email FROM customers WHERE id = %s", [order['customer_id']])
            c_row = cur.fetchone()
            if c_row: customer_email = c_row['email']

        if customer_email:
            try:
                import json
                items = json.loads(order['items_json']) if order.get('items_json') else []
                from ..mailer import send_order_confirmation_email
                order_data = {
                    'id': order['id'],
                    'nombre': order.get('nombre'),
                    'telefono': order.get('telefono'),
                    'email': customer_email,
                    'items': items,
                    'total': order.get('total', 0),
                    'descuento': order.get('descuento', 0),
                    'cupon_codigo': order.get('cupon_codigo'),
                    'puntos_ganados': order.get('puntos_ganados', 0),
                    'fecha': order['created_at'].strftime("%d/%m/%Y %H:%M") if order.get('created_at') else None
                }
                send_order_confirmation_email(customer_email, order_data, attach_boleta=True)
            except Exception as e:
                print(f"[ADMIN ERROR] Error al enviar confirmación con boleta: {e}")

    elif order and status == 'cancelado':
        customer_email = None
        if order.get('customer_id'):
            cur.execute("SELECT email FROM customers WHERE id = %s", [order['customer_id']])
            c_row = cur.fetchone()
            if c_row: customer_email = c_row['email']

        if customer_email:
            try:
                from ..mailer import send_order_cancelled_email
                send_order_cancelled_email(customer_email, {
                    'id': order['id'],
                    'nombre': order.get('nombre'),
                    'motivo_cancelacion': 'Cancelado por administración o tiempo límite de reserva expirado'
                })
            except Exception as e:
                print(f"[ADMIN ERROR] Error al enviar correo de cancelación: {e}")

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


@admin_bp.route('/admin/sync-shopify-variants')
@require_login
def sync_shopify_variants():
    import urllib.request, json
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT setting_value FROM settings WHERE setting_key = 'shopify_shop'")
    s_row = cur.fetchone()
    shop = (s_row['setting_value'] if s_row else 'dcw11w-0p.myshopify.com').replace('https://', '').replace('http://', '').strip('/')
    if not shop: shop = 'dcw11w-0p.myshopify.com'
    
    sku_map = {}
    for page in range(1, 10):
        try:
            url = f"https://{shop}/products.json?limit=250&page={page}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                prods = data.get('products', [])
                if not prods:
                    break
                for p in prods:
                    for v in p.get('variants', []):
                        if v.get('sku'):
                            sku_map[v.get('sku').strip().upper()] = str(v.get('id'))
        except Exception:
            break
            
    updated = 0
    for sku, vid in sku_map.items():
        cur.execute("UPDATE products SET shopify_variant_id = %s WHERE sku = %s", [vid, sku])
        if cur.rowcount > 0:
            updated += 1
    db.commit()
    flash(f'¡Variantes de Shopify actualizadas! Se vincularon {updated} productos de {len(sku_map)} encontrados en Shopify.', 'success')
    return redirect(url_for('admin.admin_inventario'))


@admin_bp.route('/admin/sync-shopify')
@require_login
def sync_shopify():
    import urllib.request, json, time
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT setting_value FROM settings WHERE setting_key = 'shopify_access_token'")
    t_row = cur.fetchone()
    token = t_row['setting_value'] if t_row else None
    
    cur.execute("SELECT setting_value FROM settings WHERE setting_key = 'shopify_shop'")
    s_row = cur.fetchone()
    shop = s_row['setting_value'] if s_row else 'dcw11w-0p.myshopify.com'
    
    if not token:
        flash('Primero conecta Shopify abriendo el enlace de instalación.', 'error')
        return redirect(url_for('admin.admin_inventario'))
        
    cur.execute('''SELECT sku, nombre, linea, precio_venta, stock, imagen, aroma_family
                   FROM products
                   WHERE is_active = 1 AND stock > 0 AND precio_venta > 0''')
    prods = cur.fetchall()
    
    success = 0
    errors = 0
    
    for p in prods:
        sku = p['sku']
        title = p['nombre']
        vendor = p['linea'] or 'Siyash'
        price = str(p['precio_venta'])
        image_url = p['imagen']
        
        payload = {
            "product": {
                "title": title,
                "vendor": vendor,
                "product_type": "Perfume",
                "status": "active",
                "published_scope": "global",
                "variants": [
                    {
                        "sku": sku,
                        "price": price,
                        "requires_shipping": True
                    }
                ]
            }
        }
        if image_url and image_url.startswith('http'):
            payload["product"]["images"] = [{"src": image_url}]
            
        req = urllib.request.Request(
            f"https://{shop}/admin/api/2024-01/products.json",
            data=json.dumps(payload).encode('utf-8'),
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": token
            }
        )
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status in (200, 201):
                    success += 1
            time.sleep(0.5)
        except Exception:
            errors += 1
            
    flash(f'Sincronización completada: {success} productos creados en Shopify ({errors} errores).', 'success')
    return redirect(url_for('admin.admin_inventario'))

@admin_bp.route('/admin/export/shopify-csv')
@require_login
def export_shopify_csv():
    import csv, io
    from ..db import _get_margen
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute('''SELECT sku, nombre, linea, precio_mayorista, precio_venta, stock, imagen, aroma_family
                   FROM products
                   WHERE is_active = 1 AND stock > 0
                   ORDER BY linea, nombre''')
    products = cur.fetchall()
    margen = _get_margen(db)

    output = io.StringIO()
    writer = csv.writer(output)
    headers = [
        'Handle', 'Title', 'Body (HTML)', 'Vendor', 'Product Category', 'Type', 'Tags',
        'Published', 'Option1 Name', 'Option1 Value', 'Variant SKU', 'Variant Grams',
        'Variant Inventory Tracker', 'Variant Qty', 'Variant Inventory Policy',
        'Variant Fulfillment Service', 'Variant Price', 'Variant Compare At Price',
        'Variant Requires Shipping', 'Variant Taxable', 'Image Src', 'Image Position',
        'Status'
    ]
    writer.writerow(headers)

    for p in products:
        p_price = p.get('precio_venta')
        if not p_price and p.get('precio_mayorista'):
            p_price = int(round((p['precio_mayorista'] * (1 + margen / 100) + 10) / 1000) * 1000 - 10)
        p_price = p_price or 0
        if p_price <= 0:
            continue

        handle = re.sub(r'[^a-z0-9]+', '-', (p['nombre'] or p['sku']).lower()).strip('-')
        writer.writerow([
            handle,
            p['nombre'],
            f"<p>Perfume original {p['linea']}. Familia: {p['aroma_family'] or 'Exclusiva'}.</p>",
            p['linea'] or 'Siyash',
            'Health & Beauty > Personal Care > Cosmetics > Perfumes & Colognes',
            'Perfume',
            f"{p['linea']}, {p['aroma_family'] or ''}".strip(', '),
            'TRUE',
            'Title', 'Default Title',
            p['sku'],
            '500',
            'shopify',
            p['stock'],
            'deny',
            'manual',
            p_price,
            '',
            'TRUE',
            'TRUE',
            p['imagen'] or '',
            '1' if p['imagen'] else '',
            'active'
        ])

    output.seek(0)
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename=productos_siyash_shopify.csv'
    return resp


@admin_bp.route('/admin/premios', methods=['GET', 'POST'])
@require_login
def admin_premios():
    db = get_db()
    cur = db.cursor(dictionary=True)

    if request.method == 'POST':
        nombre = (request.form.get('nombre') or '').strip()
        descripcion = (request.form.get('descripcion') or '').strip()
        imagen = (request.form.get('imagen') or '').strip()
        try:
            puntos_requeridos = int(request.form.get('puntos_requeridos', 0))
        except (ValueError, TypeError):
            puntos_requeridos = 0
        try:
            stock = int(request.form.get('stock', 999))
        except (ValueError, TypeError):
            stock = 999

        if not nombre:
            flash('El nombre del premio es obligatorio.', 'error')
            return redirect(url_for('admin.admin_premios'))
        if puntos_requeridos <= 0:
            flash('Los puntos requeridos deben ser un número positivo.', 'error')
            return redirect(url_for('admin.admin_premios'))
        if stock < 0:
            stock = 0

        cur.execute(
            '''INSERT INTO rewards (nombre, descripcion, puntos_requeridos, imagen, stock, is_active)
               VALUES (%s, %s, %s, %s, %s, 1)''',
            [nombre, descripcion, puntos_requeridos, imagen or '/static/img/LogoSiyash-01-t.png', stock]
        )
        if hasattr(db, 'commit'):
            db.commit()
        flash(f"Premio '{nombre}' creado correctamente.", 'success')
        return redirect(url_for('admin.admin_premios'))

    # GET: List rewards with redemption stats
    cur.execute(
        '''SELECT r.*,
                  COUNT(crr.id) AS total_canjes,
                  SUM(CASE WHEN crr.estado = 'disponible' THEN 1 ELSE 0 END) AS canjes_disponibles,
                  SUM(CASE WHEN crr.estado = 'aplicado_en_pedido' THEN 1 ELSE 0 END) AS canjes_aplicados
           FROM rewards r
           LEFT JOIN customer_reward_redemptions crr ON crr.reward_id = r.id
           GROUP BY r.id
           ORDER BY r.puntos_requeridos ASC, r.id DESC'''
    )
    rewards = cur.fetchall()

    # General redemption stats
    cur.execute(
        '''SELECT COUNT(*) AS total_canjes,
                  SUM(CASE WHEN estado = 'disponible' THEN 1 ELSE 0 END) AS disponibles,
                  SUM(CASE WHEN estado = 'aplicado_en_pedido' THEN 1 ELSE 0 END) AS aplicados,
                  COALESCE(SUM(puntos_gastados), 0) AS total_puntos_canjeados
           FROM customer_reward_redemptions'''
    )
    stats = cur.fetchone() or {'total_canjes': 0, 'disponibles': 0, 'aplicados': 0, 'total_puntos_canjeados': 0}

    # Recent redemptions list (last 30)
    cur.execute(
        '''SELECT crr.id, crr.codigo_canje, crr.puntos_gastados, crr.estado, crr.order_id, crr.created_at,
                  c.nombre AS cliente_nombre, c.email AS cliente_email,
                  r.nombre AS premio_nombre
           FROM customer_reward_redemptions crr
           JOIN customers c ON c.id = crr.customer_id
           JOIN rewards r ON r.id = crr.reward_id
           ORDER BY crr.created_at DESC LIMIT 30'''
    )
    recent_redemptions = cur.fetchall()

    return render_template('admin_premios.html', rewards=rewards, stats=stats, recent_redemptions=recent_redemptions)


@admin_bp.route('/admin/premios/<int:reward_id>/toggle', methods=['POST'])
@require_login
def admin_premio_toggle(reward_id):
    db = get_db()
    cur = db.cursor()
    cur.execute('UPDATE rewards SET is_active = 1 - is_active WHERE id = %s', [reward_id])
    if hasattr(db, 'commit'):
        db.commit()
    flash('Estado del premio actualizado.', 'success')
    return redirect(url_for('admin.admin_premios'))


@admin_bp.route('/admin/premios/<int:reward_id>/eliminar', methods=['POST'])
@require_login
def admin_premio_eliminar(reward_id):
    db = get_db()
    cur = db.cursor()
    cur.execute('DELETE FROM rewards WHERE id = %s', [reward_id])
    if hasattr(db, 'commit'):
        db.commit()
    flash('Premio eliminado exitosamente.', 'success')
    return redirect(url_for('admin.admin_premios'))


@admin_bp.route('/admin/cupones', methods=['GET', 'POST'])
@require_login
def admin_cupones():
    db = get_db()
    cur = db.cursor(dictionary=True)

    if request.method == 'POST':
        codigo = (request.form.get('codigo') or '').strip().upper()
        tipo = (request.form.get('tipo') or '').strip().lower()
        customer_id_raw = (request.form.get('customer_id') or '').strip()
        valido_hasta_raw = (request.form.get('valido_hasta') or '').strip()

        try:
            valor = int(request.form.get('valor', 0))
        except (ValueError, TypeError):
            valor = 0

        try:
            minimo_compra = max(0, int(request.form.get('minimo_compra', 0) or 0))
        except (ValueError, TypeError):
            minimo_compra = 0

        limite_usos = None
        limite_usos_raw = (request.form.get('limite_usos') or '').strip()
        if limite_usos_raw:
            try:
                parsed_limite = int(limite_usos_raw)
                if parsed_limite > 0:
                    limite_usos = parsed_limite
            except (ValueError, TypeError):
                limite_usos = None

        customer_id = None
        if customer_id_raw and customer_id_raw not in ('', '0', 'none', 'todos'):
            try:
                customer_id = int(customer_id_raw)
            except (ValueError, TypeError):
                customer_id = None

        valido_hasta = None
        if valido_hasta_raw:
            try:
                # Handle YYYY-MM-DD or YYYY-MM-DDTHH:MM
                if 'T' in valido_hasta_raw:
                    valido_hasta = datetime.strptime(valido_hasta_raw, '%Y-%m-%dT%H:%M')
                else:
                    valido_hasta = datetime.strptime(valido_hasta_raw, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            except Exception:
                valido_hasta = None

        if not codigo:
            flash('El código del cupón es obligatorio.', 'error')
            return redirect(url_for('admin.admin_cupones'))

        if tipo not in ('porcentaje', 'monto_fijo'):
            flash('Tipo de cupón no válido. Selecciona Porcentaje o Monto Fijo.', 'error')
            return redirect(url_for('admin.admin_cupones'))

        if valor <= 0:
            flash('El valor del descuento debe ser mayor a 0.', 'error')
            return redirect(url_for('admin.admin_cupones'))

        if tipo == 'porcentaje' and (valor < 1 or valor > 100):
            flash('El porcentaje de descuento debe estar entre 1% y 100%.', 'error')
            return redirect(url_for('admin.admin_cupones'))

        cur.execute('SELECT id FROM coupons WHERE codigo = %s', [codigo])
        if cur.fetchone():
            flash(f"El cupón con código '{codigo}' ya existe.", 'error')
            return redirect(url_for('admin.admin_cupones'))

        cur.execute(
            '''INSERT INTO coupons (codigo, tipo, valor, minimo_compra, limite_usos, customer_id, valido_hasta, is_active)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 1)''',
            [codigo, tipo, valor, minimo_compra, limite_usos, customer_id, valido_hasta]
        )
        if hasattr(db, 'commit'):
            db.commit()
        flash(f"Cupón '{codigo}' creado exitosamente.", 'success')
        return redirect(url_for('admin.admin_cupones'))

    # GET: List coupons with customer info
    cur.execute(
        '''SELECT c.*, cust.nombre AS customer_nombre, cust.email AS customer_email
           FROM coupons c
           LEFT JOIN customers cust ON cust.id = c.customer_id
           ORDER BY c.created_at DESC'''
    )
    coupons = cur.fetchall()

    # KPI stats
    cur.execute(
        '''SELECT COUNT(*) AS total_cupones,
                  SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS activos,
                  COALESCE(SUM(usos_actuales), 0) AS total_usos
           FROM coupons'''
    )
    coupon_stats = cur.fetchone() or {'total_cupones': 0, 'activos': 0, 'total_usos': 0}

    cur.execute(
        '''SELECT COUNT(*) AS total_usos_reg,
                  COALESCE(SUM(descuento_aplicado), 0) AS total_descontado
           FROM coupon_usages'''
    )
    usage_stats = cur.fetchone() or {'total_usos_reg': 0, 'total_descontado': 0}

    kpi = {
        'total_activos': int(coupon_stats.get('activos') or 0),
        'total_usos': int(coupon_stats.get('total_usos') or usage_stats.get('total_usos_reg') or 0),
        'total_descontado': int(usage_stats.get('total_descontado') or 0)
    }

    # Customers list for dropdown
    cur.execute('SELECT id, nombre, email FROM customers WHERE is_active = 1 ORDER BY nombre ASC')
    customers = cur.fetchall()

    # Recent coupon usages (last 30)
    cur.execute(
        '''SELECT cu.id, cu.descuento_aplicado, cu.order_id, cu.created_at,
                  c.codigo AS coupon_codigo, c.tipo AS coupon_tipo,
                  cust.nombre AS customer_nombre, cust.email AS customer_email
           FROM coupon_usages cu
           JOIN coupons c ON c.id = cu.coupon_id
           LEFT JOIN customers cust ON cust.id = cu.customer_id
           ORDER BY cu.created_at DESC LIMIT 30'''
    )
    recent_usages = cur.fetchall()

    return render_template(
        'admin_cupones.html',
        coupons=coupons,
        kpi=kpi,
        customers=customers,
        recent_usages=recent_usages
    )


@admin_bp.route('/admin/cupones/<int:coupon_id>/toggle', methods=['POST'])
@require_login
def admin_cupon_toggle(coupon_id):
    db = get_db()
    cur = db.cursor()
    cur.execute('UPDATE coupons SET is_active = 1 - is_active WHERE id = %s', [coupon_id])
    if hasattr(db, 'commit'):
        db.commit()
    flash('Estado del cupón actualizado.', 'success')
    return redirect(url_for('admin.admin_cupones'))


@admin_bp.route('/admin/cupones/<int:coupon_id>/eliminar', methods=['POST'])
@require_login
def admin_cupon_eliminar(coupon_id):
    db = get_db()
    cur = db.cursor()
    cur.execute('DELETE FROM coupons WHERE id = %s', [coupon_id])
    if hasattr(db, 'commit'):
        db.commit()
    flash('Cupón eliminado exitosamente.', 'success')
    return redirect(url_for('admin.admin_cupones'))



@admin_bp.route('/admin/cyber')
@require_login
def admin_cyber():
    db = get_db()
    cur = db.cursor(dictionary=True)

    # 1. Setting cyber_mode_active
    cur.execute("SELECT setting_value FROM settings WHERE setting_key = 'cyber_mode_active'")
    r_mode = cur.fetchone()
    cyber_mode_active = r_mode['setting_value'] if r_mode and r_mode.get('setting_value') else '0'

    # 2. Total views
    cur.execute("SELECT COALESCE(SUM(views_count), 0) AS total_views FROM product_views")
    r_views = cur.fetchone()
    total_views = int(r_views['total_views']) if r_views else 0

    # 3. Total products in promo cyber
    cur.execute("SELECT COUNT(*) AS total_cyber FROM products WHERE en_cyber = 1")
    r_cyber = cur.fetchone()
    total_cyber_products = int(r_cyber['total_cyber']) if r_cyber else 0

    # 4. Most viewed products (Top 50)
    cur.execute('''
        SELECT pv.sku, pv.views_count, pv.last_viewed,
               p.nombre, p.linea, p.stock, p.precio_venta, p.imagen,
               COALESCE(p.en_cyber, 0) AS en_cyber,
               COALESCE(p.descuento_cyber_pct, 0) AS descuento_cyber_pct
        FROM product_views pv
        JOIN products p ON p.sku = pv.sku
        ORDER BY pv.views_count DESC
        LIMIT 50
    ''')
    most_viewed = cur.fetchall()
    top_viewed = most_viewed[0] if most_viewed else None

    # 5. Best sellers aggregated from orders
    cur.execute("SELECT items_json FROM orders WHERE status != 'cancelado'")
    orders_rows = cur.fetchall()
    sales_by_sku = {}
    for row in orders_rows:
        raw = row.get('items_json')
        if not raw:
            continue
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(items, list):
                for it in items:
                    sku = it.get('sku')
                    if not sku or str(sku).startswith('REW-'):
                        continue
                    qty = int(it.get('qty') or 1)
                    precio = int(it.get('precio') or 0)
                    if sku not in sales_by_sku:
                        sales_by_sku[sku] = {
                            'sku': sku,
                            'nombre': it.get('nombre', sku),
                            'linea': it.get('linea', ''),
                            'units_sold': 0,
                            'revenue': 0,
                            'imagen': None,
                            'stock': 0,
                            'precio_venta': precio,
                            'en_cyber': 0,
                            'descuento_cyber_pct': 0
                        }
                    sales_by_sku[sku]['units_sold'] += qty
                    sales_by_sku[sku]['revenue'] += (precio * qty)
        except Exception:
            pass

    # Enrich sales with current product info
    if sales_by_sku:
        skus = list(sales_by_sku.keys())
        format_strings = ','.join(['%s'] * len(skus))
        cur.execute(f'''
            SELECT sku, nombre, linea, imagen, stock, precio_venta,
                   COALESCE(en_cyber, 0) AS en_cyber,
                   COALESCE(descuento_cyber_pct, 0) AS descuento_cyber_pct
            FROM products WHERE sku IN ({format_strings})
        ''', skus)
        p_map = {p['sku']: p for p in cur.fetchall()}
        for sku, s in sales_by_sku.items():
            if sku in p_map:
                p_info = p_map[sku]
                s['nombre'] = p_info['nombre'] or s['nombre']
                s['linea'] = p_info['linea'] or s['linea']
                s['imagen'] = p_info['imagen']
                s['stock'] = p_info['stock']
                s['precio_venta'] = p_info['precio_venta']
                s['en_cyber'] = p_info['en_cyber']
                s['descuento_cyber_pct'] = p_info['descuento_cyber_pct']

    top_sellers = sorted(sales_by_sku.values(), key=lambda x: x['units_sold'], reverse=True)[:20]
    top_seller = top_sellers[0] if top_sellers else None

    # Available brands with active stock
    cur.execute('''
        SELECT DISTINCT linea, COUNT(*) AS cnt
        FROM products 
        WHERE linea IS NOT NULL AND linea != '' AND is_active = 1 AND stock > 0
        GROUP BY linea 
        ORDER BY linea ASC
    ''')
    brands = cur.fetchall()

    return render_template(
        'admin_cyber.html',
        cyber_mode_active=cyber_mode_active,
        total_views=total_views,
        total_cyber_products=total_cyber_products,
        top_viewed=top_viewed,
        top_seller=top_seller,
        most_viewed=most_viewed,
        top_sellers=top_sellers,
        brands=brands
    )


@admin_bp.route('/admin/cyber/toggle-modo', methods=['POST'])
@require_login
def admin_cyber_toggle_modo():
    from .store import clear_store_cache, clear_settings_cache
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT setting_value FROM settings WHERE setting_key = 'cyber_mode_active'")
    r = cur.fetchone()
    current = r['setting_value'] if r and r.get('setting_value') else '0'
    new_val = '0' if current == '1' else '1'
    set_setting(db, 'cyber_mode_active', new_val)
    clear_store_cache()
    clear_settings_cache()
    msg = '🔥 Modo Cyber GLOBAL ACTIVADO para toda la tienda.' if new_val == '1' else 'Modo Cyber GLOBAL Desactivado.'
    flash(msg, 'success')
    return redirect(url_for('admin.admin_cyber'))


@admin_bp.route('/admin/cyber/aplicar-masivo', methods=['POST'])
@require_login
def admin_cyber_aplicar_masivo():
    from .store import clear_store_cache
    target = (request.form.get('target') or '').strip()
    try:
        descuento_pct = int(request.form.get('descuento_pct') or 15)
    except (ValueError, TypeError):
        descuento_pct = 15
    descuento_pct = max(1, min(90, descuento_pct))

    db = get_db()
    cur = db.cursor()
    updated_count = 0

    if target == 'all':
        cur.execute('''
            UPDATE products 
            SET en_cyber = 1, descuento_cyber_pct = %s 
            WHERE stock > 0 AND is_active = 1
        ''', [descuento_pct])
        updated_count = cur.rowcount
    elif target == 'top_views':
        cur_d = db.cursor(dictionary=True)
        cur_d.execute('''
            SELECT pv.sku 
            FROM product_views pv
            JOIN products p ON p.sku = pv.sku
            WHERE p.stock > 0 AND p.is_active = 1
            ORDER BY pv.views_count DESC
            LIMIT 20
        ''')
        skus = [r['sku'] for r in cur_d.fetchall()]
        cur_d.close()
        if skus:
            fmt = ','.join(['%s'] * len(skus))
            cur.execute(f'''
                UPDATE products 
                SET en_cyber = 1, descuento_cyber_pct = %s 
                WHERE sku IN ({fmt})
            ''', [descuento_pct] + skus)
            updated_count = cur.rowcount
    elif target.startswith('brand:'):
        brand_name = target[6:]
        cur.execute('''
            UPDATE products 
            SET en_cyber = 1, descuento_cyber_pct = %s 
            WHERE linea = %s AND stock > 0 AND is_active = 1
        ''', [descuento_pct, brand_name])
        updated_count = cur.rowcount
    else:
        flash('Selección de objetivo no válida.', 'error')
        return redirect(url_for('admin.admin_cyber'))

    if hasattr(db, 'commit'):
        db.commit()
    clear_store_cache()
    flash(f'¡Éxito! Se aplicó {descuento_pct}% OFF Cyber a {updated_count} producto(s).', 'success')
    return redirect(url_for('admin.admin_cyber'))


@admin_bp.route('/admin/cyber/producto/<path:sku>', methods=['POST'])
@require_login
def admin_cyber_producto_toggle(sku):
    from .store import clear_store_cache
    en_cyber = request.form.get('en_cyber', '0')
    try:
        descuento_pct = int(request.form.get('descuento_pct') or 15)
    except (ValueError, TypeError):
        descuento_pct = 15
    descuento_pct = max(1, min(90, descuento_pct))

    db = get_db()
    cur = db.cursor()
    if en_cyber == '1':
        cur.execute('UPDATE products SET en_cyber = 1, descuento_cyber_pct = %s WHERE sku = %s', [descuento_pct, sku])
        flash(f'Producto {sku} agregado a Cyber con {descuento_pct}% OFF.', 'success')
    else:
        cur.execute('UPDATE products SET en_cyber = 0, descuento_cyber_pct = 0 WHERE sku = %s', [sku])
        flash(f'Producto {sku} retirado de la promoción Cyber.', 'success')
    if hasattr(db, 'commit'):
        db.commit()
    clear_store_cache()
    return redirect(url_for('admin.admin_cyber'))


@admin_bp.route('/admin/cyber/limpiar-todo', methods=['POST'])
@require_login
def admin_cyber_limpiar_todo():
    from .store import clear_store_cache, clear_settings_cache
    db = get_db()
    cur = db.cursor()
    cur.execute('UPDATE products SET en_cyber = 0, descuento_cyber_pct = 0')
    set_setting(db, 'cyber_mode_active', '0')
    if hasattr(db, 'commit'):
        db.commit()
    clear_store_cache()
    clear_settings_cache()
    flash('🚨 Campaña Cyber apagada: todos los productos volvieron a sus precios estándar.', 'success')
    return redirect(url_for('admin.admin_cyber'))
