"""
app/shopify_api.py — Cliente de integración directa con Shopify Admin REST API (2024-01).
Gestiona sincronización de:
1. Cupones / Reglas de descuento (PriceRules + DiscountCodes).
2. Clientes (Customers) al registrarse en Club Siyash.
"""

import os
import re
import json
import logging
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime

logger = logging.getLogger('siyash.shopify')


def get_shopify_credentials(db=None):
    """
    Obtiene el dominio de la tienda y el token de acceso para la Shopify Admin API.
    Prioriza la tabla settings, con fallback a variables de entorno.
    """
    shop = None
    token = None

    if db:
        try:
            cur = db.cursor(dictionary=True)
            cur.execute("SELECT setting_value FROM settings WHERE setting_key = 'shopify_shop'")
            r_shop = cur.fetchone()
            if r_shop and r_shop.get('setting_value'):
                shop = r_shop['setting_value']

            cur.execute("SELECT setting_value FROM settings WHERE setting_key = 'shopify_access_token'")
            r_token = cur.fetchone()
            if r_token and r_token.get('setting_value'):
                token = r_token['setting_value']
        except Exception as e:
            logger.warning(f"Error consultando credenciales Shopify en DB: {e}")

    if not shop:
        shop = os.environ.get('SHOPIFY_SHOP', 'dcw11w-0p.myshopify.com')
    if not token:
        token = os.environ.get('SHOPIFY_ACCESS_TOKEN', '')

    shop = shop.replace('https://', '').replace('http://', '').strip('/')
    if not shop:
        shop = 'dcw11w-0p.myshopify.com'

    return shop, token


def _shopify_request(shop, token, method, path, payload=None, timeout=8):
    """
    Ejecuta un request HTTP autenticado a la Admin REST API de Shopify.
    """
    url = f"https://{shop}/admin/api/2024-01/{path.lstrip('/')}"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Siyash-Perfumeria/1.0"
    }

    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = resp.getcode()
            body_bytes = resp.read()
            body_json = json.loads(body_bytes.decode('utf-8')) if body_bytes else {}
            return True, status_code, body_json, None
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode('utf-8')
            parsed = json.loads(error_body)
            error_msg = parsed.get('errors') or error_body
        except Exception:
            error_msg = error_body or str(e)
        logger.warning(f"Shopify API HTTPError {e.code} en {method} {path}: {error_msg}")
        return False, e.code, {}, error_msg
    except Exception as e:
        logger.warning(f"Shopify API Exception en {method} {path}: {e}")
        return False, 0, {}, str(e)


def sync_coupon_to_shopify(coupon, db=None):
    """
    Sincroniza un cupón de Siyash con el motor de descuentos de Shopify (PriceRule + DiscountCode).
    coupon: dict o Row con [id, codigo, tipo, valor, minimo_compra, limite_usos, valido_hasta, is_active, shopify_price_rule_id]
    Retorna: (ok: bool, price_rule_id: str, discount_code_id: str, error_msg: str)
    """
    shop, token = get_shopify_credentials(db)
    if not token:
        return False, None, None, "No se encontró el Shopify Access Token en la configuración."

    codigo = str(coupon.get('codigo') or '').strip().upper()
    if not codigo:
        return False, None, None, "Código de cupón inválido."

    tipo = coupon.get('tipo', 'porcentaje')
    try:
        valor = int(coupon.get('valor', 0))
    except (ValueError, TypeError):
        valor = 0

    try:
        minimo_compra = int(coupon.get('minimo_compra', 0) or 0)
    except (ValueError, TypeError):
        minimo_compra = 0

    limite_usos = coupon.get('limite_usos')
    valido_hasta = coupon.get('valido_hasta')
    is_active = bool(coupon.get('is_active', 1))
    price_rule_id = coupon.get('shopify_price_rule_id')
    discount_code_id = coupon.get('shopify_discount_code_id')

    # Si el cupón fue desactivado y tiene price_rule en Shopify, lo eliminamos
    if not is_active:
        if price_rule_id:
            delete_coupon_from_shopify(price_rule_id, db=db)
            if db and coupon.get('id'):
                try:
                    cur = db.cursor()
                    cur.execute(
                        "UPDATE coupons SET shopify_price_rule_id = NULL, shopify_discount_code_id = NULL WHERE id = %s",
                        [coupon['id']]
                    )
                    if hasattr(db, 'commit'):
                        db.commit()
                except Exception:
                    pass
        return True, None, None, "Cupón desactivado y removido de Shopify."

    # En Shopify los descuentos son valores negativos
    value_type = "percentage" if tipo == 'porcentaje' else "fixed_amount"
    value_str = f"-{valor}.0"

    price_rule_payload = {
        "price_rule": {
            "title": codigo,
            "target_type": "line_item",
            "target_selection": "all",
            "allocation_method": "across",
            "value_type": value_type,
            "value": value_str,
            "customer_selection": "all",
            "starts_at": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        }
    }

    if limite_usos and int(limite_usos) > 0:
        price_rule_payload["price_rule"]["usage_limit"] = int(limite_usos)

    if minimo_compra and minimo_compra > 0:
        price_rule_payload["price_rule"]["prerequisite_subtotal_range"] = {
            "greater_than_or_equal_to": str(minimo_compra)
        }

    if valido_hasta:
        if isinstance(valido_hasta, str):
            try:
                ends_dt = datetime.strptime(
                    valido_hasta[:19],
                    '%Y-%m-%d %H:%M:%S' if ' ' in valido_hasta else '%Y-%m-%dT%H:%M:%S'
                )
                price_rule_payload["price_rule"]["ends_at"] = ends_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            except Exception:
                pass
        elif hasattr(valido_hasta, 'strftime'):
            price_rule_payload["price_rule"]["ends_at"] = valido_hasta.strftime('%Y-%m-%dT%H:%M:%SZ')

    # 1. Intentar actualizar si ya existe PriceRule
    if price_rule_id:
        ok_put, status, res_put, err_put = _shopify_request(
            shop, token, 'PUT', f'price_rules/{price_rule_id}.json', price_rule_payload
        )
        if ok_put:
            return True, str(price_rule_id), str(discount_code_id or ''), None
        # Si dio 404, fue borrado en Shopify, pasamos a crear uno nuevo
        if status != 404:
            return False, None, None, f"Error actualizando regla en Shopify: {err_put}"

    # 2. Crear nuevo PriceRule
    ok_post, status, res_post, err_post = _shopify_request(
        shop, token, 'POST', 'price_rules.json', price_rule_payload
    )
    if not ok_post:
        return False, None, None, f"Error creando regla de descuento en Shopify: {err_post}"

    new_rule_id = str(res_post.get('price_rule', {}).get('id', ''))
    if not new_rule_id:
        return False, None, None, "Shopify no retornó ID de regla de precio."

    # 3. Crear el código de descuento asociado
    code_payload = {
        "discount_code": {
            "code": codigo
        }
    }
    ok_code, status_code, res_code, err_code = _shopify_request(
        shop, token, 'POST', f'price_rules/{new_rule_id}/discount_codes.json', code_payload
    )

    new_code_id = ""
    if ok_code:
        new_code_id = str(res_code.get('discount_code', {}).get('id', ''))
    else:
        # Si falló la creación del código, eliminar la price rule huérfana
        delete_coupon_from_shopify(new_rule_id, db=db)
        return False, None, None, f"Error creando código '{codigo}' en Shopify: {err_code}"

    # 4. Guardar en MySQL
    if db and coupon.get('id'):
        try:
            cur = db.cursor()
            cur.execute(
                "UPDATE coupons SET shopify_price_rule_id = %s, shopify_discount_code_id = %s WHERE id = %s",
                [new_rule_id, new_code_id, coupon['id']]
            )
            if hasattr(db, 'commit'):
                db.commit()
        except Exception as e:
            logger.warning(f"Error guardando IDs de Shopify en coupons: {e}")

    return True, new_rule_id, new_code_id, None


def delete_coupon_from_shopify(price_rule_id, db=None):
    """
    Elimina una PriceRule y sus códigos asociados en Shopify.
    """
    if not price_rule_id:
        return True, None
    shop, token = get_shopify_credentials(db)
    if not token:
        return False, "Sin token"
    ok, status, _, err = _shopify_request(shop, token, 'DELETE', f'price_rules/{price_rule_id}.json')
    return ok, err


def sync_customer_to_shopify(customer_data, db=None):
    """
    Sincroniza un cliente registrado en Siyash con la base de clientes de Shopify.
    customer_data: dict con [id, nombre, email, telefono]
    Retorna: (ok: bool, shopify_customer_id: str, error_msg: str)
    """
    shop, token = get_shopify_credentials(db)
    if not token:
        return False, None, "No se encontró el Shopify Access Token."

    email = str(customer_data.get('email') or '').strip().lower()
    if not email:
        return False, None, "Email obligatorio."

    nombre = str(customer_data.get('nombre') or '').strip()
    parts = nombre.split()
    first_name = parts[0] if parts else "Cliente"
    last_name = " ".join(parts[1:]) if len(parts) > 1 else "Siyash"

    # Formateo de teléfono chileno para cumplir validación E.164 de Shopify
    raw_phone = str(customer_data.get('telefono') or '').strip().replace(' ', '').replace('-', '')
    phone_formatted = None
    if raw_phone:
        if raw_phone.startswith('+'):
            phone_formatted = raw_phone
        elif raw_phone.startswith('56'):
            phone_formatted = f"+{raw_phone}"
        elif len(raw_phone) == 9 and raw_phone.startswith('9'):
            phone_formatted = f"+56{raw_phone}"

    customer_payload = {
        "customer": {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "verified_email": True,
            "tags": "club-siyash, web",
            "send_email_welcome": False
        }
    }
    if phone_formatted:
        customer_payload["customer"]["phone"] = phone_formatted

    # 1. Intentar POST nuevo cliente
    ok_post, status, res_post, err_post = _shopify_request(
        shop, token, 'POST', 'customers.json', customer_payload
    )

    shopify_cust_id = None
    if ok_post:
        shopify_cust_id = str(res_post.get('customer', {}).get('id', ''))
    elif status == 422:
        # Si falló por formato de teléfono, reintentar sin teléfono
        if "phone" in str(err_post).lower():
            customer_payload["customer"].pop("phone", None)
            ok_retry, status_retry, res_retry, err_retry = _shopify_request(
                shop, token, 'POST', 'customers.json', customer_payload
            )
            if ok_retry:
                shopify_cust_id = str(res_retry.get('customer', {}).get('id', ''))

        # Si el cliente ya existía en Shopify con ese email, recuperamos su ID mediante búsqueda
        if not shopify_cust_id:
            query = urllib.parse.quote(f"email:{email}")
            ok_search, _, res_search, _ = _shopify_request(
                shop, token, 'GET', f'customers/search.json?query={query}'
            )
            if ok_search and res_search.get('customers'):
                found = res_search['customers'][0]
                shopify_cust_id = str(found.get('id', ''))

    if shopify_cust_id and db and customer_data.get('id'):
        try:
            cur = db.cursor()
            cur.execute(
                "UPDATE customers SET shopify_customer_id = %s WHERE id = %s",
                [shopify_cust_id, customer_data['id']]
            )
            if hasattr(db, 'commit'):
                db.commit()
        except Exception as e:
            logger.warning(f"Error guardando shopify_customer_id: {e}")

    if shopify_cust_id:
        return True, shopify_cust_id, None
    return False, None, f"No se pudo sincronizar el cliente con Shopify: {err_post}"


def sync_product_to_shopify(p, db=None):
    """
    Sincroniza un producto individual de Siyash con Shopify:
    - Título, Vendor (marca), Product Type: 'Perfume'
    - Tags (marca, género, familia olfativa, formato)
    - Descripción enriquecida en HTML
    - Imagen (URL de alta resolución)
    - Variante con SKU, Barcode (EAN) y Precio de venta
    - Metacampos oficiales:
        * facts.ean: Código EAN-13 universal
        * custom.ean: Código EAN-13 (fallback)
        * custom.aroma_family: Familia olfativa
        * custom.concentracion: Concentración / Formato (EDP, EDT, etc.)
    Retorna: (ok: bool, shopify_product_id: str, shopify_variant_id: str, error_msg: str)
    """
    shop, token = get_shopify_credentials(db)
    if not token:
        return False, None, None, "No se encontró el Shopify Access Token en la configuración."

    sku = str(p.get('sku') or '').strip()
    if not sku:
        return False, None, None, "El producto no tiene SKU válido."

    ean = str(p.get('ean') or '').strip()
    title = str(p.get('nombre') or sku).strip()
    vendor = str(p.get('linea') or 'Siyash').strip()
    formato = str(p.get('formato') or '').strip()
    aroma_family = str(p.get('aroma_family') or '').strip()
    genero = str(p.get('genero') or '').strip()
    try:
        precio = int(p.get('precio_venta') or 0)
    except (ValueError, TypeError):
        precio = 0

    image_url = str(p.get('imagen') or '').strip()
    shopify_prod_id = p.get('shopify_product_id')
    shopify_var_id = p.get('shopify_variant_id')

    # Tags para colecciones y filtros automáticos en Shopify
    tag_list = ["perfumeria", "siyash"]
    if genero:
        tag_list.append(genero)
    if aroma_family:
        tag_list.append(aroma_family)
    if formato:
        tag_list.append(formato)
    if vendor and vendor.lower() != 'siyash':
        tag_list.append(vendor)

    # Variante nativa (el barcode nativo de Shopify es para el EAN/GTIN)
    clean_ean = re.sub(r'[^0-9]', '', ean)
    variant_data = {
        "sku": sku,
        "price": str(precio),
        "requires_shipping": True,
        "taxable": True
    }
    if clean_ean:
        variant_data["barcode"] = clean_ean
    elif ean:
        variant_data["barcode"] = ean

    if shopify_var_id:
        try:
            variant_data["id"] = int(shopify_var_id)
        except (ValueError, TypeError):
            pass

    # Metacampos homologados con las definiciones de Shopify
    metafields = []
    # facts.ean requiere estándar numérico GTIN (8, 12, 13 o 14 dígitos)
    if clean_ean and len(clean_ean) in (8, 12, 13, 14):
        metafields.append({
            "namespace": "facts",
            "key": "ean",
            "value": clean_ean,
            "type": "single_line_text_field"
        })

    if aroma_family:
        metafields.append({
            "namespace": "custom",
            "key": "aroma_family",
            "value": str(aroma_family)[:200],
            "type": "single_line_text_field"
        })
    if formato:
        metafields.append({
            "namespace": "custom",
            "key": "concentracion",
            "value": str(formato)[:200],
            "type": "single_line_text_field"
        })

    # Descripción HTML enriquecida
    desc_html = f"<p><strong>{title}</strong> original de la casa <strong>{vendor}</strong>.</p>"
    if formato:
        desc_html += f"<p><strong>Concentración / Formato:</strong> {formato}</p>"
    if aroma_family:
        desc_html += f"<p><strong>Familia Olfativa:</strong> {aroma_family}</p>"
    if p.get('aromas'):
        desc_html += f"<p><strong>Notas Aromáticas:</strong> {p['aromas']}</p>"
    desc_html += "<p><em>Garantía Siyash: 100% original, importación directa.</em></p>"

    # 1. Si ya tiene shopify_product_id, intentar actualizar vía PUT
    if shopify_prod_id:
        put_payload = {
            "product": {
                "id": int(shopify_prod_id),
                "title": title,
                "vendor": vendor,
                "product_type": "Perfume",
                "body_html": desc_html,
                "tags": ", ".join(tag_list),
                "variants": [variant_data]
            }
        }
        if metafields:
            put_payload["product"]["metafields"] = metafields

        ok_put, status, res_put, err_put = _shopify_request(
            shop, token, 'PUT', f'products/{shopify_prod_id}.json', put_payload
        )
        # Si falló por formato de metacampos, reintentar actualización sin metacampos
        if not ok_put and metafields and ("value" in str(err_put).lower() or "metafield" in str(err_put).lower()):
            put_payload["product"].pop("metafields", None)
            ok_put, status, res_put, err_put = _shopify_request(
                shop, token, 'PUT', f'products/{shopify_prod_id}.json', put_payload
            )

        if ok_put:
            res_prod = res_put.get('product', {})
            vars_list = res_prod.get('variants', [])
            ret_var_id = str(vars_list[0].get('id', '')) if vars_list else str(shopify_var_id or '')
            _save_product_shopify_ids(db, sku, str(shopify_prod_id), ret_var_id)
            return True, str(shopify_prod_id), ret_var_id, None
        
        # Si no fue 404, reportar error; si fue 404, se continúa a crear uno nuevo
        if status != 404:
            return False, None, None, f"Error actualizando en Shopify: {err_put}"

    # 2. Crear producto nuevo vía POST
    post_payload = {
        "product": {
            "title": title,
            "vendor": vendor,
            "product_type": "Perfume",
            "body_html": desc_html,
            "tags": ", ".join(tag_list),
            "status": "active",
            "published_scope": "global",
            "variants": [variant_data]
        }
    }
    if metafields:
        post_payload["product"]["metafields"] = metafields
    if image_url and image_url.startswith('http'):
        post_payload["product"]["images"] = [{"src": image_url}]

    ok_post, status, res_post, err_post = _shopify_request(
        shop, token, 'POST', 'products.json', post_payload
    )

    # Reintento de resiliencia: si un metacampo específico falló en validación, reintentar creación limpia sin metacampos
    if not ok_post and metafields and ("value" in str(err_post).lower() or "metafield" in str(err_post).lower()):
        post_payload["product"].pop("metafields", None)
        ok_post, status, res_post, err_post = _shopify_request(
            shop, token, 'POST', 'products.json', post_payload
        )

    if not ok_post:
        return False, None, None, f"Error creando producto en Shopify: {err_post}"

    res_prod = res_post.get('product', {})
    new_prod_id = str(res_prod.get('id', ''))
    vars_list = res_prod.get('variants', [])
    new_var_id = str(vars_list[0].get('id', '')) if vars_list else ''

    _save_product_shopify_ids(db, sku, new_prod_id, new_var_id)
    return True, new_prod_id, new_var_id, None


def _save_product_shopify_ids(db, sku, prod_id, var_id):
    if not db or not sku:
        return
    try:
        cur = db.cursor()
        cur.execute(
            "UPDATE products SET shopify_product_id = %s, shopify_variant_id = %s, shopify_synced_at = NOW() WHERE sku = %s",
            [prod_id, var_id, sku]
        )
        if hasattr(db, 'commit'):
            db.commit()
    except Exception as e:
        logger.warning(f"Error guardando IDs Shopify para SKU {sku}: {e}")


def sync_catalog_to_shopify(db=None, limit=100):
    """
    Sincroniza el catálogo activo en Siyash con Shopify aplicando metacampos y EAN.
    """
    import time
    if not db:
        return 0, 0, ["Base de datos no disponible"]

    cur = db.cursor(dictionary=True)
    sql = """
        SELECT sku, ean, nombre, linea, genero, formato, aroma_family,
               precio_mayorista, precio_venta, stock, imagen, aromas,
               shopify_product_id, shopify_variant_id
        FROM products
        WHERE is_active = 1 AND stock > 0 AND precio_venta > 0
        ORDER BY (shopify_product_id IS NOT NULL) ASC, linea, nombre
    """
    if limit and int(limit) > 0:
        sql += f" LIMIT {int(limit)}"

    cur.execute(sql)
    products = cur.fetchall()

    success_count = 0
    error_count = 0
    errors = []

    for p in products:
        ok, prod_id, var_id, err = sync_product_to_shopify(p, db=db)
        if ok:
            success_count += 1
        else:
            error_count += 1
            errors.append(f"{p['sku']}: {err}")
        time.sleep(0.35)

    return success_count, error_count, errors
