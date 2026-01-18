from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from supabase import create_client, Client
import os
import bcrypt
from dotenv import load_dotenv
from utils.activity_logger import log_activity
from utils.device_detector import get_template_suffix

# Load environment variables
load_dotenv()

# Create blueprint
auth_bp = Blueprint('auth', __name__)

# Supabase configuration
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_ANON_KEY')

if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        mobile = request.form.get('mobile')
        password = request.form.get('password')
        
        if not mobile or not password:
            flash("Mobile number and password are required", 'error')
            template_name = f'auth/login{get_template_suffix()}.html'
            return render_template(template_name)
        
        # Validate mobile number format
        if len(mobile) != 10 or not mobile.isdigit():
            flash("Please enter a valid 10-digit mobile number", 'error')
            template_name = f'auth/login{get_template_suffix()}.html'
            return render_template(template_name)
        
        try:
            # Fetch user by phone_number and role_id (without password check)
            user_result = supabase.table('users').select('*').eq('role_id', 4).eq('phone_number', mobile).execute()
            
            if user_result.data and len(user_result.data) > 0:
                user_data = user_result.data[0]
                stored_password = user_data.get('password', '')
                
                # Verify password using bcrypt
                if stored_password and bcrypt.checkpw(password.encode('utf-8'), stored_password.encode('utf-8')):
                    # Password is correct
                    user_id = user_data.get('id')
                    
                    # Store user info in session
                    session['user'] = {
                        'id': user_id,
                        'mobile': mobile,
                        'name': user_data.get('name', 'User'),
                        'email': user_data.get('email', ''),
                        'role_id': user_data.get('role_id')
                    }
                    
                    # Log login activity
                    try:
                        log_activity(
                            leader_id=user_id,
                            user_id=user_id,
                            activity_type='user_login',
                            description='User logged in',
                            user_role='leader',
                            user_name=session['user'].get('name', 'User'),
                            source='cell_app',
                            platform='mobile' if mobile else 'web',
                            details={'mobile': mobile, 'login_time': 'now'}
                        )
                    except Exception as e:
                        print(f"Error logging activity: {e}")
                    
                    flash("Login successful!", 'success')
                    return redirect(url_for('main.index'))
                else:
                    # Password is incorrect
                    flash("Invalid mobile number or password", 'error')
            else:
                # User not found
                flash("Invalid mobile number or password", 'error')
        except Exception as e:
            print(f"Error during login: {e}")
            flash("An error occurred during login. Please try again.", 'error')
    
    template_name = f'auth/login{get_template_suffix()}.html'
    return render_template(template_name)

@auth_bp.route('/logout')
def logout():
    session.pop('user', None)
    flash("You have been logged out", 'info')
    return redirect(url_for('auth.login'))
