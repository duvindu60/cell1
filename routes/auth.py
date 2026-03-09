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
            # 1) Try leader login (users table)
            user_result = supabase.table('users').select('*').eq('role_id', 4).eq('phone_number', mobile).execute()
            
            if user_result.data and len(user_result.data) > 0:
                user_data = user_result.data[0]
                stored_password = user_data.get('password', '')
                
                if stored_password and bcrypt.checkpw(password.encode('utf-8'), stored_password.encode('utf-8')):
                    user_id = user_data.get('id')
                    session['user'] = {
                        'id': user_id,
                        'mobile': mobile,
                        'name': user_data.get('name', 'User'),
                        'email': user_data.get('email', ''),
                        'role_id': user_data.get('role_id')
                    }
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
            
            # 2) Try deputy leader login (cell_members only; no users row)
            DEPUTY_TEST_PASSWORD = 'leader123'
            member_result = supabase.table('cell_members').select('id, name, phone_number, leader_id').eq('phone_number', mobile).eq('deputy_leader', True).eq('can_login', True).execute()
            if member_result.data and len(member_result.data) > 0:
                member = member_result.data[0]
                if password == DEPUTY_TEST_PASSWORD:
                    leader_id = member.get('leader_id')
                    member_id = member.get('id')
                    session['user'] = {
                        'id': member_id,
                        'member_id': member_id,
                        'leader_id': leader_id,
                        'mobile': mobile,
                        'name': member.get('name', 'Deputy'),
                        'is_deputy': True
                    }
                    try:
                        log_activity(
                            leader_id=leader_id,
                            user_id=leader_id,
                            activity_type='user_login',
                            description='Deputy leader logged in',
                            user_role='deputy_leader',
                            user_name=session['user'].get('name', 'Deputy'),
                            source='cell_app',
                            platform='mobile' if mobile else 'web',
                            details={'mobile': mobile, 'login_time': 'now', 'member_id': str(member_id)}
                        )
                    except Exception as e:
                        print(f"Error logging activity: {e}")
                    flash("Login successful!", 'success')
                    return redirect(url_for('main.attendance_list'))
            
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
