from flask import Blueprint, request, jsonify, session
from functools import wraps
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import os
import re
import threading
from supabase import create_client, Client
from dotenv import load_dotenv
from utils.leaderboard_snapshot_query import (
    month_bounds_utc,
    query_with_fallback_filters,
    SNAPSHOT_TABLE_CANDIDATES,
)

load_dotenv()

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_ANON_KEY')
if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None

api_bp = Blueprint('api', __name__, url_prefix='/api')
PERIOD_REGEX = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Portal schema (leader_gamification_schema.sql) uses *_defs and leader_gamification_achievements;
# longer *_definitions / *_achievements_period names kept as fallbacks.
TASK_DEFINITION_TABLES = [
    'leader_gamification_task_defs',
    'leader_gamification_task_definitions',
    'gamification_task_definitions',
]
TASK_PROGRESS_TABLES = [
    'leader_gamification_task_progress',
    'gamification_task_progress',
]
ACHIEVEMENT_DEFINITION_TABLES = [
    'leader_gamification_achievement_defs',
    'leader_gamification_achievement_definitions',
    'gamification_achievement_definitions',
]
ACHIEVEMENT_EARNED_TABLES = [
    'leader_gamification_achievements',
    'leader_gamification_achievements_period',
    'gamification_achievements_period',
]

_FIRST_TABLE_LOCK = threading.Lock()
# tuple(candidates) -> table_name that returned rows (skips probing other tables)
_first_table_hit_cache = {}


def _achievement_defs_by_id(defs):
    by = {}
    for d in defs or []:
        rid = d.get('id')
        if rid is not None:
            by[str(rid)] = d
    return by


def enrich_earned_achievements_with_defs(earned_rows, defs):
    """Attach name/label/icon from catalog rows when earned rows only store FKs."""
    ref = _achievement_defs_by_id(defs)
    out = []
    for row in earned_rows or []:
        fk = (
            row.get('achievement_def_id')
            or row.get('achievement_id')
            or row.get('def_id')
            or row.get('leader_gamification_achievement_def_id')
        )
        base = ref.get(str(fk)) if fk is not None else {}
        name = row.get('name') or base.get('name') or base.get('title')
        label = row.get('label') or base.get('label') or base.get('name') or base.get('title')
        icon = row.get('icon') or base.get('icon')
        merged = dict(row)
        merged['name'] = name
        merged['label'] = label
        merged['title'] = row.get('title') or base.get('title') or name
        if icon:
            merged['icon'] = icon
        out.append(merged)
    return out


def login_required(f):
    """Decorator to require login for API endpoints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'success': False, 'message': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function


def leaderboard_access_required(f):
    """Require a logged-in leader with gamification visibility."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        user = session.get('user', {})
        permissions = user.get('permissions') or []
        has_explicit_permission = (
            'leader_gamification.view' in permissions
            or 'leaderboard.view' in permissions
        )
        role_id = user.get('role_id')
        is_leader_role = role_id == 4

        # If permissions are present in session, enforce them.
        # Gap note: legacy backend sessions may not yet expose explicit permissions.
        # Until `leader_gamification.view` is consistently attached to session/JWT,
        # leader role fallback keeps the mobile leaderboard usable.
        if permissions:
            allowed = has_explicit_permission
        else:
            # Backward compatibility for older session payloads.
            allowed = is_leader_role

        if not allowed:
            return jsonify({'success': False, 'message': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return decorated_function


def current_period_key():
    return datetime.now(timezone.utc).strftime('%Y-%m')


def normalize_period_key(raw_period):
    period = (raw_period or '').strip()
    if not period:
        period = current_period_key()
    if not PERIOD_REGEX.match(period):
        return None
    return period


def query_first_available(candidates, select='*', eq_filters=None, order_by=None, limit=None):
    """
    Try candidate tables until one succeeds.
    Prefers the first table that returns rows so an empty first table does not
    hide data in a fallback table (e.g. task / achievement definition names).
    Returns (rows, table_name_used) or ([], None).
    """
    if not supabase:
        return [], None

    eq_filters = eq_filters or []
    cache_key = tuple(candidates)

    def _query_table(table_name):
        query = supabase.table(table_name).select(select)
        for key, value in eq_filters:
            query = query.eq(key, value)
        if order_by:
            query = query.order(order_by[0], desc=order_by[1])
        if limit:
            query = query.limit(limit)
        result = query.execute()
        return result.data or [], table_name

    with _FIRST_TABLE_LOCK:
        preferred = _first_table_hit_cache.get(cache_key)
    if preferred:
        try:
            rows, table_name = _query_table(preferred)
            if rows:
                return rows, table_name
        except Exception:
            with _FIRST_TABLE_LOCK:
                _first_table_hit_cache.pop(cache_key, None)

    first_ok = None
    for table_name in candidates:
        try:
            rows, tn = _query_table(table_name)
            if first_ok is None:
                first_ok = (rows, tn)
            if rows:
                with _FIRST_TABLE_LOCK:
                    _first_table_hit_cache[cache_key] = tn
                return rows, tn
        except Exception:
            continue
    return first_ok if first_ok is not None else ([], None)


def normalize_top_item(row):
    return {
        'rank': row.get('rank') or row.get('leaderboard_rank'),
        'leader_user_id': row.get('leader_user_id') or row.get('user_id') or row.get('leader_id'),
        'display_name': row.get('display_name') or row.get('name') or 'Leader',
        'avatar_url': row.get('avatar_url') or row.get('photo_url'),
        'total_score': row.get('total_score') or row.get('score') or row.get('exp') or 0,
        'tier': row.get('tier') or row.get('current_tier') or 'Unranked'
    }


def _users_by_id_batch(user_ids):
    """Fetch users by id; tolerate missing optional columns."""
    if not supabase or not user_ids:
        return {}
    for cols in ('id,name,avatar_url,photo_url', 'id,name'):
        try:
            res = supabase.table('users').select(cols).in_('id', user_ids).execute()
            return {str(r['id']): r for r in (res.data or [])}
        except Exception:
            continue
    return {}


def _leaders_by_id_batch(leader_ids):
    if not supabase or not leader_ids:
        return {}
    try:
        res = supabase.table('leaders').select('id,user_id,name').in_('id', leader_ids).execute()
        return {str(r['id']): r for r in (res.data or [])}
    except Exception:
        return {}


def enrich_leaderboard_items_from_users(items):
    """
    Snapshot rows often only store leader_user_id and scores. Fill display_name and avatar_url
    from users (or leaders + users when the id is a leaders.id).
    """
    if not supabase or not items:
        return items

    raw_ids = []
    seen = set()
    for it in items:
        uid = it.get('leader_user_id')
        if uid is None:
            continue
        key = str(uid)
        if key not in seen:
            seen.add(key)
            raw_ids.append(uid)

    if not raw_ids:
        return items

    by_user = _users_by_id_batch(raw_ids)
    by_leader = _leaders_by_id_batch(raw_ids)

    follow_user_ids = []
    for it in items:
        uid = it.get('leader_user_id')
        if uid is None:
            continue
        sk = str(uid)
        if sk in by_user:
            continue
        lr = by_leader.get(sk)
        if lr and lr.get('user_id'):
            follow_user_ids.append(lr['user_id'])
    follow_user_ids = list(dict.fromkeys(follow_user_ids))
    by_user_extra = _users_by_id_batch(follow_user_ids) if follow_user_ids else {}

    for it in items:
        uid = it.get('leader_user_id')
        if uid is None:
            continue
        sk = str(uid)
        name = None
        avatar = None
        u = by_user.get(sk)
        if u:
            name = (u.get('name') or '').strip()
            avatar = u.get('avatar_url') or u.get('photo_url')
        else:
            lr = by_leader.get(sk)
            if lr:
                name = (lr.get('name') or '').strip()
                u2 = by_user_extra.get(str(lr['user_id'])) if lr.get('user_id') else None
                if u2:
                    if not name:
                        name = (u2.get('name') or '').strip()
                    if not avatar:
                        avatar = u2.get('avatar_url') or u2.get('photo_url')
        if name:
            it['display_name'] = name
        if avatar and not it.get('avatar_url'):
            it['avatar_url'] = avatar

    return items


@api_bp.route('/user')
@login_required
def get_user():
    return jsonify({'success': True, 'user': session['user']})


@api_bp.route('/health')
def health_check():
    return jsonify({'success': True, 'status': 'healthy', 'message': 'API is running'})


@api_bp.route('/test')
@login_required
def test_endpoint():
    return jsonify({'success': True, 'message': 'This is a protected endpoint', 'user_id': session['user']['id']})


@api_bp.route('/leaderboard/periods')
@leaderboard_access_required
def leaderboard_periods():
    period_key = current_period_key()
    return jsonify({
        'success': True,
        'period_key': period_key,
        'supported_format': 'YYYY-MM',
        'supports_only_calendar_month': True,
        'periods': [period_key]
    })


@api_bp.route('/leaderboard/me')
@leaderboard_access_required
def leaderboard_me():
    if not supabase:
        return jsonify({'success': False, 'message': 'Supabase client is not configured'}), 500

    period_key = normalize_period_key(request.args.get('period'))
    if not period_key:
        return jsonify({'success': False, 'message': 'Invalid period format. Use YYYY-MM'}), 400

    user = session.get('user', {})
    user_id = user.get('id')
    period_start, period_end = month_bounds_utc(period_key)

    def _load_snapshot():
        return query_with_fallback_filters(
            supabase,
            SNAPSHOT_TABLE_CANDIDATES,
            select='*',
            period_key=period_key,
            user_id=user_id,
            limit=1,
        )

    def _load_task_defs():
        return query_first_available(
            candidates=TASK_DEFINITION_TABLES,
            select='*',
            eq_filters=[],
        )

    def _load_task_progress():
        return query_with_fallback_filters(
            supabase,
            TASK_PROGRESS_TABLES,
            select='*',
            period_key=period_key,
            user_id=user_id,
        )

    def _load_achievement_defs():
        return query_first_available(
            candidates=ACHIEVEMENT_DEFINITION_TABLES,
            select='*',
            eq_filters=[],
        )

    def _load_achievements_raw():
        return query_with_fallback_filters(
            supabase,
            ACHIEVEMENT_EARNED_TABLES,
            select='*',
            period_key=period_key,
            user_id=user_id,
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        f_snap = pool.submit(_load_snapshot)
        f_td = pool.submit(_load_task_defs)
        f_tp = pool.submit(_load_task_progress)
        f_ad = pool.submit(_load_achievement_defs)
        f_ar = pool.submit(_load_achievements_raw)
        snapshot_rows, _ = f_snap.result()
        task_defs, _ = f_td.result()
        task_progress, _ = f_tp.result()
        achievement_defs_total, _ = f_ad.result()
        achievements_raw, _ = f_ar.result()

    snapshot = snapshot_rows[0] if snapshot_rows else None
    achievements_period = enrich_earned_achievements_with_defs(
        achievements_raw,
        achievement_defs_total
    )

    payload = {
        'success': True,
        'period_key': period_key,
        'period_start': period_start.date().isoformat(),
        'period_end': (period_end.date()).isoformat(),
        'leader_user_id': user_id,
        'display_name': (snapshot or {}).get('display_name') or user.get('name') or 'Leader',
        'avatar_url': (snapshot or {}).get('avatar_url'),
        'rank': (snapshot or {}).get('rank'),
        'total_score': (snapshot or {}).get('total_score') or (snapshot or {}).get('score') or (snapshot or {}).get('exp') or 0,
        'tier': (snapshot or {}).get('tier') or 'Unranked',
        'breakdown': (snapshot or {}).get('breakdown') or {},
        'task_definitions': task_defs,
        'task_progress': task_progress,
        'achievements_period': achievements_period,
        'achievement_defs_total': achievement_defs_total
    }
    return jsonify(payload)


@api_bp.route('/leaderboard/top')
@leaderboard_access_required
def leaderboard_top():
    if not supabase:
        return jsonify({'success': False, 'message': 'Supabase client is not configured'}), 500

    period_key = normalize_period_key(request.args.get('period'))
    if not period_key:
        return jsonify({'success': False, 'message': 'Invalid period format. Use YYYY-MM'}), 400

    try:
        limit = int(request.args.get('limit', 10))
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid limit'}), 400
    limit = max(1, min(limit, 50))

    rows, _ = query_with_fallback_filters(
        supabase,
        SNAPSHOT_TABLE_CANDIDATES,
        select='*',
        period_key=period_key,
        limit=limit,
        score_order=True
    )

    normalized = []
    for idx, row in enumerate(rows, start=1):
        item = normalize_top_item(row)
        if not item['rank']:
            item['rank'] = idx
        normalized.append(item)

    enrich_leaderboard_items_from_users(normalized)

    return jsonify({'success': True, 'period_key': period_key, 'leaders': normalized})


@api_bp.route('/leaderboard/podium')
@leaderboard_access_required
def leaderboard_podium():
    if not supabase:
        return jsonify({'success': False, 'message': 'Supabase client is not configured'}), 500

    period_key = normalize_period_key(request.args.get('period'))
    if not period_key:
        return jsonify({'success': False, 'message': 'Invalid period format. Use YYYY-MM'}), 400

    rows, _ = query_with_fallback_filters(
        supabase,
        SNAPSHOT_TABLE_CANDIDATES,
        select='*',
        period_key=period_key,
        limit=3,
        score_order=True
    )

    by_rank = {}
    for idx, row in enumerate(rows, start=1):
        item = normalize_top_item(row)
        rank = item['rank'] or idx
        by_rank[rank] = item

    enrich_leaderboard_items_from_users([v for v in by_rank.values() if v])

    return jsonify({
        'success': True,
        'period_key': period_key,
        'podium': [by_rank.get(2), by_rank.get(1), by_rank.get(3)]
    })


@api_bp.route('/leaderboard/tasks', methods=['PATCH'])
@leaderboard_access_required
def leaderboard_tasks_patch():
    if not supabase:
        return jsonify({'success': False, 'message': 'Supabase client is not configured'}), 500

    body = request.get_json(silent=True) or {}
    task_def_id = body.get('task_def_id')
    status = body.get('status')
    period_key = normalize_period_key(body.get('period'))
    if not task_def_id or not status or not period_key:
        return jsonify({'success': False, 'message': 'task_def_id, period and status are required'}), 400

    user_id = session.get('user', {}).get('id')
    payload = {
        'leader_user_id': user_id,
        'task_def_id': task_def_id,
        'period_key': period_key,
        'status': status
    }

    last_error = None
    for table_name in TASK_PROGRESS_TABLES:
        try:
            result = supabase.table(table_name).upsert(payload).execute()
            return jsonify({'success': True, 'period_key': period_key, 'task_progress': result.data[0] if result.data else payload})
        except Exception as exc:
            last_error = str(exc)
            continue

    return jsonify({'success': False, 'message': f'Unable to update task progress: {last_error or "No task table found"}'}), 500
