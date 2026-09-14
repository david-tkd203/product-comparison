import os
from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

from .db import close_db_connection

csrf = CSRFProtect()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["1000 per day", "200 per hour"],
    storage_uri="memory://"
)

def create_app():
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.url_map.strict_slashes = False
    
    app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'cambiar-en-produccion')
    
    csrf.init_app(app)
    limiter.init_app(app)
    
    app.teardown_appcontext(close_db_connection)
    
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
