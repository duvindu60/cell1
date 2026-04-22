from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from supabase import create_client, Client
import os
import bcrypt
import re
from dotenv import load_dotenv
from utils.activity_logger import log_activity

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

def sl_local_mobile10_candidates(mobile_input: str) -> tuple[str | None, list[str]]:
    """
    Convert Sri Lanka mobile inputs into the app's canonical format (local 10-digit: 071XXXXXXXX)
    and return a set of DB lookup candidates for the same number.
    """
    raw = (mobile_input or "").strip()
    digits = re.sub(r"\D", "", raw)  # remove '+', spaces, etc.
    if not digits:
        return None, []

    local10 = None

    # Local already: 0 + 9 digits
    if len(digits) == 10 and digits.startswith("0"):
        local10 = digits
    # National format: 9 digits starting with 7 -> 0 + 9 digits
    elif len(digits) == 9 and digits.startswith("7"):
        local10 = "0" + digits
    # International: 94 + 9 digits -> 0 + 9 digits
    elif len(digits) == 11 and digits.startswith("94"):
        local10 = "0" + digits[2:]

    if not local10:
        return None, []

    national9 = local10[1:]  # remove leading 0

    # Possible DB storage formats (exact match required by Supabase query)
    candidates = {
        local10,                # 071...
        national9,              # 7...
        f"+94{national9}",      # +947...
        f"94{national9}",       # 947...
    }

    return local10, list(candidates)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        mobile = request.form.get('mobile')
        password = request.form.get('password')
        
        if not mobile or not password:
            flash("Mobile number and password are required", 'error')
            return render_template('auth/login.html')
        
        local10, candidates = sl_local_mobile10_candidates(mobile)
        if not local10 or not candidates:
            flash(
                "Please enter a valid Sri Lanka mobile number (example: 071..., +947...)",
                'error'
            )
            return render_template('auth/login.html')
        
        try:
            # 1) Try leader login (users table)
            user_data = None
            for cand in candidates:
                user_result = (
                    supabase.table('users')
                    .select('*')
                    .eq('role_id', 4)
                    .eq('phone_number', cand)
                    .execute()
                )
                if user_result.data and len(user_result.data) > 0:
                    user_data = user_result.data[0]
                    break

            if user_data:
                stored_password = user_data.get('password', '')
                if stored_password and bcrypt.checkpw(password.encode('utf-8'), stored_password.encode('utf-8')):
                    user_id = user_data.get('id')
                    session['user'] = {
                        'id': user_id,
                        'mobile': local10,  # canonical local 10-digit format
                        'name': user_data.get('name', 'User'),
                        'email': user_data.get('email', ''),
                        'role_id': user_data.get('role_id')
                    }
                    try:
                        from routes.main import ensure_leader_self_member_row
                        ensure_leader_self_member_row(user_id)
                    except Exception as le:
                        print(f"ensure_leader_self_member_row skipped: {le}")
                    try:
                        log_activity(
                            leader_id=user_id,
                            user_id=user_id,
                            activity_type='user_login',
                            description='User logged in',
                            user_role='leader',
                            user_name=session['user'].get('name', 'User'),
                            source='cell_app',
                            platform='mobile' if local10 else 'web',
                            details={'mobile': local10, 'login_time': 'now'}
                        )
                    except Exception as e:
                        print(f"Error logging activity: {e}")
                    flash("Login successful!", 'success')
                    return redirect(url_for('main.index'))
            
            # 2) Try deputy leader login (cell_members only; no users row)
            DEPUTY_TEST_PASSWORD = 'leader123'
            member = None
            for cand in candidates:
                member_result = (
                    supabase.table('cell_members')
                    .select('id, name, phone_number, leader_id')
                    .eq('phone_number', cand)
                    .eq('deputy_leader', True)
                    .eq('can_login', True)
                    .execute()
                )
                if member_result.data and len(member_result.data) > 0:
                    member = member_result.data[0]
                    break

            if member and password == DEPUTY_TEST_PASSWORD:
                leader_id = member.get('leader_id')
                member_id = member.get('id')
                session['user'] = {
                    'id': member_id,
                    'member_id': member_id,
                    'leader_id': leader_id,
                    'mobile': local10,  # canonical local 10-digit format
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
                        platform='mobile' if local10 else 'web',
                        details={'mobile': local10, 'login_time': 'now', 'member_id': str(member_id)}
                    )
                except Exception as e:
                    print(f"Error logging activity: {e}")
                flash("Login successful!", 'success')
                return redirect(url_for('main.attendance_list'))
            
            flash("Invalid mobile number or password", 'error')
        except Exception as e:
            print(f"Error during login: {e}")
            flash("An error occurred during login. Please try again.", 'error')
    
    return render_template('auth/login.html')

@auth_bp.route('/logout')
def logout():
    session.pop('user', None)
    flash("You have been logged out", 'info')
    return redirect(url_for('auth.login'))
