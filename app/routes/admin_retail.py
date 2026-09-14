import csv, io
from flask import request, render_template, redirect, url_for, flash, make_response
from .admin import admin_bp
from ..db import get_db, _get_margen
from ..utils import require_login

@admin_bp.route('/retail')
@require_login
def retail():
    query = request.args.get('q', '').strip()
    linea = request.args.get('linea', '').strip()
    sort = request.args.get('sort', 'diff_cosmetic_desc').strip()
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = 50

    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute('SELECT DISTINCT linea FROM products ORDER BY linea')
    lineas = [r['linea'] for r in cur.fetchall()]
    
    cur.execute('SELECT MAX(import_date) as d FROM imports')
    latest = cur.fetchone()['d']
    
    cur.execute('SELECT MAX(last_synced) as d FROM cosmetic_products')
    synced = cur.fetchone()['d']
    
    margen_pct = _get_margen(db)
    margen_obj = 1 + margen_pct / 100.0

    # Optimized: Removed JOIN prices
    sql = '''SELECT p.sku, p.nombre, p.linea, p.genero, p.formato,
                    p.precio_mayorista AS precio,
                    cp.precio_retail as precio_cosmetic, cp.precio_ref as ref_cosmetic,
                    cp.imagen as img_cosmetic, cp.url as url_cosmetic,
                    sp.precio_retail as precio_silk, sp.precio_ref as ref_silk,
                    sp.imagen as img_silk, sp.url as url_silk,
                    mp.precio_retail as precio_mm, mp.precio_ref as ref_mm,
                    mp.imagen as img_mm, mp.url as url_mm,
                    (cp.precio_retail - p.precio_mayorista) as diff_cosmetic,
                    (sp.precio_retail - p.precio_mayorista) as diff_silk,
                    (mp.precio_retail - p.precio_mayorista) as diff_mm,
                    CASE WHEN cp.precio_retail > 0 THEN ROUND((cp.precio_retail - p.precio_mayorista) * 100.0 / cp.precio_retail, 1) END as margen_cosmetic,
                    CASE WHEN sp.precio_retail > 0 THEN ROUND((sp.precio_retail - p.precio_mayorista) * 100.0 / sp.precio_retail, 1) END as margen_silk,
                    CASE WHEN mp.precio_retail > 0 THEN ROUND((mp.precio_retail - p.precio_mayorista) * 100.0 / mp.precio_retail, 1) END as margen_mm
             FROM products p
             LEFT JOIN cosmetic_products cp ON p.sku = cp.sku
             LEFT JOIN silk_matches sm ON p.sku = sm.sku_wholesale
             LEFT JOIN silk_products sp ON sm.sku_silk = sp.sku
             LEFT JOIN multimarca_matches mm ON p.sku = mm.sku_wholesale
             LEFT JOIN multimarca_products mp ON mm.sku_mm = mp.sku'''
    params = []
    conditions = []
    if query:
        conditions.append('(p.nombre LIKE %s OR p.sku LIKE %s OR p.linea LIKE %s)')
        like = f'%{query}%'
        params.extend([like, like, like])
    if linea:
        conditions.append('p.linea = %s')
        params.append(linea)
    if conditions:
        sql += ' WHERE ' + ' AND '.join(conditions)

    sort_map = {
        'diff_cosmetic_desc': '(cp.precio_retail - p.precio_mayorista) DESC',
        'diff_silk_desc': '(sp.precio_retail - p.precio_mayorista) DESC',
        'margen_cosmetic': 'CASE WHEN cp.precio_retail > 0 THEN (cp.precio_retail - p.precio_mayorista) * 100.0 / cp.precio_retail ELSE 0 END DESC',
        'margen_silk': 'CASE WHEN sp.precio_retail > 0 THEN (sp.precio_retail - p.precio_mayorista) * 100.0 / sp.precio_retail ELSE 0 END DESC',
        'linea': 'p.linea, p.nombre',
    }
    sql += f' ORDER BY {sort_map.get(sort, sort_map["diff_cosmetic_desc"])}'

    cur.execute(f'SELECT COUNT(*) AS cnt FROM ({sql}) AS t', params)
    total = cur.fetchone()['cnt']
    offset = (page - 1) * per_page
    cur.execute(f'{sql} LIMIT %s OFFSET %s', params + [per_page, offset])
    products = cur.fetchall()
    total_pages = (total + per_page - 1) // per_page if total else 1
    
    con_cosmetic = sum(1 for p in products if p['precio_cosmetic'] and p['precio_cosmetic'] > 0)
    con_silk = sum(1 for p in products if p['precio_silk'] and p['precio_silk'] > 0)
    con_mm = sum(1 for p in products if p['precio_mm'] and p['precio_mm'] > 0)
    
    cur.close()
    db.close()
    
    return render_template('retail.html', products=products, query=query,
                          linea=linea, lineas=lineas, latest=latest,
                          synced=synced, total=total,
                          con_cosmetic=con_cosmetic, con_silk=con_silk, con_mm=con_mm,
                          sort=sort, margen_obj=margen_obj, margen_pct=margen_pct,
                          page=page, per_page=per_page, total_pages=total_pages)

@admin_bp.route('/retail/export')
@require_login
def retail_export():
    skus = request.args.get('skus', '').split(',')
    if not skus or skus == ['']:
        flash('Seleccioná al menos un producto', 'error')
        return redirect(url_for('admin.retail'))
    db = get_db()
    cur = db.cursor(dictionary=True)

    placeholders = ','.join(['%s'] * len(skus))
    cur.execute(f'''
        SELECT p.sku, p.nombre, p.linea, p.genero, p.formato,
               p.precio_mayorista as mayorista,
               cp.precio_retail as cosmetic, cp.url as url_cosmetic, cp.imagen as img_cosmetic,
               sp.precio_retail as silk, sp.url as url_silk, sp.imagen as img_silk
        FROM products p
        LEFT JOIN cosmetic_products cp ON p.sku = cp.sku
        LEFT JOIN silk_matches sm ON p.sku = sm.sku_wholesale
        LEFT JOIN silk_products sp ON sm.sku_silk = sp.sku
        WHERE p.sku IN ({placeholders})
        GROUP BY p.sku, p.nombre, p.linea, p.genero, p.formato, p.precio_mayorista,
                 cp.precio_retail, cp.url, cp.imagen, sp.precio_retail, sp.url, sp.imagen
        ORDER BY p.linea, p.nombre
    ''', skus)
    rows = cur.fetchall()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['SKU', 'Marca', 'Nombre', 'Genero', 'Formato', 'Mayorista',
                     'cosmetic.cl', 'silkperfumes.cl', 'URL cosmetic', 'URL silk',
                     'Imagen cosmetic', 'Imagen silk'])
    for r in rows:
        writer.writerow([
            r['sku'], r['linea'], r['nombre'], r['genero'], r['formato'],
            r['mayorista'], r['cosmetic'] or '', r['silk'] or '',
            r['url_cosmetic'] or '', r['url_silk'] or '',
            r['img_cosmetic'] or '', r['img_silk'] or ''
        ])
    output.seek(0)
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename=comparativa.csv'
    return resp
