import os
from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from .db import close_db_connection

csrf = CSRFProtect()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["120 per minute", "3000 per hour"],
    storage_uri="memory://"
)

def create_app():
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.url_map.strict_slashes = False
    
    # Trust reverse proxy headers (Traefik / Dokploy)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    
    app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'cambiar-en-produccion')
    
    csrf.init_app(app)
    limiter.init_app(app)
    
    app.teardown_appcontext(close_db_connection)
    
    @app.template_filter('resize_img')
    def resize_img(url, width=400):
        if not url: return url
        if '?' in url:
            return f"{url}&width={width}"
        return f"{url}?width={width}"
    
    # Ultra-lightweight health endpoint for Docker / Traefik (0ms, 0 DB calls)
    @app.route('/health')
    @limiter.exempt
    def health():
        return {"status": "ok"}, 200

    # Favicon route to prevent 404 template rendering storms
    @app.route('/favicon.ico')
    @limiter.exempt
    def favicon():
        return app.send_static_file('img/LogoSiyash-01-t.png')

    # SEO robots.txt
    @app.route('/robots.txt')
    @limiter.exempt
    def robots():
        content = "User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\nSitemap: https://siyash.cl/sitemap.xml\n"
        return content, 200, {'Content-Type': 'text/plain'}

    # SEO sitemap.xml
    @app.route('/sitemap.xml')
    @limiter.exempt
    def sitemap():
        try:
            from .db import get_db
            db = get_db()
            cur = db.cursor(dictionary=True)
            cur.execute("SELECT sku FROM products WHERE is_active = 1 AND stock > 0")
            skus = [r['sku'] for r in cur.fetchall()]
        except Exception:
            skus = []
        xml = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            '  <url><loc>https://siyash.cl/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>',
            '  <url><loc>https://siyash.cl/perfumes</loc><changefreq>daily</changefreq><priority>0.9</priority></url>',
            '  <url><loc>https://siyash.cl/mujer</loc><changefreq>daily</changefreq><priority>0.8</priority></url>',
            '  <url><loc>https://siyash.cl/hombre</loc><changefreq>daily</changefreq><priority>0.8</priority></url>',
            '  <url><loc>https://siyash.cl/nosotros</loc><changefreq>monthly</changefreq><priority>0.5</priority></url>',
        ]
        for sku in skus:
            xml.append(f'  <url><loc>https://siyash.cl/producto/{sku}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>')
        xml.append('</urlset>')
        return "\n".join(xml), 200, {'Content-Type': 'application/xml'}

    # Context processors
    from .routes.store import inject_nav_data
    app.context_processor(inject_nav_data)
    
    @app.context_processor
    def inject_tunnel_url():
        return dict(tunnel_url='')
        
    # Register blueprints
    from .routes.store import store_bp
    from .routes.admin import admin_bp
    from .routes.sync import sync_bp
    
    app.register_blueprint(store_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(sync_bp)
    
    return app
