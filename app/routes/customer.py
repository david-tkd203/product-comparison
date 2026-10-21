import json
import re
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

from ..db import get_db

customer_bp = Blueprint('customer', __name__)


def customer_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('customer_id'):
            return redirect(url_for('customer.login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def _is_valid_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ''))


def _safe_next_url(target):
    if target and target.startswith('/') and not target.startswith('//'):
        return target
    return url_for('customer.cuenta')


@customer_bp.route('/cliente/registro', methods=['GET', 'POST'])
def registro():
    if session.get('customer_id'):
        return redirect(url_for('customer.cuenta'))

    if request.method == 'GET':
        return render_template('tienda/cliente_registro.html')

    nombre = (request.form.get('nombre') or '').strip()
    email = (request.form.get('email') or '').strip().lower()
    telefono = (request.form.get('telefono') or '').strip()
    password = (request.form.get('password') or '').strip()

    if not nombre:
        flash('Por favor ingresa tu nombre completo.', 'error')
        return render_template('tienda/cliente_registro.html', nombre=nombre, email=email, telefono=telefono), 400

    if not _is_valid_email(email):
        flash('Por favor ingresa un correo electrónico válido.', 'error')
        return render_template('tienda/cliente_registro.html', nombre=nombre, email=email, telefono=telefono), 400

    if len(password) < 6:
        flash('La contraseña debe contener al menos 6 caracteres.', 'error')
        return render_template('tienda/cliente_registro.html', nombre=nombre, email=email, telefono=telefono), 400

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("SELECT id FROM customers WHERE email = %s", [email])
    if cur.fetchone():
        flash('Ese correo electrónico ya se encuentra registrado. Inicia sesión.', 'error')
        return render_template('tienda/cliente_registro.html', nombre=nombre, email=email, telefono=telefono), 400

    password_hash = generate_password_hash(password)

    cur.execute(
        """INSERT INTO customers (email, password_hash, nombre, telefono, puntos, is_active)
           VALUES (%s, %s, %s, %s, 100, 1)""",
        [email, password_hash, nombre, telefono]
    )
    customer_id = cur.lastrowid

    cur.execute(
        """INSERT INTO customer_points_history (customer_id, puntos, motivo)
           VALUES (%s, %s, %s)""",
        [customer_id, 100, 'Bono de bienvenida']
    )

    if hasattr(db, 'commit'):
        db.commit()

    session['customer_id'] = customer_id
    session['customer_nombre'] = nombre
    session['customer_email'] = email

    flash('¡Bienvenido a Siyash! Tu cuenta ha sido creada exitosamente con 100 puntos de bienvenida.', 'success')
    return redirect(url_for('customer.cuenta'))


@customer_bp.route('/cliente/login', methods=['GET', 'POST'])
def login():
    if session.get('customer_id'):
        return redirect(url_for('customer.cuenta'))

    next_url = request.args.get('next', '')

    if request.method == 'GET':
        return render_template('tienda/cliente_login.html', next_url=next_url)

    email = (request.form.get('email') or '').strip().lower()
    password = (request.form.get('password') or '').strip()

    if not email or not password:
        flash('Por favor ingresa tu correo y contraseña.', 'error')
        return render_template('tienda/cliente_login.html', email=email, next_url=next_url), 400

    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute(
        "SELECT id, email, password_hash, nombre, telefono, is_active FROM customers WHERE email = %s",
        [email]
    )
    customer = cur.fetchone()

    if not customer or not customer.get('is_active') or not check_password_hash(customer['password_hash'], password):
        flash('Credenciales incorrectas o cuenta inactiva. Verifica tus datos.', 'error')
        return render_template('tienda/cliente_login.html', email=email, next_url=next_url), 401

    session['customer_id'] = customer['id']
    session['customer_nombre'] = customer['nombre']
    session['customer_email'] = customer['email']

    flash(f"¡Nos alegra verte de nuevo, {customer['nombre'].split()[0]}!", 'success')
    return redirect(_safe_next_url(next_url))


@customer_bp.route('/cliente/logout')
def logout():
    session.pop('customer_id', None)
    session.pop('customer_nombre', None)
    session.pop('customer_email', None)
    flash('Has cerrado tu sesión de cliente.', 'success')
    return redirect('/')


@customer_bp.route('/cliente/cuenta')
@customer_login_required
def cuenta():
    customer_id = session['customer_id']
    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute(
        "SELECT id, email, nombre, telefono, puntos, created_at FROM customers WHERE id = %s",
        [customer_id]
    )
    customer = cur.fetchone()
    if not customer:
        session.pop('customer_id', None)
        session.pop('customer_nombre', None)
        session.pop('customer_email', None)
        flash('Sesión de usuario no válida.', 'error')
        return redirect(url_for('customer.login'))

    session['customer_nombre'] = customer['nombre']

    cur.execute(
        """SELECT id, puntos, motivo, order_id, created_at
           FROM customer_points_history
           WHERE customer_id = %s
           ORDER BY created_at DESC, id DESC LIMIT 50""",
        [customer_id]
    )
    points_history = cur.fetchall()

    cur.execute(
        """SELECT id, link_token, nombre, telefono, items_json, total, puntos_ganados, status, created_at
           FROM orders
           WHERE customer_id = %s
           ORDER BY created_at DESC, id DESC""",
        [customer_id]
    )
    orders = cur.fetchall()
    for order in orders:
        try:
            order['items'] = json.loads(order['items_json']) if order.get('items_json') else []
        except Exception:
            order['items'] = []

    return render_template(
        'tienda/cliente_cuenta.html',
        customer=customer,
        points_history=points_history,
        orders=orders
    )
