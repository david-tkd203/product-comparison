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
