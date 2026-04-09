"""Shared Supabase queries for gamification snapshot tables (period / user column fallbacks)."""

from datetime import datetime, timezone
import threading

PERIOD_FIELDS = ['period_key', 'period', 'month_key', 'month']
USER_FIELDS = ['leader_user_id', 'user_id', 'leader_id']
SCORE_FIELDS = ['total_score', 'score', 'exp']

SNAPSHOT_TABLE_CANDIDATES = [
    'leader_gamification_snapshots',
    'leaderboard_snapshots',
    'leader_gamification_snapshot',
]

_CACHE_LOCK = threading.Lock()
# (candidates, has_period, has_user, score_order) -> dict describing first successful query shape
_snapshot_path_cache = {}


def month_bounds_utc(period_key):
    year, month = map(int, period_key.split('-'))
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _cache_key(candidates, period_key, user_id, score_order):
    return (tuple(candidates), bool(period_key), bool(user_id), bool(score_order))


def _run_attempt(
    supabase,
    table_name,
    select,
    period_key,
    user_id,
    limit,
    p_field,
    u_field,
    score_field,
    month_start,
    month_end,
    range_mode,
):
    """
    range_mode: None (use p_field eq), 'period_start', or 'period_end' for date-range filters.
    """
    try:
        query = supabase.table(table_name).select(select)
        if range_mode is None:
            if p_field:
                query = query.eq(p_field, period_key)
        elif range_mode == 'period_start':
            if month_start and month_end:
                query = query.gte('period_start', month_start).lt('period_start', month_end)
        elif range_mode == 'period_end':
            if month_start and month_end:
                query = query.gte('period_end', month_start).lt('period_end', month_end)
        if u_field:
            query = query.eq(u_field, user_id)
        if score_field:
            query = query.order(score_field, desc=True)
        if limit:
            query = query.limit(limit)
        result = query.execute()
        return result.data or []
    except Exception:
        return None


def _attempt_from_cache(supabase, cached, select, period_key, user_id, limit, month_start, month_end):
    return _run_attempt(
        supabase,
        cached['table'],
        select,
        period_key,
        user_id,
        limit,
        cached.get('p_field'),
        cached.get('u_field'),
        cached.get('score_field'),
        month_start,
        month_end,
        cached.get('range_mode'),
    )


def query_with_fallback_filters(
    supabase,
    candidates,
    select='*',
    period_key=None,
    user_id=None,
    limit=None,
    score_order=False,
):
    """
    Query candidate tables with alternate period/user column names.
    Returns (rows, table_name) for first successful non-empty match.
    Caches the winning query shape per process to avoid repeated probe latency.
    """
    if not supabase:
        return [], None

    period_fields = PERIOD_FIELDS if period_key else [None]
    user_fields = USER_FIELDS if user_id else [None]
    month_start = None
    month_end = None
    if period_key:
        start_dt, end_dt = month_bounds_utc(period_key)
        month_start = start_dt.date().isoformat()
        month_end = end_dt.date().isoformat()

    key = _cache_key(candidates, period_key, user_id, score_order)
    with _CACHE_LOCK:
        cached = _snapshot_path_cache.get(key)

    if cached:
        rows = _attempt_from_cache(
            supabase, cached, select, period_key, user_id, limit, month_start, month_end
        )
        if rows is not None:
            return rows, cached['table']
        with _CACHE_LOCK:
            _snapshot_path_cache.pop(key, None)

    score_iter = SCORE_FIELDS if score_order else [None]

    for table_name in candidates:
        for p_field in period_fields:
            for u_field in user_fields:
                for score_field in score_iter:
                    rows = _run_attempt(
                        supabase,
                        table_name,
                        select,
                        period_key,
                        user_id,
                        limit,
                        p_field,
                        u_field,
                        score_field,
                        month_start,
                        month_end,
                        None,
                    )
                    if rows is None:
                        continue
                    if rows:
                        strat = {
                            'table': table_name,
                            'p_field': p_field,
                            'u_field': u_field,
                            'score_field': score_field,
                            'range_mode': None,
                        }
                        with _CACHE_LOCK:
                            _snapshot_path_cache[key] = strat
                        return rows, table_name

        for u_field in user_fields:
            for score_field in score_iter:
                for range_mode in ('period_start', 'period_end'):
                    rows = _run_attempt(
                        supabase,
                        table_name,
                        select,
                        period_key,
                        user_id,
                        limit,
                        None,
                        u_field,
                        score_field,
                        month_start,
                        month_end,
                        range_mode,
                    )
                    if rows is None:
                        continue
                    if rows:
                        strat = {
                            'table': table_name,
                            'p_field': None,
                            'u_field': u_field,
                            'score_field': score_field,
                            'range_mode': range_mode,
                        }
                        with _CACHE_LOCK:
                            _snapshot_path_cache[key] = strat
                        return rows, table_name

    return [], None
