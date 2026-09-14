import json
import urllib.request
import urllib.error
import re
from difflib import SequenceMatcher
from flask import Blueprint, flash, redirect, url_for

from ..db import get_db, rebuild_storefront_cache
from ..utils import require_login, _extract_aromas

sync_bp = Blueprint('sync', __name__)

def _sync_shopify(db, table, base_url):
    added = 0
    error = None
    page = 1
    batch = []
    sql = f'''INSERT INTO {table} (sku, nombre, precio_retail, precio_ref, imagen, url, body_html)
              VALUES (%s, %s, %s, %s, %s, %s, %s)
              ON DUPLICATE KEY UPDATE nombre=VALUES(nombre),
              precio_retail=VALUES(precio_retail), precio_ref=VALUES(precio_ref),
              imagen=VALUES(imagen), url=VALUES(url), body_html=VALUES(body_html),
              last_synced=CURRENT_TIMESTAMP'''

    def _flush():
        nonlocal batch
        if batch:
            cur = db.cursor()
            cur.executemany(sql, batch)
            cur.close()
            batch = []

    while True:
        url = f'{base_url}/products.json?limit=250&page={page}'
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'es-CL,es;q=0.9',
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error = f'HTTP {e.code}: {e.reason}'
            break
        except urllib.error.URLError as e:
            error = f'Error de conexión: {e.reason}'
            break
        except Exception as e:
            error = f'Error inesperado: {e}'
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
                url_slug = f'{base_url}/products/{p.get("handle", "")}'
                imagen = (p.get('images') or [{}])[0].get('src', '') if p.get('images') else ''
                body_html = p.get('body_html', '')
                batch.append([sku, nombre, precio, compare_at, imagen, url_slug, body_html])
                added += 1
                if len(batch) >= 100:
                    _flush()
        page += 1
    _flush()
    db.commit()
    return (added, error)

def _normalize(n):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', '', n.lower().replace('á','a').replace('é','e').replace('í','i').replace('ó','o').replace('ú','u'))).strip()

def _match_by_name(db, match_table, source_table, sku_col):
    autocommit = db.autocommit
    db.autocommit = False
    try:
        cur = db.cursor(dictionary=True)
        cur.execute(f'DELETE FROM {match_table}')
        cur.execute('SELECT sku, nombre, linea FROM products')
        wholesale = cur.fetchall()
        cur.execute(f'SELECT sku, nombre FROM {source_table}')
        source = cur.fetchall()

        source_by_token = {}
        for s in source:
            sn = _normalize(s['nombre'])
            tokens = sn.split()
            if tokens:
                k1 = tokens[0]
                source_by_token.setdefault(k1, []).append((sn, s['sku']))
                if len(tokens) >= 2:
                    k2 = f'{tokens[0]} {tokens[1]}'
                    source_by_token.setdefault(k2, []).append((sn, s['sku']))

        matched = 0
        for w in wholesale:
            w_norm = _normalize(w['nombre'])
            w_brand = _normalize(w['linea'] or '')
            tokens = w_norm.split()
            w_key1 = tokens[0] if tokens else ''
            w_key2 = f'{tokens[0]} {tokens[1]}' if len(tokens) >= 2 else w_key1

            seen = set()
            candidates = []
            for key in [w_brand, w_key1, w_key2]:
                for sn, ssku in source_by_token.get(key, []):
                    if ssku not in seen:
                        seen.add(ssku)
                        candidates.append((sn, ssku))

            if w_brand and len(w_brand) > 3:
                for s in source:
                    if s['sku'] in seen:
                        continue
                    sn = _normalize(s['nombre'])
                    if w_brand in sn:
                        seen.add(s['sku'])
                        candidates.append((sn, ssku))
                        if len(candidates) > 200:
                            break

            if not candidates:
                continue

            best_score = 0
            best_sku = None
            for sn, ssku in candidates:
                if abs(len(w_norm) - len(sn)) > len(w_norm) * 0.6:
                    continue
                score = SequenceMatcher(None, w_norm, sn).ratio()
                if score > best_score:
                    best_score = score
                    best_sku = ssku

            if best_score >= 0.70:
                cur.execute(f'INSERT INTO {match_table} (sku_wholesale, {sku_col}, confidence) VALUES (%s, %s, %s)',
                       [w['sku'], best_sku, round(best_score, 3)])
                matched += 1

        db.commit()
        return matched
    except Exception:
        db.rollback()
        raise
    finally:
        db.autocommit = autocommit

@sync_bp.route('/sync-cosmetic')
@require_login
def sync_cosmetic():
    db = get_db()
    try:
        added, error = _sync_shopify(db, 'cosmetic_products', 'https://cosmetic.cl')
        extracted = 0
        if not error:
            extracted = _extract_aromas(db, 'cosmetic_products')
            # OPTIMIZATION: rebuild storefront cache to push new images/aromas to products table
            rebuild_storefront_cache(db)
    finally:
        db.close()
    if error:
        flash(f'Error sync cosmetic.cl: {error}', 'error')
    elif added == 0:
        flash('No se encontraron productos en cosmetic.cl', 'warning')
    else:
        flash(f'Sincronizados {added} productos, {extracted} con aromas de cosmetic.cl', 'success')
    from .admin import admin_bp # fallback
    return redirect(url_for('admin.retail'))

@sync_bp.route('/sync-silk')
@require_login
def sync_silk():
    db = get_db()
    try:
        added, error = _sync_shopify(db, 'silk_products', 'https://silkperfumes.cl')
        if not error:
            _match_by_name(db, 'silk_matches', 'silk_products', 'sku_silk')
            rebuild_storefront_cache(db)
    finally:
        db.close()
    if error:
        flash(f'Error sync silkperfumes.cl: {error}', 'error')
    elif added == 0:
        flash('No se encontraron productos en silkperfumes.cl', 'warning')
    else:
        flash(f'Sincronizados {added} productos de silkperfumes.cl', 'success')
    return redirect(url_for('admin.retail'))

@sync_bp.route('/sync-multimarca')
@require_login
def sync_multimarca():
    db = get_db()
    try:
        added, error = _sync_shopify(db, 'multimarca_products', 'https://multimarcasperfumes.cl')
        matched = 0
        extracted = 0
        if not error:
            matched = _match_by_name(db, 'multimarca_matches', 'multimarca_products', 'sku_mm')
            extracted = _extract_aromas(db, 'multimarca_products')
            rebuild_storefront_cache(db)
    finally:
        db.close()
    if error:
        flash(f'Error sync multimarcasperfumes.cl: {error}', 'error')
    elif added == 0:
        flash('No se encontraron productos en multimarcasperfumes.cl', 'warning')
    else:
        flash(f'Sincronizados {added} productos, {matched} matcheados, {extracted} aromas de multimarcasperfumes.cl', 'success')
    return redirect(url_for('admin.retail'))
