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
    sql = '''SELECT p.sku, p.ean, p.nombre, p.linea, p.genero, p.stock, p.is_active, p.aroma_family,
                    p.precio_mayorista AS precio_mayor,
                    p.precio_venta
             FROM products p'''
    params = []
    if query:
        sql += ' WHERE (p.nombre LIKE %s OR p.sku LIKE %s OR p.linea LIKE %s OR (p.ean IS NOT NULL AND p.ean LIKE %s))'
        like = f'%{query}%'
        params.extend([like, like, like, like])
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


@admin_bp.route('/admin/sync-shopify', methods=['GET', 'POST'])
@require_login
def sync_shopify():
    from ..shopify_api import sync_catalog_to_shopify, get_shopify_credentials
    db = get_db()
    shop, token = get_shopify_credentials(db)
    
    if not token:
        flash('Primero conecta Shopify con el Access Token para autorizar la API.', 'error')
        return redirect(url_for('admin.admin_inventario'))
        
    limit = request.args.get('limit', 100, type=int)
    success, errors_count, error_msgs = sync_catalog_to_shopify(db=db, limit=limit)
    if errors_count == 0:
        flash(f'¡Sincronización exitosa con Shopify! {success} productos sincronizados con EAN y metacampos.', 'success')
    else:
        sample_err = f" (Ej: {error_msgs[0]})" if error_msgs else ""
        flash(f'Sincronización con Shopify: {success} exitosos, {errors_count} con alertas{sample_err}.', 'warning')
    return redirect(url_for('admin.admin_inventario'))


@admin_bp.route('/admin/productos/<sku>/sync-shopify', methods=['POST'])
@require_login
def admin_producto_sync_shopify(sku):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT sku, ean, nombre, linea, genero, formato, aroma_family,
               precio_mayorista, precio_venta, stock, imagen, aromas,
               shopify_product_id, shopify_variant_id
        FROM products WHERE sku = %s
    """, [sku])
    prod = cur.fetchone()
    if not prod:
        flash(f"Producto con SKU '{sku}' no encontrado.", 'error')
        return redirect(url_for('admin.admin_inventario'))

    from ..shopify_api import sync_product_to_shopify
    ok, prod_id, var_id, err = sync_product_to_shopify(prod, db=db)
    if ok:
        flash(f"'{prod['nombre']}' sincronizado con Shopify exitosamente (ID: {prod_id}).", 'success')
    else:
        flash(f"Error sincronizando '{prod['nombre']}' con Shopify: {err}", 'error')

    return redirect(request.referrer or url_for('admin.admin_inventario'))

@admin_bp.route('/admin/export/shopify-csv')
@require_login
def export_shopify_csv():
    import csv, io, re
    from ..db import _get_margen
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute('''SELECT sku, ean, nombre, linea, genero, formato, precio_mayorista, precio_venta, stock, imagen, aroma_family
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
        'Variant Requires Shipping', 'Variant Taxable', 'Variant Barcode', 'Image Src', 'Image Position',
        'Status',
        'EAN (product.metafields.facts.ean)',
        'Familia Olfativa (product.metafields.custom.aroma_family)',
        'Concentracion (product.metafields.custom.concentracion)'
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
            f"{p['linea']}, {p['aroma_family'] or ''}, {p.get('genero') or ''}".strip(', '),
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
            p.get('ean') or '',
            p['imagen'] or '',
            '1' if p['imagen'] else '',
            'active',
            p.get('ean') or '',
            p.get('aroma_family') or '',
            p.get('formato') or ''
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
        coupon_id = cur.lastrowid
        if hasattr(db, 'commit'):
            db.commit()

        shopify_msg = ""
        try:
            from ..shopify_api import sync_coupon_to_shopify
            ok_sh, rule_id, code_id, err_sh = sync_coupon_to_shopify({
                'id': coupon_id,
                'codigo': codigo,
                'tipo': tipo,
                'valor': valor,
                'minimo_compra': minimo_compra,
                'limite_usos': limite_usos,
                'valido_hasta': valido_hasta,
                'is_active': 1
            }, db=db)
            if ok_sh:
                shopify_msg = " y sincronizado con Shopify exitosamente."
            elif err_sh:
                shopify_msg = f" (Aviso Shopify: {err_sh})."
        except Exception as e:
            shopify_msg = f" (No se pudo conectar con Shopify: {e})."

        flash(f"Cupón '{codigo}' creado{shopify_msg}", 'success' if "exitosamente" in shopify_msg or not shopify_msg else 'warning')
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
    cur = db.cursor(dictionary=True)
    cur.execute('SELECT * FROM coupons WHERE id = %s', [coupon_id])
    coupon = cur.fetchone()
    if not coupon:
        flash('Cupón no encontrado.', 'error')
        return redirect(url_for('admin.admin_cupones'))

    new_status = 0 if coupon['is_active'] else 1
    cur.execute('UPDATE coupons SET is_active = %s WHERE id = %s', [new_status, coupon_id])
    if hasattr(db, 'commit'):
        db.commit()

    coupon['is_active'] = new_status
    try:
        from ..shopify_api import sync_coupon_to_shopify
        sync_coupon_to_shopify(coupon, db=db)
    except Exception:
        pass

    flash(f"Cupón '{coupon['codigo']}' {'activado y sincronizado con Shopify' if new_status else 'desactivado'}.", 'success')
    return redirect(url_for('admin.admin_cupones'))


@admin_bp.route('/admin/cupones/<int:coupon_id>/eliminar', methods=['POST'])
@require_login
def admin_cupon_eliminar(coupon_id):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute('SELECT * FROM coupons WHERE id = %s', [coupon_id])
    coupon = cur.fetchone()
    if coupon and coupon.get('shopify_price_rule_id'):
        try:
            from ..shopify_api import delete_coupon_from_shopify
            delete_coupon_from_shopify(coupon['shopify_price_rule_id'], db=db)
        except Exception:
            pass
    cur.execute('DELETE FROM coupons WHERE id = %s', [coupon_id])
    if hasattr(db, 'commit'):
        db.commit()
    flash(f"Cupón '{coupon['codigo'] if coupon else coupon_id}' eliminado exitosamente.", 'success')
    return redirect(url_for('admin.admin_cupones'))


@admin_bp.route('/admin/cupones/<int:coupon_id>/sync-shopify', methods=['POST'])
@require_login
def admin_cupon_sync_shopify(coupon_id):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute('SELECT * FROM coupons WHERE id = %s', [coupon_id])
    coupon = cur.fetchone()
    if not coupon:
        flash('Cupón no encontrado.', 'error')
        return redirect(url_for('admin.admin_cupones'))

    try:
        from ..shopify_api import sync_coupon_to_shopify
        ok, rule_id, code_id, err = sync_coupon_to_shopify(coupon, db=db)
        if ok:
            flash(f"Cupón '{coupon['codigo']}' sincronizado con Shopify exitosamente.", 'success')
        else:
            flash(f"Error sincronizando '{coupon['codigo']}' con Shopify: {err}", 'error')
    except Exception as e:
        flash(f"Excepción al conectar con Shopify: {e}", 'error')

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


# -------------------------------------------------------------
# GESTIÓN DE USUARIOS / CLIENTES (CLUB SIYASH)
# -------------------------------------------------------------

@admin_bp.route('/admin/usuarios')
@require_login
def admin_usuarios():
    db = get_db()
    cur = db.cursor(dictionary=True)

    q = (request.args.get('q') or '').strip()
    status_filter = (request.args.get('status') or 'todos').strip()
    order_by = (request.args.get('order_by') or 'recientes').strip()

    sql = '''
        SELECT c.*,
               COUNT(DISTINCT o.id) AS total_pedidos,
               COALESCE(SUM(o.total), 0) AS total_gastado,
               MAX(o.created_at) AS ultimo_pedido
        FROM customers c
        LEFT JOIN orders o ON o.customer_id = c.id
        WHERE 1=1
    '''
    params = []

    if q:
        sql += " AND (c.nombre LIKE %s OR c.email LIKE %s OR c.telefono LIKE %s)"
        like_q = f"%{q}%"
        params.extend([like_q, like_q, like_q])

    if status_filter == 'activos':
        sql += " AND c.is_active = 1"
    elif status_filter == 'inactivos':
        sql += " AND c.is_active = 0"

    sql += " GROUP BY c.id"

    if order_by == 'nombre':
        sql += " ORDER BY c.nombre ASC"
    elif order_by == 'puntos':
        sql += " ORDER BY c.puntos DESC"
    elif order_by == 'gastado':
        sql += " ORDER BY total_gastado DESC"
    elif order_by == 'pedidos':
        sql += " ORDER BY total_pedidos DESC"
    else:
        sql += " ORDER BY c.created_at DESC"

    cur.execute(sql, params)
    customers = cur.fetchall()

    # Métricas Globales
    cur.execute('''
        SELECT COUNT(*) AS total_usuarios,
               SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS activos,
               SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END) AS inactivos,
               COALESCE(SUM(puntos), 0) AS puntos_totales
        FROM customers
    ''')
    kpi_stats = cur.fetchone() or {'total_usuarios': 0, 'activos': 0, 'inactivos': 0, 'puntos_totales': 0}

    cur.execute('SELECT COALESCE(SUM(total), 0) AS facturacion_clientes FROM orders WHERE customer_id IS NOT NULL')
    fact_row = cur.fetchone() or {'facturacion_clientes': 0}

    kpis = {
        'total_usuarios': int(kpi_stats.get('total_usuarios') or 0),
        'activos': int(kpi_stats.get('activos') or 0),
        'inactivos': int(kpi_stats.get('inactivos') or 0),
        'puntos_totales': int(kpi_stats.get('puntos_totales') or 0),
        'facturacion_clientes': int(fact_row.get('facturacion_clientes') or 0)
    }

    return render_template(
        'admin_usuarios.html',
        customers=customers,
        kpis=kpis,
        q=q,
        status_filter=status_filter,
        order_by=order_by
    )


@admin_bp.route('/admin/usuarios/crear', methods=['POST'])
@require_login
def admin_usuario_crear():
    nombre = (request.form.get('nombre') or '').strip()
    email = (request.form.get('email') or '').strip().lower()
    telefono = (request.form.get('telefono') or '').strip()
    password = (request.form.get('password') or '').strip()
    try:
        puntos = max(0, int(request.form.get('puntos', 100)))
    except (ValueError, TypeError):
        puntos = 100

    if not nombre or not email:
        flash('Nombre y correo electrónico son obligatorios.', 'error')
        return redirect(url_for('admin.admin_usuarios'))

    from werkzeug.security import generate_password_hash
    import secrets

    if not password:
        password = secrets.token_urlsafe(8)

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute('SELECT id FROM customers WHERE email = %s', [email])
    if cur.fetchone():
        flash(f"El correo '{email}' ya se encuentra registrado por otro usuario.", 'error')
        return redirect(url_for('admin.admin_usuarios'))

    cur.execute('''
        INSERT INTO customers (nombre, email, telefono, password_hash, puntos, is_active)
        VALUES (%s, %s, %s, %s, %s, 1)
    ''', [nombre, email, telefono, generate_password_hash(password), puntos])
    customer_id = cur.lastrowid

    if puntos > 0:
        cur.execute('''
            INSERT INTO customer_points_history (customer_id, puntos, motivo)
            VALUES (%s, %s, %s)
        ''', [customer_id, puntos, 'Bono inicial asignado por administración'])

    if hasattr(db, 'commit'):
        db.commit()

    # Sincronización con Shopify
    shopify_msg = ""
    try:
        from ..shopify_api import sync_customer_to_shopify
        ok_sh, sh_id, err_sh = sync_customer_to_shopify({
            'id': customer_id,
            'nombre': nombre,
            'email': email,
            'telefono': telefono
        }, db=db)
        if ok_sh:
            shopify_msg = " y sincronizado con Shopify exitosamente."
    except Exception as e:
        shopify_msg = f" (Aviso Shopify: {e})."

    flash(f"Usuario '{nombre}' creado exitosamente{shopify_msg}", 'success')
    return redirect(url_for('admin.admin_usuarios'))


@admin_bp.route('/admin/usuarios/<int:customer_id>/editar', methods=['POST'])
@require_login
def admin_usuario_editar(customer_id):
    nombre = (request.form.get('nombre') or '').strip()
    email = (request.form.get('email') or '').strip().lower()
    telefono = (request.form.get('telefono') or '').strip()
    password = (request.form.get('password') or '').strip()
    puntos_raw = request.form.get('puntos')

    if not nombre or not email:
        flash('Nombre y correo electrónico son obligatorios.', 'error')
        return redirect(url_for('admin.admin_usuarios'))

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute('SELECT * FROM customers WHERE id = %s', [customer_id])
    customer = cur.fetchone()
    if not customer:
        flash('Usuario no encontrado.', 'error')
        return redirect(url_for('admin.admin_usuarios'))

    cur.execute('SELECT id FROM customers WHERE email = %s AND id != %s', [email, customer_id])
    if cur.fetchone():
        flash(f"El correo '{email}' ya pertenece a otro usuario.", 'error')
        return redirect(url_for('admin.admin_usuarios'))

    updates = ['nombre = %s', 'email = %s', 'telefono = %s']
    params = [nombre, email, telefono]

    if password:
        from werkzeug.security import generate_password_hash
        updates.append('password_hash = %s')
        params.append(generate_password_hash(password))

    if puntos_raw is not None and puntos_raw != '':
        try:
            nuevos_puntos = max(0, int(puntos_raw))
            diff_puntos = nuevos_puntos - int(customer.get('puntos', 0))
            if diff_puntos != 0:
                updates.append('puntos = %s')
                params.append(nuevos_puntos)
                motivo_admin = f"Ajuste manual de administración ({'+' if diff_puntos > 0 else ''}{diff_puntos} pts)"
                cur.execute('''
                    INSERT INTO customer_points_history (customer_id, puntos, motivo)
                    VALUES (%s, %s, %s)
                ''', [customer_id, diff_puntos, motivo_admin])
        except (ValueError, TypeError):
            pass

    params.append(customer_id)
    cur.execute(f"UPDATE customers SET {', '.join(updates)} WHERE id = %s", params)

    if hasattr(db, 'commit'):
        db.commit()

    # Re-sincronizar Shopify
    try:
        from ..shopify_api import sync_customer_to_shopify
        sync_customer_to_shopify({
            'id': customer_id,
            'nombre': nombre,
            'email': email,
            'telefono': telefono
        }, db=db)
    except Exception:
        pass

    flash(f"Usuario '{nombre}' actualizado exitosamente.", 'success')
    return redirect(url_for('admin.admin_usuarios'))


@admin_bp.route('/admin/usuarios/<int:customer_id>/toggle', methods=['POST'])
@require_login
def admin_usuario_toggle(customer_id):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute('SELECT id, nombre, is_active FROM customers WHERE id = %s', [customer_id])
    customer = cur.fetchone()
    if not customer:
        flash('Usuario no encontrado.', 'error')
        return redirect(url_for('admin.admin_usuarios'))

    new_status = 0 if customer['is_active'] else 1
    cur.execute('UPDATE customers SET is_active = %s WHERE id = %s', [new_status, customer_id])
    if hasattr(db, 'commit'):
        db.commit()

    flash(f"Usuario '{customer['nombre']}' {'activado' if new_status else 'suspendido'}.", 'success')
    return redirect(url_for('admin.admin_usuarios'))


@admin_bp.route('/admin/usuarios/<int:customer_id>/eliminar', methods=['POST'])
@require_login
def admin_usuario_eliminar(customer_id):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute('SELECT id, nombre, email FROM customers WHERE id = %s', [customer_id])
    customer = cur.fetchone()
    if not customer:
        flash('Usuario no encontrado.', 'error')
        return redirect(url_for('admin.admin_usuarios'))

    try:
        from ..mailer import send_account_deleted_email
        send_account_deleted_email(customer['email'], customer['nombre'])
    except Exception:
        pass

    cur.execute('DELETE FROM customers WHERE id = %s', [customer_id])
    if hasattr(db, 'commit'):
        db.commit()

    flash(f"Usuario '{customer['nombre']}' eliminado permanentemente.", 'success')
    return redirect(url_for('admin.admin_usuarios'))


@admin_bp.route('/admin/usuarios/<int:customer_id>/detalle')
@require_login
def admin_usuario_detalle(customer_id):
    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute('SELECT * FROM customers WHERE id = %s', [customer_id])
    customer = cur.fetchone()
    if not customer:
        return jsonify({'ok': False, 'error': 'Usuario no encontrado'}), 404

    # Compras / Pedidos del cliente
    cur.execute('''
        SELECT id, link_token, total, descuento, cupon_codigo, puntos_ganados, status, created_at, items_json
        FROM orders
        WHERE customer_id = %s
        ORDER BY created_at DESC
    ''', [customer_id])
    orders = cur.fetchall()

    for o in orders:
        if o.get('created_at'):
            o['fecha_fmt'] = o['created_at'].strftime('%d/%m/%Y %H:%M')
        if o.get('items_json'):
            try:
                parsed_items = json.loads(o['items_json'])
            except Exception:
                parsed_items = []
        else:
            parsed_items = []
        o['items'] = parsed_items
        o['items_list'] = parsed_items

    # Historial de Puntos
    cur.execute('''
        SELECT id, puntos, motivo, created_at
        FROM customer_points_history
        WHERE customer_id = %s
        ORDER BY created_at DESC
        LIMIT 50
    ''', [customer_id])
    points_history = cur.fetchall()
    for ph in points_history:
        if ph.get('created_at'):
            ph['fecha_fmt'] = ph['created_at'].strftime('%d/%m/%Y %H:%M')

    # Canjes de Premios
    cur.execute('''
        SELECT crr.*, r.nombre AS reward_nombre
        FROM customer_reward_redemptions crr
        JOIN rewards r ON r.id = crr.reward_id
        WHERE crr.customer_id = %s
        ORDER BY crr.created_at DESC
    ''', [customer_id])
    redemptions = cur.fetchall()
    for r in redemptions:
        if r.get('created_at'):
            r['fecha_fmt'] = r['created_at'].strftime('%d/%m/%Y %H:%M')

    session_data = {
        'canal': 'Tienda Web Oficial (siyash.cl)',
        'cookie_name': 'session (Flask Secure Cookie / HMAC SHA256)',
        'shopify_linked': bool(customer.get('shopify_customer_id')),
        'shopify_customer_id': customer.get('shopify_customer_id') or 'Pendiente de sincronizar',
        'auth_method': 'Email + Contraseña (PBKDF2 SHA256)',
        'fecha_registro': customer['created_at'].strftime('%d/%m/%Y %H:%M') if customer.get('created_at') else 'N/A'
    }

    return jsonify({
        'ok': True,
        'customer': {
            'id': customer['id'],
            'nombre': customer['nombre'],
            'email': customer['email'],
            'telefono': customer.get('telefono') or '',
            'puntos': customer.get('puntos', 0),
            'is_active': customer.get('is_active', 1),
            'shopify_customer_id': customer.get('shopify_customer_id') or '',
            'created_at': customer['created_at'].strftime('%d/%m/%Y %H:%M') if customer.get('created_at') else ''
        },
        'orders': orders,
        'points_history': points_history,
        'redemptions': redemptions,
        'session_data': session_data
    })


@admin_bp.route('/admin/usuarios/<int:customer_id>/sync-shopify', methods=['POST'])
@require_login
def admin_usuario_sync_shopify(customer_id):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute('SELECT * FROM customers WHERE id = %s', [customer_id])
    customer = cur.fetchone()
    if not customer:
        flash('Usuario no encontrado.', 'error')
        return redirect(url_for('admin.admin_usuarios'))

    try:
        from ..shopify_api import sync_customer_to_shopify
        ok, sh_id, err = sync_customer_to_shopify(customer, db=db)
        if ok:
            flash(f"Usuario '{customer['nombre']}' sincronizado con Shopify exitosamente (ID: {sh_id}).", 'success')
        else:
            flash(f"Error al sincronizar con Shopify: {err}", 'error')
    except Exception as e:
        flash(f"Excepción al conectar con Shopify: {e}", 'error')

    return redirect(url_for('admin.admin_usuarios'))


# -------------------------------------------------------------
# LOGÍSTICA, PICKING Y CONTROL DE DESPACHOS
# -------------------------------------------------------------

COURIER_TRACKING_URLS = {
    'Starken': 'https://www.starken.cl/seguimiento?codigo={tracking}',
    'Chilexpress': 'https://www.chilexpress.cl/seguimiento?orden={tracking}',
    'Blue Express': 'https://www.bluex.cl/seguimiento/?tracking={tracking}',
    'Correos de Chile': 'https://www.correos.cl/seguimiento-en-linea?envio={tracking}',
    'Delivery Propio': ''
}


@admin_bp.route('/admin/envios')
@require_login
def admin_envios():
    db = get_db()
    cur = db.cursor(dictionary=True)

    stage_filter = (request.args.get('stage') or 'todos').strip().lower()
    q = (request.args.get('q') or '').strip()

    sql = '''
        SELECT o.*,
               COALESCE(c.nombre, o.nombre) AS cliente_nombre,
               COALESCE(c.email, o.email) AS cliente_email,
               COALESCE(c.telefono, o.telefono) AS cliente_telefono
        FROM orders o
        LEFT JOIN customers c ON c.id = o.customer_id
        WHERE 1=1
    '''
    params = []

    if stage_filter in ('recibido', 'armado', 'empaquetado', 'despachado', 'entregado'):
        sql += " AND o.dispatch_stage = %s"
        params.append(stage_filter)

    if q:
        sql += " AND (o.id = %s OR o.nombre LIKE %s OR o.shipping_tracking LIKE %s OR o.barcode_val LIKE %s)"
        try:
            q_id = int(q.replace('SIY-ORD-', '').replace('SIY-', ''))
        except ValueError:
            q_id = -1
        like_q = f"%{q}%"
        params.extend([q_id, like_q, like_q, like_q])

    sql += " ORDER BY o.created_at DESC"

    cur.execute(sql, params)
    orders = cur.fetchall()

    for o in orders:
        if not o.get('barcode_val'):
            o['barcode_val'] = f"SIY-ORD-{o['id']:06d}"
        if not o.get('dispatch_stage'):
            o['dispatch_stage'] = 'recibido'

        if o.get('items_json'):
            try:
                parsed_items = json.loads(o['items_json'])
            except Exception:
                parsed_items = []
        else:
            parsed_items = []
        o['items'] = parsed_items
        o['items_list'] = parsed_items

        courier = o.get('shipping_courier')
        tracking = o.get('shipping_tracking')
        if courier and tracking and courier in COURIER_TRACKING_URLS:
            tmpl = COURIER_TRACKING_URLS[courier]
            o['tracking_url'] = tmpl.format(tracking=tracking) if tmpl else ''
        else:
            o['tracking_url'] = ''

    cur.execute('''
        SELECT 
            SUM(CASE WHEN dispatch_stage = 'recibido' OR dispatch_stage IS NULL THEN 1 ELSE 0 END) AS c_recibido,
            SUM(CASE WHEN dispatch_stage = 'armado' THEN 1 ELSE 0 END) AS c_armado,
            SUM(CASE WHEN dispatch_stage = 'empaquetado' THEN 1 ELSE 0 END) AS c_empaquetado,
            SUM(CASE WHEN dispatch_stage = 'despachado' THEN 1 ELSE 0 END) AS c_despachado,
            SUM(CASE WHEN dispatch_stage = 'entregado' THEN 1 ELSE 0 END) AS c_entregado,
            COUNT(*) AS c_total
        FROM orders
    ''')
    counts_row = cur.fetchone() or {}

    stage_counts = {
        'total': int(counts_row.get('c_total') or 0),
        'recibido': int(counts_row.get('c_recibido') or 0),
        'armado': int(counts_row.get('c_armado') or 0),
        'empaquetado': int(counts_row.get('c_empaquetado') or 0),
        'despachado': int(counts_row.get('c_despachado') or 0),
        'entregado': int(counts_row.get('c_entregado') or 0),
    }

    return render_template(
        'admin_envios.html',
        orders=orders,
        stage_counts=stage_counts,
        stage_filter=stage_filter,
        q=q
    )


@admin_bp.route('/admin/envios/<int:order_id>/etapa', methods=['POST'])
@require_login
def admin_envio_cambiar_etapa(order_id):
    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute('''
        SELECT o.*, 
               COALESCE(c.nombre, o.nombre) AS cliente_nombre,
               COALESCE(c.email, o.email) AS cliente_email
        FROM orders o
        LEFT JOIN customers c ON c.id = o.customer_id
        WHERE o.id = %s
    ''', [order_id])
    order = cur.fetchone()

    if not order:
        flash('Pedido no encontrado.', 'error')
        return redirect(url_for('admin.admin_envios'))

    nueva_etapa = (request.form.get('nueva_etapa') or '').strip().lower()
    courier = (request.form.get('courier') or order.get('shipping_courier') or '').strip()
    tracking = (request.form.get('tracking') or order.get('shipping_tracking') or '').strip()
    direccion = (request.form.get('direccion') or order.get('shipping_address') or '').strip()
    comuna = (request.form.get('comuna') or order.get('shipping_commune') or '').strip()

    valid_stages = ('recibido', 'armado', 'empaquetado', 'despachado', 'entregado')
    if nueva_etapa not in valid_stages:
        flash('Etapa de despacho no válida.', 'error')
        return redirect(url_for('admin.admin_envios'))

    updates = [
        'dispatch_stage = %s',
        'shipping_courier = %s',
        'shipping_tracking = %s',
        'shipping_address = %s',
        'shipping_commune = %s'
    ]
    params = [nueva_etapa, courier or None, tracking or None, direccion or None, comuna or None]

    if nueva_etapa == 'entregado':
        updates.append("status = 'completado'")

    params.append(order_id)
    cur.execute(f"UPDATE orders SET {', '.join(updates)} WHERE id = %s", params)

    if hasattr(db, 'commit'):
        db.commit()

    recipient_email = order.get('cliente_email') or order.get('email')
    nombre_cliente = order.get('cliente_nombre') or order.get('nombre') or 'Cliente Siyash'

    if nueva_etapa == 'despachado' and recipient_email:
        tracking_url = ''
        if courier in COURIER_TRACKING_URLS and tracking:
            tmpl = COURIER_TRACKING_URLS[courier]
            tracking_url = tmpl.format(tracking=tracking) if tmpl else ''

        try:
            from ..mailer import send_order_shipped_email
            send_order_shipped_email(recipient_email, {
                'id': order_id,
                'nombre': nombre_cliente,
                'courier': courier or 'Courier Express',
                'tracking_number': tracking or 'En asignación',
                'tracking_url': tracking_url,
                'direccion_envio': f"{direccion}, {comuna}".strip(', ') if direccion else 'Domicilio del cliente'
            })
            flash(f"Pedido #{order_id} marcado como 'Despachado' y correo de seguimiento enviado a {recipient_email}.", 'success')
        except Exception as e:
            flash(f"Pedido #{order_id} despachado. Aviso de correo: {e}", 'warning')

    elif nueva_etapa == 'entregado' and recipient_email:
        try:
            from ..mailer import send_order_delivered_email
            send_order_delivered_email(recipient_email, {
                'id': order_id,
                'nombre': nombre_cliente,
                'fecha_entrega': datetime.now().strftime('%d/%m/%Y %H:%M')
            })
            flash(f"Pedido #{order_id} marcado como 'Entregado' y correo de confirmación enviado a {recipient_email}.", 'success')
        except Exception as e:
            flash(f"Pedido #{order_id} entregado. Aviso de correo: {e}", 'warning')
    else:
        flash(f"Pedido #{order_id} actualizado a etapa '{nueva_etapa.capitalize()}'.", 'success')

    return redirect(url_for('admin.admin_envios', stage=nueva_etapa))


@admin_bp.route('/admin/envios/<int:order_id>/subir-foto', methods=['POST'])
@require_login
def admin_envio_subir_foto(order_id):
    if 'foto' not in request.files:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'ok': False, 'error': 'No se adjuntó archivo de fotografía.'}), 400
        flash('No se seleccionó ningún archivo de fotografía.', 'error')
        return redirect(url_for('admin.admin_envios'))

    file = request.files['foto']
    if not file or not file.filename:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'ok': False, 'error': 'Archivo de fotografía vacío.'}), 400
        flash('Archivo de fotografía vacío.', 'error')
        return redirect(url_for('admin.admin_envios'))

    import secrets
    upload_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'static', 'uploads', 'despachos')
    os.makedirs(upload_dir, exist_ok=True)

    ext = os.path.splitext(file.filename)[1].lower() or '.jpg'
    filename = f"orden_{order_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}{ext}"
    dest_path = os.path.join(upload_dir, filename)
    file.save(dest_path)

    relative_url = f"/static/uploads/despachos/{filename}"

    db = get_db()
    cur = db.cursor()
    cur.execute('''
        UPDATE orders 
        SET package_photo = %s,
            dispatch_stage = CASE 
                WHEN dispatch_stage IN ('recibido', 'armado') THEN 'empaquetado' 
                ELSE dispatch_stage 
            END
        WHERE id = %s
    ''', [relative_url, order_id])

    if hasattr(db, 'commit'):
        db.commit()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'ok': True, 'photo_url': relative_url, 'message': 'Fotografía validada exitosamente.'})

    flash(f"Fotografía de validación guardada exitosamente para el Pedido #{order_id}.", 'success')
    return redirect(url_for('admin.admin_envios'))


@admin_bp.route('/admin/envios/<int:order_id>/etiqueta')
@require_login
def admin_envio_etiqueta(order_id):
    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute('''
        SELECT o.*,
               COALESCE(c.nombre, o.nombre) AS cliente_nombre,
               COALESCE(c.telefono, o.telefono) AS cliente_telefono,
               COALESCE(c.email, o.email) AS cliente_email
        FROM orders o
        LEFT JOIN customers c ON c.id = o.customer_id
        WHERE o.id = %s
    ''', [order_id])
    order = cur.fetchone()

    if not order:
        return "Pedido no encontrado", 404

    if not order.get('barcode_val'):
        order['barcode_val'] = f"SIY-ORD-{order['id']:06d}"

    if order.get('items_json'):
        try:
            parsed_items = json.loads(order['items_json'])
        except Exception:
            parsed_items = []
    else:
        parsed_items = []
    order['items'] = parsed_items
    order['items_list'] = parsed_items

    return render_template('admin_etiqueta_despacho.html', order=order)


@admin_bp.route('/api/envios/scan/<path:barcode_query>')
@require_login
def api_envios_scan(barcode_query):
    raw = barcode_query.strip().upper()
    order_id = None

    if raw.startswith('SIY-ORD-'):
        try:
            order_id = int(raw.replace('SIY-ORD-', ''))
        except ValueError:
            pass
    elif raw.startswith('SIY-'):
        try:
            order_id = int(raw.replace('SIY-', ''))
        except ValueError:
            pass
    else:
        try:
            order_id = int(raw)
        except ValueError:
            pass

    db = get_db()
    cur = db.cursor(dictionary=True)

    if order_id:
        cur.execute('SELECT * FROM orders WHERE id = %s', [order_id])
    else:
        cur.execute('SELECT * FROM orders WHERE shipping_tracking = %s OR barcode_val = %s', [raw, raw])

    order = cur.fetchone()
    if not order:
        return jsonify({'ok': False, 'error': f"No se encontró ningún pedido para el código '{raw}'"}), 404

    return jsonify({
        'ok': True,
        'order': {
            'id': order['id'],
            'nombre': order['nombre'],
            'telefono': order.get('telefono'),
            'total': order['total'],
            'dispatch_stage': order.get('dispatch_stage') or 'recibido',
            'package_photo': order.get('package_photo'),
            'courier': order.get('shipping_courier'),
            'tracking': order.get('shipping_tracking'),
            'barcode': order.get('barcode_val') or f"SIY-ORD-{order['id']:06d}"
        }
    })
