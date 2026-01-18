from flask import Blueprint, request, jsonify, session
from functools import wraps
# Create blueprint
api_bp = Blueprint('api', __name__, url_prefix='/api')
def login_required(f):
    """Decorator to require login for API endpoints"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function
@api_bp.route('/user')
@login_required
def get_user():
    """Get current user information"""
    return jsonify({
        'user': session['user'],
        'status': 'success'
    })
@api_bp.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'message': 'API is running'
    })
@api_bp.route('/test')
@login_required
def test_endpoint():
    """Test endpoint for authenticated users"""
    return jsonify({
        'message': 'This is a protected endpoint',
        'user_id': session['user']['id']
    })
