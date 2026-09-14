import os
from flask import request, render_template, redirect, url_for, flash, session, make_response
from werkzeug.security import check_password_hash
import io, csv
from .admin import admin_bp
from ..db import get_db
from ..utils import require_login

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute('SELECT * FROM users WHERE username = %s', [username])
        user = cur.fetchone()
        db.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            flash(f'Bienvenido, {user["username"]}', 'success')
            return redirect(url_for('admin.admin_productos'))
        else:
            flash('Usuario o contraseña incorrectos', 'error')
    return render_template('login.html')

@admin_bp.route('/logout')
def logout():
    session.clear()
    flash('Sesión cerrada', 'success')
    return redirect(url_for('admin.login'))

@admin_bp.route('/estudio')
@require_login
def estudio():
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute('SELECT COUNT(*) as cnt FROM products')
    total_wholesale = cur.fetchone()['cnt']
    cur.execute('SELECT COUNT(*) as cnt FROM cosmetic_products')
    total_cosmetic = cur.fetchone()['cnt']
    cur.execute('SELECT COUNT(*) as cnt FROM silk_products')
    total_silk = cur.fetchone()['cnt']
    cur.execute('SELECT COUNT(*) as cnt FROM multimarca_products')
    total_mm = cur.fetchone()['cnt']
    
    cur.execute('SELECT COUNT(*) as cnt FROM products p JOIN cosmetic_products cp ON p.sku = cp.sku')
    matched_cosmetic = cur.fetchone()['cnt']
    cur.execute('SELECT COUNT(*) as cnt FROM silk_matches')
    matched_silk = cur.fetchone()['cnt']
    cur.execute('SELECT COUNT(*) as cnt FROM multimarca_matches')
    matched_mm = cur.fetchone()['cnt']
    
    match_rate_cosmetic = round(matched_cosmetic * 100.0 / total_wholesale, 1) if total_wholesale else 0
    match_rate_silk = round(matched_silk * 100.0 / total_wholesale, 1) if total_wholesale else 0
    match_rate_mm = round(matched_mm * 100.0 / total_wholesale, 1) if total_wholesale else 0

    # Optimized: Removed JOIN prices
    cur.execute('''
        SELECT ROUND(AVG(cp.precio_retail - p.precio_mayorista)) as avg_diff,
               ROUND(AVG((cp.precio_retail - p.precio_mayorista) * 100.0 / cp.precio_retail), 1) as avg_margin
        FROM products p
        JOIN cosmetic_products cp ON p.sku = cp.sku
        WHERE cp.precio_retail > 0 AND p.precio_mayorista > 0
    ''')
    avg_gap = cur.fetchone()

    cur.execute('''
        SELECT ROUND(AVG(mp.precio_retail - p.precio_mayorista)) as avg_diff,
               ROUND(AVG((mp.precio_retail - p.precio_mayorista) * 100.0 / mp.precio_retail), 1) as avg_margin
        FROM products p
        JOIN multimarca_matches mm ON p.sku = mm.sku_wholesale
        JOIN multimarca_products mp ON mm.sku_mm = mp.sku
        WHERE mp.precio_retail > 0 AND p.precio_mayorista > 0
    ''')
    avg_gap_mm = cur.fetchone()

    cur.execute('''
        SELECT p.linea, COUNT(*) as n,
               ROUND(AVG(p.precio_mayorista)) as avg_costo,
               ROUND(AVG(cp.precio_retail)) as avg_retail,
               ROUND(AVG((cp.precio_retail - p.precio_mayorista) * 100.0 / cp.precio_retail), 1) as avg_margin
        FROM products p
        JOIN cosmetic_products cp ON p.sku = cp.sku
        WHERE cp.precio_retail > 0 AND p.precio_mayorista > 0
        GROUP BY p.linea HAVING n >= 3
        ORDER BY avg_margin DESC LIMIT 20
    ''')
    brand_stats = cur.fetchall()

    cur.execute('''
        SELECT p.linea, COUNT(*) as n,
               ROUND(AVG(p.precio_mayorista)) as avg_costo,
               ROUND(AVG(mp.precio_retail)) as avg_retail,
               ROUND(AVG((mp.precio_retail - p.precio_mayorista) * 100.0 / mp.precio_retail), 1) as avg_margin
        FROM products p
        JOIN multimarca_matches mm ON p.sku = mm.sku_wholesale
        JOIN multimarca_products mp ON mm.sku_mm = mp.sku
        WHERE mp.precio_retail > 0 AND p.precio_mayorista > 0
        GROUP BY p.linea HAVING n >= 3
        ORDER BY avg_margin DESC LIMIT 20
    ''')
    brand_stats_mm = cur.fetchall()

    cur.execute('''
        SELECT p.sku, p.nombre, p.linea, p.precio_mayorista as costo,
               cp.precio_retail, cp.url, cp.imagen,
               (cp.precio_retail - p.precio_mayorista) as diff,
               ROUND((cp.precio_retail - p.precio_mayorista) * 100.0 / cp.precio_retail, 1) as margen
        FROM products p
        JOIN cosmetic_products cp ON p.sku = cp.sku
        WHERE cp.precio_retail > 0 AND p.precio_mayorista > 0
        ORDER BY margen DESC LIMIT 100
    ''')
    opportunities = cur.fetchall()
    
    cur.close()
    db.close()
    
    return render_template('estudio.html',
                           total_wholesale=total_wholesale,
                           total_cosmetic=total_cosmetic,
                           total_silk=total_silk,
                           total_mm=total_mm,
                           match_rate_cosmetic=match_rate_cosmetic,
                           match_rate_silk=match_rate_silk,
                           match_rate_mm=match_rate_mm,
                           avg_gap=avg_gap,
                           avg_gap_mm=avg_gap_mm,
                           brand_stats=brand_stats,
                           brand_stats_mm=brand_stats_mm,
                           opportunities=opportunities)
