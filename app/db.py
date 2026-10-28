import os
import mysql.connector
from mysql.connector import pooling
from flask import g, has_app_context
from werkzeug.security import generate_password_hash

DB_CONFIG = {
    'host': os.environ.get('MYSQL_HOST', 'db'),
    'user': os.environ.get('MYSQL_USER', 'perfumes'),
    'password': os.environ.get('MYSQL_PASSWORD', 'perfumes'),
    'database': os.environ.get('MYSQL_DATABASE', 'perfumes'),
    'charset': 'utf8mb4',
    'autocommit': True,
}

_connection_pool = None

def _get_pool():
    global _connection_pool
    if _connection_pool is None:
        try:
            _connection_pool = pooling.MySQLConnectionPool(
                pool_name="siyash_pool",
                pool_size=10,
                pool_reset_session=True,
                **DB_CONFIG
            )
        except Exception:
            _connection_pool = None
    return _connection_pool

def get_db():
    if has_app_context():
        if not hasattr(g, '_database') or g._database is None or not g._database.is_connected():
            pool = _get_pool()
            if pool:
                try:
                    g._database = pool.get_connection()
                except Exception:
                    g._database = mysql.connector.connect(**DB_CONFIG)
            else:
                g._database = mysql.connector.connect(**DB_CONFIG)
        return g._database
    else:
        pool = _get_pool()
        if pool:
            try:
                return pool.get_connection()
            except Exception:
                return mysql.connector.connect(**DB_CONFIG)
        return mysql.connector.connect(**DB_CONFIG)

def close_db_connection(exception=None):
    db = getattr(g, '_database', None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass
        g._database = None

def _query(db, sql, params=None):
    cur = db.cursor(dictionary=True)
    cur.execute(sql, params or ())
    return cur

def _add_column_if_missing(cur, table, column, col_type):
    """Add a column if it doesn't exist in the table."""
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except mysql.connector.Error:
        pass  # column already exists

def _get_margen(db):
    row = _query(db, "SELECT setting_value FROM settings WHERE setting_key = 'margen_pct'").fetchone()
    if row and row['setting_value'] is not None:
        try:
            return float(row['setting_value'])
        except ValueError:
            return 30.0
    return 30.0

def set_setting(db, key, value):
    """Insert or update a key-value pair in the settings table."""
    cur = db.cursor()
    try:
        cur.execute('''
            INSERT INTO settings (setting_key, setting_value) 
            VALUES (%s, %s) 
            ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)
        ''', [key, str(value)])
        db.commit()
    finally:
        cur.close()

def rebuild_storefront_cache(db):
    '''Materialize heavy joins into the products table directly for 0% CPU on storefront.'''
    factor = 1 + _get_margen(db) / 100.0
    cur = db.cursor()
    
    # Storefront only needs `imagen` and `aromas`. It used to JOIN, but we cache it here.
    cur.execute('''
        UPDATE products p
        LEFT JOIN cosmetic_products cp ON cp.sku = p.sku
        LEFT JOIN multimarca_matches mmk ON mmk.sku_wholesale = p.sku
        LEFT JOIN multimarca_products mp ON mp.sku = mmk.sku_mm
        LEFT JOIN silk_matches smk ON smk.sku_wholesale = p.sku
        LEFT JOIN silk_products sp ON sp.sku = smk.sku_silk
        SET p.imagen = COALESCE(NULLIF(cp.imagen, ''), NULLIF(mp.imagen, ''), NULLIF(sp.imagen, '')),
            p.aromas = COALESCE(cp.aromas, mp.aromas)
    ''')
    
    # Pre-calculate prices
    cur.execute('''
        UPDATE products p
        JOIN (SELECT sku, MAX(import_date) AS max_date FROM prices GROUP BY sku) latest ON latest.sku = p.sku
        JOIN prices pr ON pr.sku = latest.sku AND pr.import_date = latest.max_date
        SET p.precio_mayorista = pr.precio,
            p.precio_venta = (ROUND((pr.precio * %s + 10) / 1000) * 1000 - 10)
    ''', [factor])
    db.commit()

def init_db():
    db = get_db()
    cur = db.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS products (
            sku VARCHAR(50) PRIMARY KEY,
            nombre TEXT,
            linea VARCHAR(100),
            ean VARCHAR(50),
            genero VARCHAR(50),
            formato VARCHAR(50)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')
    _add_column_if_missing(cur, 'products', 'stock', 'INT DEFAULT 0')
    _add_column_if_missing(cur, 'products', 'is_active', 'TINYINT(1) DEFAULT 0')
    _add_column_if_missing(cur, 'products', 'aroma_family', 'VARCHAR(50) DEFAULT NULL')
    _add_column_if_missing(cur, 'products', 'imagen', 'TEXT')
    _add_column_if_missing(cur, 'products', 'aromas', 'TEXT')
    _add_column_if_missing(cur, 'products', 'precio_mayorista', 'INT')
    _add_column_if_missing(cur, 'products', 'precio_venta', 'INT')
    _add_column_if_missing(cur, 'products', 'shopify_variant_id', 'VARCHAR(50) DEFAULT NULL')
    _add_column_if_missing(cur, 'products', 'en_cyber', 'TINYINT(1) NOT NULL DEFAULT 0')
    _add_column_if_missing(cur, 'products', 'descuento_cyber_pct', 'INT NOT NULL DEFAULT 0')
    try:
        cur.execute('ALTER TABLE products ADD INDEX idx_cyber (en_cyber)')
    except Exception:
        pass
    try:
        cur.execute('ALTER TABLE products ADD INDEX idx_active_stock (is_active, stock)')
    except Exception:
        pass
    try:
        cur.execute('ALTER TABLE products ADD INDEX idx_linea (linea)')
    except Exception:
        pass
    try:
        cur.execute('ALTER TABLE products ADD INDEX idx_active_stock_gen (is_active, stock, genero)')
    except Exception:
        pass
    try:
        cur.execute('ALTER TABLE products ADD INDEX idx_active_stock_price (is_active, stock, precio_venta)')
    except Exception:
        pass
    try:
        cur.execute('ALTER TABLE products ADD INDEX idx_active_stock_fam (is_active, stock, aroma_family)')
    except Exception:
        pass
    cur.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            id INT AUTO_INCREMENT PRIMARY KEY,
            sku VARCHAR(50) NOT NULL,
            import_date DATE NOT NULL,
            precio INT NOT NULL,
            FOREIGN KEY (sku) REFERENCES products(sku)
        ) ENGINE=InnoDB
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS imports (
            id INT AUTO_INCREMENT PRIMARY KEY,
            filename TEXT,
            import_date DATE UNIQUE,
            product_count INT
        ) ENGINE=InnoDB
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS cosmetic_products (
            sku VARCHAR(50) PRIMARY KEY,
            nombre TEXT,
            precio_retail INT,
            precio_ref INT,
            imagen TEXT,
            url TEXT,
            body_html LONGTEXT,
            aromas TEXT,
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')
    _add_column_if_missing(cur, 'cosmetic_products', 'body_html', 'LONGTEXT')
    _add_column_if_missing(cur, 'cosmetic_products', 'aromas', 'TEXT')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS silk_products (
            sku VARCHAR(50) PRIMARY KEY,
            nombre TEXT,
            precio_retail INT,
            precio_ref INT,
            imagen TEXT,
            url TEXT,
            body_html LONGTEXT,
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')
    _add_column_if_missing(cur, 'silk_products', 'body_html', 'LONGTEXT')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS silk_matches (
            sku_wholesale VARCHAR(50) PRIMARY KEY,
            sku_silk VARCHAR(50),
            confidence FLOAT
        ) ENGINE=InnoDB
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS multimarca_products (
            sku VARCHAR(50) PRIMARY KEY,
            nombre TEXT,
            precio_retail INT,
            precio_ref INT,
            imagen TEXT,
            url TEXT,
            body_html LONGTEXT,
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')
    _add_column_if_missing(cur, 'multimarca_products', 'body_html', 'LONGTEXT')
    _add_column_if_missing(cur, 'multimarca_products', 'aromas', 'TEXT')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS multimarca_matches (
            sku_wholesale VARCHAR(50) PRIMARY KEY,
            sku_mm VARCHAR(50),
            confidence FLOAT
        ) ENGINE=InnoDB
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INT AUTO_INCREMENT PRIMARY KEY,
            link_token VARCHAR(32) NOT NULL,
            nombre VARCHAR(200) NOT NULL,
            telefono VARCHAR(50) NOT NULL,
            items_json LONGTEXT NOT NULL,
            total INT NOT NULL DEFAULT 0,
            status VARCHAR(20) DEFAULT 'nuevo',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_token (link_token),
            INDEX idx_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')
    _add_column_if_missing(cur, 'orders', 'customer_id', 'INT DEFAULT NULL')
    _add_column_if_missing(cur, 'orders', 'puntos_ganados', 'INT DEFAULT 0')
    _add_column_if_missing(cur, 'orders', 'cupon_codigo', 'VARCHAR(50) DEFAULT NULL')
    _add_column_if_missing(cur, 'orders', 'descuento', 'INT NOT NULL DEFAULT 0')
    try:
        cur.execute('ALTER TABLE orders ADD INDEX idx_orders_customer (customer_id)')
    except Exception:
        pass

    cur.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id INT AUTO_INCREMENT PRIMARY KEY,
            email VARCHAR(191) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            nombre VARCHAR(120) NOT NULL,
            telefono VARCHAR(50),
            puntos INT NOT NULL DEFAULT 0,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_customer_email (email)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS customer_points_history (
            id INT AUTO_INCREMENT PRIMARY KEY,
            customer_id INT NOT NULL,
            puntos INT NOT NULL,
            motivo VARCHAR(255) NOT NULL,
            order_id INT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_cph_customer (customer_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS rewards (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nombre VARCHAR(200) NOT NULL,
            descripcion TEXT,
            puntos_requeridos INT NOT NULL,
            imagen VARCHAR(500),
            stock INT NOT NULL DEFAULT 999,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_reward_active (is_active, puntos_requeridos)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS customer_reward_redemptions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            customer_id INT NOT NULL,
            reward_id INT NOT NULL,
            puntos_gastados INT NOT NULL,
            codigo_canje VARCHAR(32) NOT NULL UNIQUE,
            estado VARCHAR(20) NOT NULL DEFAULT 'disponible',
            order_id INT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_crr_customer (customer_id, estado)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS coupons (
            id INT AUTO_INCREMENT PRIMARY KEY,
            codigo VARCHAR(50) NOT NULL UNIQUE,
            tipo VARCHAR(20) NOT NULL,
            valor INT NOT NULL,
            minimo_compra INT NOT NULL DEFAULT 0,
            limite_usos INT DEFAULT NULL,
            usos_actuales INT NOT NULL DEFAULT 0,
            customer_id INT DEFAULT NULL,
            valido_desde TIMESTAMP NULL DEFAULT NULL,
            valido_hasta TIMESTAMP NULL DEFAULT NULL,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_coupon_code (codigo, is_active),
            INDEX idx_coupon_cust (customer_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS coupon_usages (
            id INT AUTO_INCREMENT PRIMARY KEY,
            coupon_id INT NOT NULL,
            customer_id INT DEFAULT NULL,
            order_id INT DEFAULT NULL,
            descuento_aplicado INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_cu_coupon (coupon_id),
            INDEX idx_cu_customer (customer_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')

    _add_column_if_missing(cur, 'coupons', 'shopify_price_rule_id', 'VARCHAR(50) DEFAULT NULL')
    _add_column_if_missing(cur, 'coupons', 'shopify_discount_code_id', 'VARCHAR(50) DEFAULT NULL')
    _add_column_if_missing(cur, 'customers', 'shopify_customer_id', 'VARCHAR(50) DEFAULT NULL')

    try:
        cur.execute('SELECT COUNT(*) FROM coupons')
        if cur.fetchone()[0] == 0:
            cur.execute('''
                INSERT INTO coupons (codigo, tipo, valor, minimo_compra, limite_usos, is_active)
                VALUES (%s, %s, %s, %s, %s, 1)
            ''', ('CYBERSIYASH', 'porcentaje', 15, 30000, 500))
    except Exception:
        pass

    try:
        cur.execute('SELECT COUNT(*) FROM rewards')
        if cur.fetchone()[0] == 0:
            initial_rewards = [
                ("Decant de Lujo 5ml (Perfume Árabe)", "Muestra exclusiva en atomizador de vidrio.", 200, "/static/img/LogoSiyash-01-t.png", 100),
                ("Envío Gratis a todo Chile", "Cupón válido para despacho sin costo en tu próxima compra.", 350, "/static/img/LogoSiyash-01-t.png", 500),
                ("Miniatura Colección 10ml", "Formato viaje de alta concentración.", 600, "/static/img/LogoSiyash-01-t.png", 50),
                ("Perfume Árabe 100ml Full Size", "Frasco completo a elección del catálogo Club Siyash.", 1500, "/static/img/LogoSiyash-01-t.png", 20),
            ]
            cur.executemany(
                '''INSERT INTO rewards (nombre, descripcion, puntos_requeridos, imagen, stock, is_active)
                   VALUES (%s, %s, %s, %s, %s, 1)''',
                initial_rewards
            )
    except Exception:
        pass

    try:
        cur.execute('''SELECT COUNT(*) FROM (
            SELECT sku, import_date, COUNT(*) as cnt FROM prices
            GROUP BY sku, import_date HAVING cnt > 1
        ) dupes''')
        dupes = cur.fetchone()[0]
    except Exception:
        dupes = 0

    if dupes > 0:
        autocommit = db.autocommit
        db.autocommit = False
        try:
            cur.execute('''CREATE TABLE prices_dedup LIKE prices''')
            cur.execute('''ALTER TABLE prices_dedup ADD UNIQUE INDEX unique_sku_date (sku, import_date)''')
            cur.execute('''INSERT IGNORE INTO prices_dedup (sku, import_date, precio)
                           SELECT sku, import_date, MAX(precio) FROM prices
                           GROUP BY sku, import_date''')
            cur.execute('''RENAME TABLE prices TO prices_old, prices_dedup TO prices''')
            cur.execute('''DROP TABLE prices_old''')
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.autocommit = autocommit
    else:
        try:
            cur.execute('''ALTER TABLE prices ADD UNIQUE INDEX unique_sku_date (sku, import_date)''')
        except Exception:
            pass

    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')

    admin_pass = os.environ.get('ADMIN_PASSWORD', 'admin123')
    cur.execute('SELECT COUNT(*) as cnt FROM users')
    if cur.fetchone()[0] == 0:
        cur.execute('INSERT INTO users (username, password_hash) VALUES (%s, %s)',
                    ['admin', generate_password_hash(admin_pass)])
    else:
        cur.execute('UPDATE users SET password_hash = %s WHERE username = %s',
                    [generate_password_hash(admin_pass), 'admin'])

    cur.execute('''
        CREATE TABLE IF NOT EXISTS product_views (
            sku VARCHAR(50) PRIMARY KEY,
            views_count INT NOT NULL DEFAULT 1,
            last_viewed TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_pv_count (views_count DESC)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            setting_key VARCHAR(50) PRIMARY KEY,
            setting_value TEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''')

    try:
        cur.execute("SELECT COUNT(*) FROM settings WHERE setting_key = 'cyber_mode_active'")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO settings (setting_key, setting_value) VALUES ('cyber_mode_active', '0')")
    except Exception:
        pass

    cur.close()
    rebuild_storefront_cache(db)
    db.close()
