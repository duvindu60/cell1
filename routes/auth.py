from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from supabase import create_client, Client
import os
import bcrypt
import re
from datetime import datetime
from dotenv import load_dotenv
from utils.activity_logger import log_activity

# Load environment variables
load_dotenv()

# Create blueprint
auth_bp = Blueprint('auth', __name__)

# Supabase configuration
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_ANON_KEY')

DEPUTY_TEST_PASSWORD = 'leader123'
PASSWORD_RESET_APPROVED_STATUS = 'approved_pending_password'
MIN_USER_PASSWORD_LENGTH = 6

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


def mask_email(email: str | None) -> str:
    if not email or '@' not in email:
        return ''
    local, domain = email.split('@', 1)
    if len(local) <= 1:
        masked_local = '*'
    else:
        masked_local = local[0] + '***'
    return f'{masked_local}@{domain}'


def account_key(actor_type: str, record_id: str) -> str:
    return f'{actor_type}:{record_id}'


def parse_account_key(key: str) -> tuple[str, str] | None:
    if not key or ':' not in key:
        return None
    actor_type, record_id = key.split(':', 1)
    if actor_type not in ('leader', 'deputy') or not record_id:
        return None
    return actor_type, record_id


def find_login_accounts(candidates: list[str]) -> list[dict]:
    """Return all leader and deputy accounts matching any phone format candidate."""
    if not supabase:
        return []

    leaders_by_id: dict[str, dict] = {}
    for cand in candidates:
        user_result = (
            supabase.table('users')
            .select('id, name, email, phone_number, role_id, cell_category, password')
            .eq('role_id', 4)
            .eq('phone_number', cand)
            .execute()
        )
        for row in user_result.data or []:
            leaders_by_id[row['id']] = row

    deputies_by_id: dict[str, dict] = {}
    for cand in candidates:
        member_result = (
            supabase.table('cell_members')
            .select('id, name, phone_number, leader_id')
            .eq('phone_number', cand)
            .eq('deputy_leader', True)
            .eq('can_login', True)
            .execute()
        )
        for row in member_result.data or []:
            deputies_by_id[row['id']] = row

    accounts: list[dict] = []
    for user in leaders_by_id.values():
        subtitle_parts = []
        masked = mask_email(user.get('email'))
        if masked:
            subtitle_parts.append(masked)
        if user.get('cell_category'):
            subtitle_parts.append(str(user['cell_category']))
        accounts.append({
            'key': account_key('leader', str(user['id'])),
            'actor_type': 'leader',
            'id': str(user['id']),
            'name': user.get('name') or 'Cell leader',
            'subtitle': ' · '.join(subtitle_parts) if subtitle_parts else 'Cell leader',
            'role_label': 'Cell leader',
            '_user': user,
        })

    leader_names: dict[str, str] = {}
    if deputies_by_id:
        leader_ids = list({str(m.get('leader_id')) for m in deputies_by_id.values() if m.get('leader_id')})
        if leader_ids:
            leaders_lookup = (
                supabase.table('users')
                .select('id, name')
                .in_('id', leader_ids)
                .execute()
            )
            for row in leaders_lookup.data or []:
                leader_names[str(row['id'])] = row.get('name') or 'Leader'

    for member in deputies_by_id.values():
        leader_id = str(member.get('leader_id') or '')
        leader_name = leader_names.get(leader_id, 'your cell')
        accounts.append({
            'key': account_key('deputy', str(member['id'])),
            'actor_type': 'deputy',
            'id': str(member['id']),
            'name': member.get('name') or 'Deputy leader',
            'subtitle': f'Deputy for {leader_name}',
            'role_label': 'Deputy leader',
            '_member': member,
        })

    accounts.sort(key=lambda a: (0 if a['actor_type'] == 'leader' else 1, a['name'].lower()))
    return accounts


def accounts_for_template(accounts: list[dict]) -> list[dict]:
    return [
        {
            'key': a['key'],
            'name': a['name'],
            'subtitle': a['subtitle'],
            'role_label': a['role_label'],
            'actor_type': a['actor_type'],
        }
        for a in accounts
    ]


def get_account_by_key(accounts: list[dict], key: str) -> dict | None:
    return next((a for a in accounts if a['key'] == key), None)


def verify_account_password(account: dict, password: str) -> bool:
    if account['actor_type'] == 'leader':
        user = account.get('_user') or {}
        stored_password = user.get('password', '')
        if not stored_password:
            return False
        return bcrypt.checkpw(password.encode('utf-8'), stored_password.encode('utf-8'))
    return password == DEPUTY_TEST_PASSWORD


def login_leader(user_data: dict, local10: str, duplicate_count: int = 1):
    user_id = user_data.get('id')
    session['user'] = {
        'id': user_id,
        'mobile': local10,
        'name': user_data.get('name', 'User'),
        'email': user_data.get('email', ''),
        'role_id': user_data.get('role_id'),
        'cell_category': user_data.get('cell_category'),
    }
    try:
        from routes.main import ensure_leader_self_member_row
        ensure_leader_self_member_row(user_id)
    except Exception as le:
        print(f"ensure_leader_self_member_row skipped: {le}")

    pending_reset = get_approved_pending_password_reset(user_id)
    if pending_reset:
        session['must_set_password'] = True
        session['password_reset_flag_id'] = pending_reset.get('id')
        flash('Your password reset was approved. Please choose your own password to continue.', 'info')
        return redirect(url_for('auth.set_password'))

    try:
        log_activity(
            leader_id=user_id,
            user_id=user_id,
            activity_type='user_login',
            description='User logged in',
            user_role='leader',
            user_name=session['user'].get('name', 'User'),
            source='cell_app',
            platform='web',
            details={
                'mobile': local10,
                'duplicate_phone_count': duplicate_count,
            },
        )
    except Exception as e:
        print(f"Error logging activity: {e}")
    flash('Login successful!', 'success')
    return redirect(url_for('main.index'))


def login_deputy(member: dict, local10: str, duplicate_count: int = 1):
    leader_id = member.get('leader_id')
    member_id = member.get('id')
    session['user'] = {
        'id': member_id,
        'member_id': member_id,
        'leader_id': leader_id,
        'mobile': local10,
        'name': member.get('name', 'Deputy'),
        'is_deputy': True,
    }
    try:
        log_activity(
            leader_id=leader_id,
            user_id=member_id,
            activity_type='user_login',
            description='Deputy leader logged in',
            user_role='deputy_leader',
            user_name=session['user'].get('name', 'Deputy'),
            source='cell_app',
            platform='web',
            details={
                'mobile': local10,
                'member_id': str(member_id),
                'duplicate_phone_count': duplicate_count,
            },
        )
    except Exception as e:
        print(f"Error logging activity: {e}")
    flash('Login successful!', 'success')
    return redirect(url_for('main.attendance_list'))


def complete_login(account: dict, local10: str, duplicate_count: int = 1):
    session.pop('login_candidates', None)
    session.pop('login_mobile', None)
    if account['actor_type'] == 'leader':
        return login_leader(account['_user'], local10, duplicate_count)
    return login_deputy(account['_member'], local10, duplicate_count)


def has_pending_password_reset(leader_id: str) -> bool:
    if not supabase:
        return False
    existing = (
        supabase.table('flagged_issues')
        .select('id, status')
        .eq('leader_id', str(leader_id))
        .eq('issue_type', 'password_reset_request')
        .in_('status', ['pending', PASSWORD_RESET_APPROVED_STATUS])
        .limit(1)
        .execute()
    )
    return bool(existing.data)


def get_approved_pending_password_reset(leader_id: str) -> dict | None:
    if not supabase:
        return None
    result = (
        supabase.table('flagged_issues')
        .select('id, leader_id, status, created_at')
        .eq('leader_id', str(leader_id))
        .eq('issue_type', 'password_reset_request')
        .eq('status', PASSWORD_RESET_APPROVED_STATUS)
        .order('created_at', desc=True)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None


def validate_new_password(password: str, confirm: str) -> str | None:
    if not password or not confirm:
        return 'New password and confirmation are required.'
    if len(password) < MIN_USER_PASSWORD_LENGTH:
        return f'Password must be at least {MIN_USER_PASSWORD_LENGTH} characters.'
    if password != confirm:
        return 'Passwords do not match.'
    return None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def create_password_reset_request(user_id: str, name: str, mobile: str) -> bool:
    if not supabase:
        return False

    try:
        from routes.main import ensure_leader_self_member_row
        member_id = ensure_leader_self_member_row(user_id)
    except Exception as e:
        print(f"ensure_leader_self_member_row for password reset failed: {e}")
        member_id = None

    if not member_id:
        print(f"Password reset: no cell_members self-row for leader {user_id}")
        return False

    flag_data = {
        'member_id': str(member_id),
        'leader_id': str(user_id),
        'issue_type': 'password_reset_request',
        'description': f'Password reset requested for {name} ({mobile}). Approve in cell portal.',
        'status': 'pending',
    }
    result = supabase.table('flagged_issues').insert(flag_data).execute()
    if not result.data:
        return False
    try:
        log_activity(
            leader_id=str(user_id),
            user_id=str(user_id),
            activity_type='password_reset_requested',
            description=f'Password reset requested for {name}',
            user_role='leader',
            user_name=name,
            source='cell_app',
            platform='web',
            details={
                'mobile': mobile,
                'flag_id': result.data[0].get('id'),
            },
            is_important=True,
        )
    except Exception as e:
        print(f"Error logging password reset request: {e}")
    return True


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    login_accounts = None
    mobile_prefill = ''

    if request.method == 'POST':
        mobile = (request.form.get('mobile') or '').strip()
        password = request.form.get('password') or ''
        selected_key = (request.form.get('account_key') or '').strip()

        if not mobile:
            flash('Mobile number is required', 'error')
            return render_template(
                'auth/login.html',
                login_accounts=login_accounts,
                selected_account_key='',
            )

        local10, candidates = sl_local_mobile10_candidates(mobile)
        if not local10 or not candidates:
            flash(
                'Please enter a valid Sri Lanka mobile number (example: 071..., +947...)',
                'error',
            )
            return render_template('auth/login.html', login_accounts=login_accounts)

        mobile_prefill = local10

        try:
            accounts = find_login_accounts(candidates)
            if not accounts:
                flash('Invalid mobile number or password', 'error')
                return render_template('auth/login.html', login_accounts=login_accounts)

            duplicate_count = len(accounts)

            if duplicate_count > 1 and not selected_key:
                if not password:
                    flash(
                        'Multiple accounts use this number. Select your account, then enter your password.',
                        'info',
                    )
                else:
                    flash(
                        'Multiple accounts use this number. Select your account before signing in.',
                        'info',
                    )
                session['login_candidates'] = accounts_for_template(accounts)
                session['login_mobile'] = local10
                return render_template(
                    'auth/login.html',
                    login_accounts=session['login_candidates'],
                    mobile_prefill=mobile_prefill,
                    selected_account_key=selected_key,
                )

            account = get_account_by_key(accounts, selected_key) if selected_key else accounts[0]
            if not account:
                flash('Please select your account.', 'error')
                session['login_candidates'] = accounts_for_template(accounts)
                return render_template(
                    'auth/login.html',
                    login_accounts=session['login_candidates'],
                    mobile_prefill=mobile_prefill,
                )

            if not password:
                flash('Password is required', 'error')
                if duplicate_count > 1:
                    session['login_candidates'] = accounts_for_template(accounts)
                    return render_template(
                        'auth/login.html',
                        login_accounts=session['login_candidates'],
                        mobile_prefill=mobile_prefill,
                        selected_account_key=account['key'],
                    )
                return render_template('auth/login.html', mobile_prefill=mobile_prefill)

            if not verify_account_password(account, password):
                flash('Invalid mobile number or password', 'error')
                if duplicate_count > 1:
                    session['login_candidates'] = accounts_for_template(accounts)
                    return render_template(
                        'auth/login.html',
                        login_accounts=session['login_candidates'],
                        mobile_prefill=mobile_prefill,
                        selected_account_key=account['key'],
                    )
                return render_template('auth/login.html', mobile_prefill=mobile_prefill)

            return complete_login(account, local10, duplicate_count)

        except Exception as e:
            print(f"Error during login: {e}")
            flash('An error occurred during login. Please try again.', 'error')

    elif session.get('login_candidates'):
        login_accounts = session['login_candidates']
        mobile_prefill = session.get('login_mobile', '')

    return render_template(
        'auth/login.html',
        login_accounts=login_accounts,
        mobile_prefill=mobile_prefill,
        selected_account_key='',
    )


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    reset_accounts = None
    mobile_prefill = ''

    if request.method == 'POST':
        mobile = (request.form.get('mobile') or '').strip()
        selected_key = (request.form.get('account_key') or '').strip()

        if not mobile:
            flash('Mobile number is required', 'error')
            return render_template('auth/forgot_password.html', reset_accounts=reset_accounts)

        local10, candidates = sl_local_mobile10_candidates(mobile)
        if not local10 or not candidates:
            flash(
                'Please enter a valid Sri Lanka mobile number (example: 071..., +947...)',
                'error',
            )
            return render_template('auth/forgot_password.html', reset_accounts=reset_accounts)

        mobile_prefill = local10

        try:
            all_accounts = find_login_accounts(candidates)
            leader_accounts = [a for a in all_accounts if a['actor_type'] == 'leader']

            if not leader_accounts:
                flash(
                    'No cell leader account found for this number. Deputies must ask their leader or admin for help.',
                    'error',
                )
                return render_template('auth/forgot_password.html', mobile_prefill=mobile_prefill)

            if len(leader_accounts) > 1 and not selected_key:
                reset_accounts = accounts_for_template(leader_accounts)
                flash('Multiple accounts use this number. Select yours to request a password reset.', 'info')
                return render_template(
                    'auth/forgot_password.html',
                    reset_accounts=reset_accounts,
                    mobile_prefill=mobile_prefill,
                    selected_account_key=selected_key,
                )

            account = get_account_by_key(leader_accounts, selected_key) if selected_key else leader_accounts[0]
            if not account:
                flash('Please select your account.', 'error')
                return render_template(
                    'auth/forgot_password.html',
                    reset_accounts=accounts_for_template(leader_accounts),
                    mobile_prefill=mobile_prefill,
                )

            user_id = account['id']
            user_name = account['name']

            if has_pending_password_reset(user_id):
                flash(
                    'A password reset is already in progress for this account. '
                    'Sign in with the temporary password from admin if approved, or wait for admin approval.',
                    'info',
                )
                return render_template('auth/forgot_password.html', mobile_prefill=mobile_prefill)

            if create_password_reset_request(user_id, user_name, local10):
                flash(
                    'Password reset request sent. An admin will approve it in the cell portal.',
                    'success',
                )
                return redirect(url_for('auth.login'))

            flash('Could not submit password reset request. Please try again.', 'error')

        except Exception as e:
            print(f"Error during password reset request: {e}")
            flash('An error occurred. Please try again.', 'error')

    return render_template(
        'auth/forgot_password.html',
        reset_accounts=reset_accounts,
        mobile_prefill=mobile_prefill,
    )


@auth_bp.route('/set-password', methods=['GET', 'POST'])
def set_password():
    if 'user' not in session:
        flash('Please sign in first.', 'error')
        return redirect(url_for('auth.login'))

    if session.get('user', {}).get('is_deputy'):
        flash('Password reset is only available for cell leader accounts.', 'error')
        return redirect(url_for('main.attendance_list'))

    user_id = str(session['user'].get('id'))
    flag_id = session.get('password_reset_flag_id')
    pending_reset = get_approved_pending_password_reset(user_id)

    if not pending_reset and not session.get('must_set_password'):
        return redirect(url_for('main.index'))

    if pending_reset and not flag_id:
        flag_id = pending_reset.get('id')
        session['password_reset_flag_id'] = flag_id
        session['must_set_password'] = True

    user_name = session['user'].get('name', 'User')

    if request.method == 'POST':
        password = request.form.get('password') or ''
        confirm = request.form.get('confirm_password') or ''
        error = validate_new_password(password, confirm)
        if error:
            flash(error, 'error')
            return render_template('auth/set_password.html', user_name=user_name)

        if not supabase:
            flash('Unable to update password. Please try again.', 'error')
            return render_template('auth/set_password.html', user_name=user_name)

        try:
            password_hash = hash_password(password)
            supabase.table('users').update({'password': password_hash}).eq('id', user_id).execute()

            session.pop('must_set_password', None)
            session.pop('password_reset_flag_id', None)

            if flag_id:
                try:
                    supabase.table('flagged_issues').update({
                        'status': 'resolved',
                        'response': 'User set their own password in Cell App.',
                        'updated_at': datetime.now().isoformat(),
                    }).eq('id', flag_id).execute()
                except Exception as fe:
                    print(f"Error resolving password reset flag {flag_id}: {fe}")

            try:
                log_activity(
                    leader_id=user_id,
                    user_id=user_id,
                    activity_type='password_changed',
                    description='User set a new password after admin-approved reset',
                    user_role='leader',
                    user_name=user_name,
                    source='cell_app',
                    platform='web',
                    details={'flag_id': flag_id},
                    is_important=True,
                )
                log_activity(
                    leader_id=user_id,
                    user_id=user_id,
                    activity_type='password_reset_completed',
                    description='Password reset completed',
                    user_role='leader',
                    user_name=user_name,
                    source='cell_app',
                    platform='web',
                    details={'flag_id': flag_id},
                    is_important=True,
                )
                log_activity(
                    leader_id=user_id,
                    user_id=user_id,
                    activity_type='user_login',
                    description='User logged in after setting new password',
                    user_role='leader',
                    user_name=user_name,
                    source='cell_app',
                    platform='web',
                    details={'mobile': session['user'].get('mobile'), 'after_password_reset': True},
                )
            except Exception as e:
                print(f"Error logging password set activity: {e}")

            flash('Your password has been updated. Welcome back!', 'success')
            return redirect(url_for('main.index'))

        except Exception as e:
            print(f"Error setting new password: {e}")
            flash('Could not update password. Please try again.', 'error')

    return render_template('auth/set_password.html', user_name=user_name)


@auth_bp.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('login_candidates', None)
    session.pop('login_mobile', None)
    session.pop('must_set_password', None)
    session.pop('password_reset_flag_id', None)
    flash('You have been logged out', 'info')
    return redirect(url_for('auth.login'))
