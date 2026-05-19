from flask import Flask, session, request, g, redirect, url_for
from routes.auth import auth_bp
from routes.main import main_bp
from routes.api import api_bp
from config import config
import os
from datetime import timedelta

def create_app(config_name=None):
    """Application factory pattern"""
    app = Flask(__name__)
    
    # Load configuration
    config_name = config_name or os.getenv('FLASK_ENV', 'default')
    app.config.from_object(config[config_name])
    
    # Session configuration
    app.config['SESSION_COOKIE_SECURE'] = False  # Set to True in production with HTTPS
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)  # 1 hour
    app.config['SESSION_COOKIE_NAME'] = 'cellapp_session'
    
    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    @app.before_request
    def require_password_set_before_app_use():
        """Block app routes until leader sets their own password after admin-approved reset."""
        if not session.get('must_set_password'):
            return None
        allowed = {
            'auth.set_password',
            'auth.logout',
            'static',
            'chrome_devtools_probe',
        }
        if request.endpoint in allowed:
            return None
        return redirect(url_for('auth.set_password'))

    @app.route('/.well-known/appspecific/com.chrome.devtools.json')
    def chrome_devtools_probe():
        # Chrome/DevTools may probe this optional metadata endpoint.
        # Return 204 to avoid noisy 404 log entries.
        return '', 204
    
    return app

# Create app instance
app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
