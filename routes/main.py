from flask import Blueprint, render_template, session, redirect, url_for, flash, request, jsonify
from supabase import create_client, Client
from datetime import datetime, timedelta, timezone, date, time
import os
import hashlib
import re
import traceback
from dotenv import load_dotenv
from utils.activity_logger import log_activity
from utils.device_detector import get_template_suffix
from utils.leaderboard_snapshot_query import query_with_fallback_filters, SNAPSHOT_TABLE_CANDIDATES
from utils.tutorials_access import (
    build_weekly_tutorial_dashboard_rows,
    fetch_leader_cell_category,
)
from utils.app_time import app_now, app_today, get_app_tz
# Load environment variables
load_dotenv()
# Supabase configuration
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_ANON_KEY')
if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None

# PostgREST table for per-meeting visitor counts (Supabase: public.attendance_visitor_counts).
ATTENDANCE_VISITOR_COUNTS_TABLE = 'attendance_visitor_counts'

# Leader finalized bulk submit for a meeting week (see database/migrations/create_attendance_submissions_table.sql).
ATTENDANCE_SUBMISSIONS_TABLE = 'attendance_submissions'


def ensure_leader_self_member_row(leader_id):
    """Idempotent safety net: make sure the leader has a cell_members row with
    is_leader=TRUE so their own attendance can be marked. Returns the row id or None.
    Fails silently: the DB migration already handles this for existing leaders;
    this only covers leaders onboarded after the migration."""
    if not supabase or not leader_id:
        return None
    try:
        existing = (
            supabase.table('cell_members')
            .select('id')
            .eq('leader_id', leader_id)
            .eq('is_leader', True)
            .limit(1)
            .execute()
        )
        if existing.data and len(existing.data) > 0:
            return existing.data[0].get('id')

        user_row = {}
        try:
            u_res = (
                supabase.table('users')
                .select('name, phone_number, country, branch_id, created_at')
                .eq('id', leader_id)
                .limit(1)
                .execute()
            )
            if u_res.data and len(u_res.data) > 0:
                user_row = u_res.data[0] or {}
        except Exception as ue:
            print(f"ensure_leader_self_member_row users lookup failed: {ue}")

        payload = {
            'leader_id': leader_id,
            'name': user_row.get('name') or 'Cell Leader',
            'phone_number': user_row.get('phone_number'),
            'country': user_row.get('country'),
            'branch_id': user_row.get('branch_id'),
            'is_leader': True,
        }
        if user_row.get('created_at'):
            payload['created_at'] = user_row['created_at']

        ins = supabase.table('cell_members').insert(payload).execute()
        if ins.data and len(ins.data) > 0:
            return ins.data[0].get('id')
    except Exception as e:
        print(f"ensure_leader_self_member_row skipped for {leader_id}: {e}")
    return None

# Helper function to get user's created_at date
def get_user_created_date(user_id):
    """Get the user's created_at date from users table"""
    try:
        user_result = supabase.table('users')\
            .select('created_at')\
            .eq('id', user_id)\
            .execute()
        
        if user_result.data and len(user_result.data) > 0:
            user_created_at_str = user_result.data[0].get('created_at')
            if user_created_at_str:
                # Parse the created_at timestamp
                if isinstance(user_created_at_str, str):
                    try:
                        user_created_at = datetime.fromisoformat(user_created_at_str.replace('Z', '+00:00'))
                    except ValueError:
                        try:
                            user_created_at = datetime.strptime(user_created_at_str, "%Y-%m-%dT%H:%M:%S.%f")
                        except ValueError:
                            user_created_at = datetime.strptime(user_created_at_str.split('T')[0], "%Y-%m-%d")
                else:
                    user_created_at = user_created_at_str
                # Extract just the date part for comparison with meeting_date
                return user_created_at.date() if hasattr(user_created_at, 'date') else user_created_at
    except Exception as e:
        print(f"Error fetching user created_at: {e}")
    return None


def get_leader_location_context(leader_id):
    """Return leader branch/country values used by member forms."""
    leader_branch_id = None
    leader_country = None
    leader_branch_name = None
    try:
        user_result = (
            supabase.table('users')
            .select('branch_id, country')
            .eq('id', leader_id)
            .limit(1)
            .execute()
        )
        if user_result.data and len(user_result.data) > 0:
            leader_branch_id = user_result.data[0].get('branch_id')
            leader_country = user_result.data[0].get('country')
            if leader_branch_id is not None:
                try:
                    branch_result = (
                        supabase.table('branches')
                        .select('name')
                        .eq('id', leader_branch_id)
                        .limit(1)
                        .execute()
                    )
                    if branch_result.data and len(branch_result.data) > 0:
                        leader_branch_name = branch_result.data[0].get('name')
                except Exception as branch_error:
                    print(f"Error fetching leader branch name: {branch_error}")
    except Exception as e:
        print(f"Error fetching leader location context: {e}")
    return leader_branch_id, leader_country, leader_branch_name


def _tutorial_resource_url(tutorial):
    """First non-empty URL on a tutorial row (legacy helpers / dashboards)."""
    if not tutorial or not isinstance(tutorial, dict):
        return ''
    raw = (
        tutorial.get('pdf_url')
        or tutorial.get('pdf_url_1')
        or tutorial.get('video_url_1')
        or tutorial.get('pdf_url_2')
        or tutorial.get('video_url_2')
        or tutorial.get('pdf_url_3')
        or tutorial.get('video_url_3')
        or tutorial.get('file_url')
        or tutorial.get('url')
        or tutorial.get('link')
        or tutorial.get('document_url')
        or tutorial.get('video_url')
        or tutorial.get('attachment_url')
        or tutorial.get('file_path')
    )
    if raw is None:
        return ''
    return str(raw).strip()


def load_tutorials_for_meeting_day(meeting_date_formatted, parsed_date, cell_category=None):
    """Rows for that meeting day and leader cell category. Uses exact match first, then a day range."""
    if not supabase or not cell_category:
        return []
    result = (
        supabase.table('tutorials')
        .select('*')
        .eq('meeting_date', meeting_date_formatted)
        .eq('cell_category', cell_category)
        .execute()
    )
    rows = result.data or []
    if rows or not parsed_date:
        return rows
    day_start = parsed_date.isoformat()
    day_end = (parsed_date + timedelta(days=1)).isoformat()
    result2 = (
        supabase.table('tutorials')
        .select('*')
        .eq('cell_category', cell_category)
        .gte('meeting_date', day_start)
        .lt('meeting_date', day_end)
        .execute()
    )
    return result2.data or []


def _str_url(val):
    if val is None:
        return ''
    return str(val).strip()


def _pdf_url_for_slot(row, i):
    """pdf_url slots: 1=Sinhala (pdf_url then pdf_url_1), 2=English, 3=Tamil."""
    if not isinstance(row, dict):
        return ''
    if i == 1:
        u = _str_url(row.get('pdf_url'))
        if not u:
            u = _str_url(row.get('pdf_url_1'))
        return u
    return _str_url(row.get(f'pdf_url_{i}'))


def _video_url_for_slot(row, i):
    """video_url slots: 1=Sinhala (video_url_1 then video_url), 2=English, 3=Tamil."""
    if not isinstance(row, dict):
        return ''
    if i == 1:
        u = _str_url(row.get('video_url_1'))
        if not u:
            u = _str_url(row.get('video_url'))
        return u
    return _str_url(row.get(f'video_url_{i}'))


def _classify_tutorial_media(url):
    """Return 'video', 'pdf', or 'other' for grouping on the meeting tutorials page."""
    if not url:
        return 'other'
    u = url.lower()
    video_markers = (
        'youtube.com',
        'youtu.be',
        'vimeo.com',
        'facebook.com/watch',
        'tiktok.com',
        'loom.com',
        'wistia.com',
        '.mp4',
        '.webm',
        '.m3u8',
        'stream.',
    )
    if any(m in u for m in video_markers):
        return 'video'
    if u.endswith('.pdf') or '.pdf?' in u or '/.pdf' in u:
        return 'pdf'
    return 'other'


def _tutorial_display_entry(row, url, title, description_fallback=None):
    """One card on the meeting tutorials page (template expects file_url + title fields)."""
    desc = row.get('description')
    if (not desc or not str(desc).strip()) and description_fallback:
        desc = description_fallback
    return {
        'file_url': url,
        'tutorial_name': title,
        'title': title,
        'meeting_date': row.get('meeting_date'),
        'description': desc,
        'uploaded_at': row.get('uploaded_at'),
    }


_YT_ID_RE = re.compile(
    r'(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})'
)
_YT_V_PARAM_RE = re.compile(r'[?&]v=([a-zA-Z0-9_-]{11})')


def youtube_video_id_from_url(url):
    """Return YouTube video id if URL is a recognizable YouTube link, else None."""
    if not url:
        return None
    u = str(url).strip()
    m = _YT_ID_RE.search(u)
    if m:
        return m.group(1)
    m = _YT_V_PARAM_RE.search(u)
    if m and 'youtube.com' in u.lower():
        return m.group(1)
    return None


def build_tutorial_language_blocks(rows):
    """Pair PDF + video per language: 1=Sinhala, 2=English, 3=Tamil.

    Slot 1 uses `pdf_url` / `pdf_url_1` and `video_url_1` / `video_url` when primary fields
    are empty. Merges first non-empty URL per slot across rows.
    """
    labels = {
        1: 'Sinhala tutorials',
        2: 'English tutorials',
        3: 'Tamil tutorials',
    }
    slot_short = {1: 'Sinhala', 2: 'English', 3: 'Tamil'}
    merged_pdf = {1: '', 2: '', 3: ''}
    merged_vid = {1: '', 2: '', 3: ''}
    meta_row = {1: None, 2: None, 3: None}

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for i in (1, 2, 3):
            pu = _pdf_url_for_slot(row, i)
            vu = _video_url_for_slot(row, i)
            if pu and not merged_pdf[i]:
                merged_pdf[i] = pu
            if vu and not merged_vid[i]:
                merged_vid[i] = vu
            if (pu or vu) and meta_row[i] is None:
                meta_row[i] = row

    blocks = []
    for i in (1, 2, 3):
        pdf_u = merged_pdf[i]
        vid_u = merged_vid[i]
        if not pdf_u and not vid_u:
            continue
        row = meta_row[i]
        if row is None:
            row = {}
        base = row.get('tutorial_name') or row.get('title') or 'Tutorial'
        desc_fb = base if base != 'Tutorial' else None

        pdf_entry = None
        if pdf_u:
            pdf_entry = _tutorial_display_entry(row, pdf_u, slot_short[i], description_fallback=desc_fb)

        video_entry = None
        if vid_u:
            video_entry = _tutorial_display_entry(row, vid_u, slot_short[i], description_fallback=desc_fb)
            yid = youtube_video_id_from_url(vid_u)
            video_entry['youtube_id'] = yid
            video_entry['youtube_embed_url'] = f'https://www.youtube.com/embed/{yid}' if yid else None
            video_entry['youtube_watch_url'] = (
                f'https://www.youtube.com/watch?v={yid}' if yid else vid_u
            )

        blocks.append({
            'title': labels[i],
            'slot': i,
            'pdf': pdf_entry,
            'video': video_entry,
        })
    return blocks


def build_tutorial_legacy_sections(rows):
    """Legacy columns only (file_url, pdf_url, …); skips URLs used by language slots.

    Slot 1 is Sinhala (pdf_url / pdf_url_1, video_url_1 / video_url).
    """
    pdfs, videos, other = [], [], []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        base = row.get('tutorial_name') or row.get('title') or 'Tutorial'
        seen_any_numbered = set()
        for i in (1, 2, 3):
            u = _str_url(row.get(f'pdf_url_{i}'))
            if u:
                seen_any_numbered.add(u)
            u = _str_url(row.get(f'video_url_{i}'))
            if u:
                seen_any_numbered.add(u)
            u = _pdf_url_for_slot(row, i)
            if u:
                seen_any_numbered.add(u)
            u = _video_url_for_slot(row, i)
            if u:
                seen_any_numbered.add(u)
        # Raw legacy English columns (avoid duplicate card when only pdf_url / video_url is set)
        u = _str_url(row.get('pdf_url'))
        if u:
            seen_any_numbered.add(u)
        u = _str_url(row.get('video_url'))
        if u:
            seen_any_numbered.add(u)

        legacy_keys = (
            'file_url',
            'pdf_url',
            'video_url',
            'url',
            'link',
            'document_url',
            'attachment_url',
            'file_path',
        )
        for key in legacy_keys:
            u = _str_url(row.get(key))
            if not u or u in seen_any_numbered:
                continue
            seen_any_numbered.add(u)
            kind = _classify_tutorial_media(u)
            stub = _tutorial_display_entry(row, u, base)
            if kind == 'video':
                videos.append(stub)
            elif kind == 'pdf':
                pdfs.append(stub)
            else:
                other.append(stub)

    sections = []
    if pdfs:
        sections.append({'kind': 'pdf', 'title': 'PDF documents', 'entries': pdfs})
    if videos:
        sections.append({'kind': 'video', 'title': 'Video tutorials', 'entries': videos})
    if other:
        sections.append({'kind': 'other', 'title': 'Other resources', 'entries': other})
    return sections


def _parse_meeting_date_value(meeting_date):
    """Parse meetings.tutorials-style meeting_date to a date; return None if invalid."""
    if meeting_date is None:
        return None
    try:
        if isinstance(meeting_date, datetime):
            return meeting_date.date()
        if isinstance(meeting_date, date):
            return meeting_date
        if isinstance(meeting_date, str):
            try:
                return datetime.strptime(meeting_date, "%Y-%m-%d").date()
            except ValueError:
                try:
                    return datetime.strptime(meeting_date, "%Y-%m-%dT%H:%M:%S").date()
                except ValueError:
                    return datetime.strptime(meeting_date.split('T')[0], "%Y-%m-%d").date()
    except Exception:
        return None
    return None


def _parse_created_at_to_date(val):
    """cell_members.created_at -> date for comparison with meeting_date."""
    if val is None:
        return None
    try:
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace('Z', '+00:00')).date()
            except ValueError:
                return datetime.strptime(val.split('T')[0], "%Y-%m-%d").date()
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, date):
            return val
    except Exception:
        return None
    return None


# Helper function to convert user ID to UUID format
def get_uuid_from_user_id(user_id):
    """Get the leader_id from the leaders table based on user_id"""
    try:
        result = supabase.table('leaders').select('id').eq('user_id', user_id).execute()
        if result.data and len(result.data) > 0:
            return result.data[0]['id']
        else:
            # If no leader found, create one with a generated UUID
            print(f"No leader found for user_id: {user_id}, creating one...")
            import uuid
            leader_id = str(uuid.uuid4())
            leader_data = {
                'id': leader_id,
                'user_id': user_id,
                'name': 'Cell Leader',
                'email': ''
            }
            result = supabase.table('leaders').insert(leader_data).execute()
            if result.data:
                return result.data[0]['id']
            else:
                raise Exception("Failed to create leader record")
    except Exception as e:
        print(f"Error getting leader_id for user_id {user_id}: {e}")
        # Fallback to old method for backward compatibility
        hash_object = hashlib.md5(user_id.encode())
        hex_digest = hash_object.hexdigest()
        return f"{hex_digest[:8]}-{hex_digest[8:12]}-{hex_digest[12:16]}-{hex_digest[16:20]}-{hex_digest[20:32]}"
# Create blueprint
main_bp = Blueprint('main', __name__)

def get_effective_leader_id():
    """Return the leader id for the current session. Leaders: own id. Deputies: leader_id from cell_members (no users row)."""
    if 'user' not in session:
        return None
    u = session['user']
    if u.get('is_deputy'):
        return u.get('leader_id')
    return u.get('id')

def redirect_deputy_to_attendance():
    """If current user is a deputy, return redirect to meeting_dates (attendance); else None."""
    if session.get('user', {}).get('is_deputy'):
        return redirect(url_for('main.meeting_dates'))
    return None


def pending_flagged_by_member_id(supabase_client, leader_id, member_ids):
    """
    Map member_id -> pending delete_request, deputy_removal_request, and/or other flags.
    issue_type deputy_removal_request targets the current deputy's cell_members.id.
    """
    ids = [str(x) for x in member_ids if x is not None]
    mapping = {
        mid: {
            'delete_request_pending': False,
            'flag_issue_pending': False,
            'deputy_removal_pending': False,
        }
        for mid in ids
    }
    if not supabase_client or not leader_id or not ids:
        return mapping
    try:
        res = (
            supabase_client.table('flagged_issues')
            .select('member_id,issue_type')
            .eq('leader_id', str(leader_id))
            .eq('status', 'pending')
            .in_('member_id', ids)
            .execute()
        )
        for row in res.data or []:
            mid = str(row.get('member_id') or '')
            if mid not in mapping:
                continue
            it = (row.get('issue_type') or '').strip()
            if it == 'delete_request':
                mapping[mid]['delete_request_pending'] = True
            elif it == 'deputy_removal_request':
                mapping[mid]['deputy_removal_pending'] = True
            else:
                mapping[mid]['flag_issue_pending'] = True
    except Exception as e:
        print(f"Error loading pending flagged_issues for leader {leader_id}: {e}")
    return mapping


def pending_flagged_state_for_member(supabase_client, leader_id, member_id):
    """Single-member pending delete, deputy removal request, and flag state from flagged_issues."""
    delete_request_pending = False
    flag_issue_pending = False
    deputy_removal_pending = False
    if not supabase_client or not leader_id or not member_id:
        return delete_request_pending, flag_issue_pending, deputy_removal_pending
    try:
        res = (
            supabase_client.table('flagged_issues')
            .select('issue_type')
            .eq('member_id', str(member_id))
            .eq('leader_id', str(leader_id))
            .eq('status', 'pending')
            .execute()
        )
        for row in res.data or []:
            it = (row.get('issue_type') or '').strip()
            if it == 'delete_request':
                delete_request_pending = True
            elif it == 'deputy_removal_request':
                deputy_removal_pending = True
            else:
                flag_issue_pending = True
    except Exception as e:
        print(f"Error checking pending flagged_issues for member {member_id}: {e}")
    return delete_request_pending, flag_issue_pending, deputy_removal_pending


def leader_has_pending_deputy_removal_request(supabase_client, leader_id):
    """True if this leader already has a pending deputy_removal_request (any member row)."""
    if not supabase_client or not leader_id:
        return False
    try:
        r = (
            supabase_client.table('flagged_issues')
            .select('id')
            .eq('leader_id', str(leader_id))
            .eq('issue_type', 'deputy_removal_request')
            .eq('status', 'pending')
            .limit(1)
            .execute()
        )
        return bool(r.data)
    except Exception as e:
        print(f"Error checking pending deputy removal for leader {leader_id}: {e}")
        return False


def _row_score(row):
    if not row:
        return 0
    return row.get('total_score') or row.get('score') or row.get('exp') or 0


def _row_user_key(row):
    if not row:
        return None
    return row.get('leader_user_id') or row.get('user_id') or row.get('leader_id')


def get_dashboard_leaderboard_stats(leader_id):
    """
    Current calendar month (UTC) snapshot for the welcome-card pill.
    Uses the same Supabase fallbacks as /api/leaderboard; computes rank from
    ordered rows when the snapshot has no rank column.
    """
    default = {'rank': None, 'total_score': 0, 'position': '-'}
    if not supabase or not leader_id:
        return default
    try:
        period_key = datetime.now(timezone.utc).strftime('%Y-%m')
        rows_user, _ = query_with_fallback_filters(
            supabase,
            SNAPSHOT_TABLE_CANDIDATES,
            select='*',
            period_key=period_key,
            user_id=leader_id,
            limit=1,
            score_order=False,
        )
        lb_row = rows_user[0] if rows_user else None
        score_val = _row_score(lb_row)
        rank_val = None
        if lb_row:
            rank_val = lb_row.get('rank') if lb_row.get('rank') is not None else lb_row.get('leaderboard_rank')

        need_period_list = lb_row is None or rank_val is None
        if need_period_list:
            ranked_rows, _ = query_with_fallback_filters(
                supabase,
                SNAPSHOT_TABLE_CANDIDATES,
                select='*',
                period_key=period_key,
                user_id=None,
                limit=200,
                score_order=True,
            )
            for idx, row in enumerate(ranked_rows or [], start=1):
                rid = _row_user_key(row)
                if rid is not None and str(rid) == str(leader_id):
                    if rank_val is None:
                        rank_val = idx
                    if lb_row is None:
                        score_val = _row_score(row)
                    break

        position = f'#{rank_val}' if rank_val is not None else '-'
        return {
            'rank': rank_val,
            'total_score': score_val,
            'position': position,
        }
    except Exception as e:
        print(f"Error loading leaderboard stats: {e}")
        return default


def get_tutorial_meeting_date_corrected():
    """Get the meeting date for tutorials - CORRECTED LOGIC:
    Tuesday 12:00 AM - 11:59 PM: Show current Tuesday
    Wednesday 12:00 AM: Switch to next Tuesday
    Wednesday - Monday: Show next Tuesday"""
    now = app_now()
    today = now.date()
    
    # Calculate days until next Tuesday
    days_until_tuesday = (1 - today.weekday()) % 7
    
    if days_until_tuesday == 0:  # Today is Tuesday
        # If it's Tuesday, check the time
        if now.hour == 23 and now.minute == 59:  # At 11:59 PM on Tuesday, switch to next Tuesday
            next_tuesday = today + timedelta(days=7)
        else:  # Before 11:59 PM on Tuesday, use current Tuesday
            next_tuesday = today
    else:
        # Wednesday through Monday, get the next Tuesday
        next_tuesday = today + timedelta(days=days_until_tuesday)
    
    return next_tuesday

def get_attendance_meeting_date_corrected():
    """Get the meeting date for attendance - CORRECTED LOGIC:
    Tuesday 12:00 AM - 11:59 PM: Show current Tuesday
    Wednesday 12:00 AM - Monday 11:59 PM: Show same Tuesday (current week)
    Tuesday 12:00 AM: Switch to new current Tuesday"""
    now = app_now()
    today = now.date()
    
    # Calculate days until next Tuesday
    days_until_tuesday = (1 - today.weekday()) % 7
    
    if days_until_tuesday == 0:  # Today is Tuesday
        # If it's Tuesday, always use current Tuesday for attendance
        attendance_tuesday = today
    elif days_until_tuesday == 1:  # Today is Monday
        # If it's Monday, check if it's before midnight (00:00)
        if now.hour == 0 and now.minute == 0:  # Exactly midnight
            # At Monday midnight, switch to next Tuesday
            attendance_tuesday = today + timedelta(days=1)
        else:
            # Before Monday midnight, use the same Tuesday from current week
            attendance_tuesday = today - timedelta(days=6)
    else:
        # Wednesday through Sunday, use the same Tuesday from current week
        # Calculate the most recent Tuesday (current week's Tuesday)
        if days_until_tuesday == 6:  # Wednesday
            attendance_tuesday = today - timedelta(days=1)  # Yesterday (Tuesday)
        elif days_until_tuesday == 5:  # Thursday
            attendance_tuesday = today - timedelta(days=2)  # 2 days ago (Tuesday)
        elif days_until_tuesday == 4:  # Friday
            attendance_tuesday = today - timedelta(days=3)  # 3 days ago (Tuesday)
        elif days_until_tuesday == 3:  # Saturday
            attendance_tuesday = today - timedelta(days=4)  # 4 days ago (Tuesday)
        elif days_until_tuesday == 2:  # Sunday
            attendance_tuesday = today - timedelta(days=5)  # 5 days ago (Tuesday)
    
    return attendance_tuesday


def get_attendance_opening_datetime(meeting_date):
    """First moment attendance may be marked for meeting_date (Tuesday): Tue 06:00:00 local."""
    return datetime.combine(meeting_date, time(6, 0, 0), tzinfo=get_app_tz())


def get_attendance_deadline(meeting_date):
    """
    Get the attendance deadline datetime for a meeting date.
    Deadline is Thursday 11:59:59 PM of the meeting week.

    Args:
        meeting_date: datetime.date object representing the Tuesday meeting date

    Returns:
        datetime: Deadline datetime (Thursday 11:59:59 PM)
    """
    meeting_thursday = meeting_date + timedelta(days=2)
    deadline = datetime.combine(
        meeting_thursday, time(23, 59, 59), tzinfo=get_app_tz()
    )
    return deadline


def get_member_attendance_cutoff_iso(meeting_date):
    """
    Members created up to the END of the meeting week (Thursday 23:59:59 local)
    are eligible for that week's attendance. This lets members added during the
    marking window (including same-day Tuesday adds) be marked present.

    Returns a timezone-aware ISO timestamp string for use in a Supabase
    .lte('created_at', ...) filter. Using an end-of-day timestamp avoids the
    bug where a bare date ('YYYY-MM-DD') is treated as midnight, which wrongly
    excluded members created later that same day.
    """
    return get_attendance_deadline(meeting_date).isoformat()


def get_member_attendance_cutoff_date(meeting_date):
    """Date form of the eligibility cutoff (the meeting week's Thursday)."""
    return get_attendance_deadline(meeting_date).date()


def member_created_within_attendance_window(member_created_at, meeting_date):
    """
    True if a member (by created_at) is eligible for meeting_date's attendance.
    Eligible when created on or before the meeting week's Thursday. Members with
    no created_at are included for backward compatibility.
    """
    if not member_created_at:
        return True
    created_date = _parse_created_at_to_date(member_created_at)
    if created_date is None:
        return True
    return created_date <= get_member_attendance_cutoff_date(meeting_date)


def get_attendance_marking_countdown_payload(meeting_date):
    """
    ISO timestamps for dashboard countdown: marking opens Tue 06:00 local,
    closes Thursday 23:59:59 local (same as get_attendance_deadline).
    If that window has already ended, rolls forward to the next Tuesday's window
    so the UI always shows an upcoming open/close pair.
    """
    if meeting_date is None:
        return None
    now = app_now()
    tuesday = meeting_date
    for _ in range(520):
        opens_at = get_attendance_opening_datetime(tuesday)
        closes_at = get_attendance_deadline(tuesday)
        if now <= closes_at:
            return {
                'opens_iso': opens_at.replace(microsecond=0).isoformat(),
                'closes_iso': closes_at.replace(microsecond=0).isoformat(),
            }
        tuesday = tuesday + timedelta(days=7)
    return None


def can_mark_attendance(meeting_date):
    """
    Check if attendance can be marked for a given meeting date.
    Window: Tuesday 06:00 through Thursday 23:59:59 local (meeting_date is Tuesday).

    Args:
        meeting_date: datetime.date object representing the Tuesday meeting date

    Returns:
        bool: True if attendance can be marked, False otherwise
    """
    now = app_now()
    opens = get_attendance_opening_datetime(meeting_date)
    deadline = get_attendance_deadline(meeting_date)
    return opens <= now <= deadline


def _normalize_meeting_date_iso_from_db(meeting_date_val):
    """Normalize PostgREST date / string to YYYY-MM-DD."""
    if meeting_date_val is None:
        return None
    if hasattr(meeting_date_val, 'isoformat'):
        return meeting_date_val.isoformat()
    s = str(meeting_date_val)
    return s[:10] if len(s) >= 10 else s


def fetch_submitted_meeting_dates(leader_id, meeting_dates_iso):
    """Return set of YYYY-MM-DD strings with a recorded bulk submission for this leader."""
    if not supabase or not leader_id or not meeting_dates_iso:
        return set()
    unique_dates = list({d for d in meeting_dates_iso if d})
    if not unique_dates:
        return set()
    submitted = set()
    chunk_size = 120
    try:
        for i in range(0, len(unique_dates), chunk_size):
            chunk = unique_dates[i : i + chunk_size]
            res = (
                supabase.table(ATTENDANCE_SUBMISSIONS_TABLE)
                .select('meeting_date')
                .eq('leader_id', str(leader_id))
                .in_('meeting_date', chunk)
                .execute()
            )
            for row in res.data or []:
                iso = _normalize_meeting_date_iso_from_db(row.get('meeting_date'))
                if iso:
                    submitted.add(iso)
    except Exception as e:
        print(f"fetch_submitted_meeting_dates: {e}")
        return set()
    return submitted


def attendance_edit_state(parsed_date, submitted_iso_set):
    """
    Whether this leader may edit attendance for parsed_date (bulk or per-member),
    plus UX hints. submitted_iso_set = set of YYYY-MM-DD with finalized bulk submit.
    """
    if parsed_date is None:
        return {
            'can_mark_attendance': False,
            'attendance_submitted': False,
            'locked_reason': None,
        }
    today = app_today()
    iso = parsed_date.isoformat()
    is_upcoming = parsed_date > today
    submitted = iso in submitted_iso_set

    if is_upcoming:
        return {
            'can_mark_attendance': False,
            'attendance_submitted': False,
            'locked_reason': 'upcoming',
        }
    if submitted:
        return {
            'can_mark_attendance': False,
            'attendance_submitted': True,
            'locked_reason': 'submitted',
        }
    if can_mark_attendance(parsed_date):
        return {
            'can_mark_attendance': True,
            'attendance_submitted': False,
            'locked_reason': None,
        }
    now = app_now()
    opens = get_attendance_opening_datetime(parsed_date)
    if now < opens:
        locked_reason = 'window_not_open'
    else:
        locked_reason = 'window_closed'
    return {
        'can_mark_attendance': False,
        'attendance_submitted': False,
        'locked_reason': locked_reason,
    }


def leader_can_mark_attendance(leader_id, parsed_date, submitted_iso_set=None):
    """Leader-specific: time window and not bulk-submitted for this meeting date."""
    if parsed_date is None:
        return False
    if submitted_iso_set is None:
        submitted_iso_set = fetch_submitted_meeting_dates(leader_id, [parsed_date.isoformat()])
    state = attendance_edit_state(parsed_date, submitted_iso_set)
    return state['can_mark_attendance']


def attendance_edit_denied_message(parsed_date, submitted_iso_set):
    """Human-readable reason when edits are not allowed."""
    state = attendance_edit_state(parsed_date, submitted_iso_set)
    if state['can_mark_attendance']:
        return None
    lr = state['locked_reason']
    if lr == 'submitted':
        return (
            'Attendance has already been submitted for this meeting and cannot be changed.'
        )
    if lr == 'upcoming':
        return 'Attendance opens during the meeting week (Tuesday 6:00 AM through Thursday 11:59 PM).'
    if lr == 'window_not_open':
        return 'Attendance opens Tuesday at 6:00 AM.'
    reminder_info = get_attendance_reminder_info(parsed_date)
    deadline_str = reminder_info['deadline_str'] if reminder_info else 'Thursday 11:59 PM'
    return (
        f'Attendance can only be marked from Tuesday 6:00 AM through {deadline_str}. '
        f'This week\'s attendance is now closed.'
    )


def get_attendance_eligible_member_id_strings(leader_id, parsed_date, meeting_date_formatted):
    """
    Member id strings that must be included in a bulk attendance submit
    (same scope as attendance_detail member list).
    """
    if not supabase or not leader_id or not meeting_date_formatted:
        return set()
    query = supabase.table('cell_members').select('*').eq('leader_id', leader_id)
    if parsed_date:
        # Use end-of-window (Thursday 23:59:59) timestamp, not a bare date, so
        # members added during the marking window are not dropped at the DB layer.
        query = query.lte('created_at', get_member_attendance_cutoff_iso(parsed_date))
    members_result = query.execute()
    members = members_result.data if members_result.data else []
    if parsed_date and members:
        members = [
            member for member in members
            if member_created_within_attendance_window(member.get('created_at'), parsed_date)
        ]
    out = set()
    for m in members:
        mid = m.get('id')
        if mid is not None:
            out.add(str(mid))
    return out


def record_attendance_week_submitted(leader_id, meeting_date_iso, submitted_by_user_id=None):
    """Persist finalized bulk submit for this leader + meeting week."""
    if not supabase or not leader_id or not meeting_date_iso:
        return False
    payload = {
        'leader_id': str(leader_id),
        'meeting_date': meeting_date_iso,
        'submitted_at': datetime.now(timezone.utc).isoformat(),
    }
    if submitted_by_user_id:
        payload['submitted_by_user_id'] = str(submitted_by_user_id)
    try:
        supabase.table(ATTENDANCE_SUBMISSIONS_TABLE).upsert(
            payload, on_conflict='leader_id,meeting_date'
        ).execute()
        return True
    except Exception as e:
        print(f"record_attendance_week_submitted: {e}")
        traceback.print_exc()
        return False


def enrich_meetings_with_attendance_eligibility(meetings, leader_id):
    """Add can_mark_attendance, attendance_submitted, locked_reason, calendar_upcoming per meeting row."""
    if not meetings:
        return
    today = app_today()
    all_iso = []
    for m in meetings:
        do = m.get('date_obj')
        if do is None and m.get('date_iso'):
            try:
                do = datetime.strptime(str(m['date_iso'])[:10], '%Y-%m-%d').date()
                m['date_obj'] = do
            except ValueError:
                pass
        if do is not None:
            all_iso.append(do.isoformat())
        m['calendar_upcoming'] = bool(do and do > today)

    submitted = fetch_submitted_meeting_dates(leader_id, all_iso) if leader_id else set()
    for m in meetings:
        do = m.get('date_obj')
        if do is None:
            m['can_mark_attendance'] = False
            m['attendance_submitted'] = False
            m['locked_reason'] = None
            m.setdefault('calendar_upcoming', False)
            continue
        st = attendance_edit_state(do, submitted)
        m['can_mark_attendance'] = st['can_mark_attendance']
        m['attendance_submitted'] = st['attendance_submitted']
        m['locked_reason'] = st['locked_reason']


def enrich_meetings_with_attendance_summary(meetings, leader_id):
    """
    Add present_count, absent_count and visitor_count per meeting so cards can
    show a submitted-attendance summary. Batched to avoid per-meeting queries.
    """
    if not meetings:
        return
    for m in meetings:
        m.setdefault('present_count', 0)
        m.setdefault('absent_count', 0)
        m.setdefault('visitor_count', 0)
        m.setdefault('has_attendance_summary', False)
    if not supabase or not leader_id:
        return

    isos = list({m['date_iso'] for m in meetings if m.get('date_iso')})
    if not isos:
        return

    present_by = {}
    absent_by = {}
    try:
        att_res = (
            supabase.table('attendance')
            .select('meeting_date,status')
            .eq('leader_id', str(leader_id))
            .in_('meeting_date', isos)
            .execute()
        )
        for row in att_res.data or []:
            iso = _normalize_meeting_date_iso_from_db(row.get('meeting_date'))
            if not iso:
                continue
            status = row.get('status')
            if status == 'present':
                present_by[iso] = present_by.get(iso, 0) + 1
            elif status == 'absent':
                absent_by[iso] = absent_by.get(iso, 0) + 1
    except Exception as e:
        print(f"enrich_meetings_with_attendance_summary attendance: {e}")

    visitor_by = {}
    try:
        vc_res = (
            supabase.table(ATTENDANCE_VISITOR_COUNTS_TABLE)
            .select('meeting_date,visitor_count')
            .eq('leader_user_id', str(leader_id))
            .in_('meeting_date', isos)
            .execute()
        )
        for row in vc_res.data or []:
            iso = _normalize_meeting_date_iso_from_db(row.get('meeting_date'))
            if not iso:
                continue
            try:
                visitor_by[iso] = int(row.get('visitor_count') or 0)
            except (TypeError, ValueError):
                visitor_by[iso] = 0
    except Exception as e:
        print(f"enrich_meetings_with_attendance_summary visitors: {e}")

    for m in meetings:
        iso = m.get('date_iso')
        if not iso:
            continue
        m['present_count'] = present_by.get(iso, 0)
        m['absent_count'] = absent_by.get(iso, 0)
        m['visitor_count'] = visitor_by.get(iso, 0)
        m['has_attendance_summary'] = (
            bool(m.get('attendance_submitted'))
            or iso in present_by
            or iso in absent_by
            or iso in visitor_by
        )


def get_attendance_reminder_info(meeting_date):
    """
    Get reminder information for attendance deadline.
    
    Args:
        meeting_date: datetime.date object representing the Tuesday meeting date
    
    Returns:
        dict: {
            'deadline': datetime object,
            'time_remaining': timedelta object,
            'hours_remaining': int,
            'minutes_remaining': int,
            'is_approaching': bool (True if less than 24 hours remaining),
            'is_urgent': bool (True if less than 6 hours remaining),
            'is_past_deadline': bool
        }
    """
    now = app_now()
    deadline = get_attendance_deadline(meeting_date)
    time_remaining = deadline - now
    
    hours_remaining = int(time_remaining.total_seconds() // 3600)
    minutes_remaining = int((time_remaining.total_seconds() % 3600) // 60)
    
    is_approaching = time_remaining.total_seconds() <= 24 * 3600 and time_remaining.total_seconds() > 0
    is_urgent = time_remaining.total_seconds() <= 6 * 3600 and time_remaining.total_seconds() > 0
    is_past_deadline = time_remaining.total_seconds() <= 0
    
    return {
        'deadline': deadline,
        'time_remaining': time_remaining,
        'hours_remaining': hours_remaining,
        'minutes_remaining': minutes_remaining,
        'is_approaching': is_approaching,
        'is_urgent': is_urgent,
        'is_past_deadline': is_past_deadline,
        'deadline_str': deadline.strftime('%B %d, %Y at %I:%M %p'),
        'deadline_iso': deadline.isoformat()  # For JavaScript Date parsing
    }

def get_past_tuesdays():
    """Calculate the past 4 Tuesday dates (including today if today is Tuesday)"""
    today = app_now()
    tuesdays = []
    # Find the most recent past Tuesday
    days_back = today.weekday() - 1  # Tuesday is 1
    if days_back < 0:  # If today is Monday (0), go back 6 days
        days_back += 7
    elif days_back == 0:  # If today is Tuesday, start from today (0 days back)
        days_back = 0
    else:  # If today is Wednesday or later, go back to the most recent Tuesday
        days_back = days_back
    # Get the 4 most recent past Tuesdays (including today if it's Tuesday)
    for i in range(4):
        tuesday = today - timedelta(days=days_back + (i * 7))
        tuesdays.append(tuesday.strftime("%B %d, %Y"))
    return tuesdays

@main_bp.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    r = redirect_deputy_to_attendance()
    if r:
        return r
    # Initialize default tutorial card data
    tutorial_card_data = {
        'upcoming_date': 'No date',
        'has_tutorials': False,
        'meeting_date_iso': None
    }
    
    if 'user' in session:
        # Get leader ID - use user ID directly
        leader_id = get_effective_leader_id()
        leaderboard_stats = get_dashboard_leaderboard_stats(leader_id)
        leader_cell_category = None
        try:
            leader_cell_category = fetch_leader_cell_category(supabase, leader_id)
        except Exception as wt_err:
            print(f"Error loading leader cell category: {wt_err}")

        next_meeting_date = get_tutorial_meeting_date_corrected()
        current_attendance_date = get_attendance_meeting_date_corrected()
        meetings_for_dashboard = []
        user_created_date_dashboard = None
        try:
            user_created_date_dashboard = get_user_created_date(leader_id)
            mq = supabase.table('meetings').select('*')
            if user_created_date_dashboard:
                mq = mq.gte('meeting_date', user_created_date_dashboard.isoformat())
            meetings_for_dashboard = (
                mq.order('meeting_date', desc=True).limit(4).execute()
            ).data or []
        except Exception:
            meetings_for_dashboard = []

        try:
            member_count = 0
            members_result = supabase.table('cell_members').select('id').eq('leader_id', leader_id).execute()
            member_count = len(members_result.data) if members_result.data else 0
            next_meeting_formatted = next_meeting_date.strftime('%B %d, %Y')

            tutorials_result_data = []
            has_tutorials = False
            try:
                if leader_cell_category:
                    tutorials_result = (
                        supabase.table('tutorials')
                        .select('*')
                        .eq('meeting_date', next_meeting_date.isoformat())
                        .eq('cell_category', leader_cell_category)
                        .execute()
                    )
                    tutorials_result_data = tutorials_result.data or []
                    has_tutorials = len(tutorials_result_data) > 0
            except Exception as e:
                print(f"Error checking tutorials: {e}")
                has_tutorials = False

            is_placeholder = False
            if has_tutorials and tutorials_result_data:
                tutorial_record = tutorials_result_data[0]
                is_placeholder = (
                    tutorial_record.get('tutorial_name') == 'No Tutorial Uploaded'
                    or tutorial_record.get('title') == 'No Tutorial Uploaded'
                )

            tutorial_status = 'updated' if has_tutorials and not is_placeholder else 'not_updated'
            tutorial_card_data.update({
                'upcoming_date': next_meeting_formatted,
                'has_tutorials': has_tutorials,
                'is_placeholder': is_placeholder,
                'meeting_date_iso': next_meeting_date.isoformat(),
                'status': tutorial_status
            })

            tutorial_list = []
            try:
                today = app_today()
                parsed_slots = []
                for meeting in meetings_for_dashboard:
                    meeting_date = meeting.get('meeting_date')
                    parsed_date = _parse_meeting_date_value(meeting_date)
                    if not parsed_date:
                        continue
                    if user_created_date_dashboard and parsed_date < user_created_date_dashboard:
                        continue
                    parsed_slots.append({
                        'parsed_date': parsed_date,
                        'meeting_date_iso': parsed_date.isoformat(),
                    })
                date_isos = list({s['meeting_date_iso'] for s in parsed_slots})
                tutorials_by_iso = {}
                if date_isos and leader_cell_category:
                    try:
                        batch_tr = (
                            supabase.table('tutorials')
                            .select('*')
                            .in_('meeting_date', date_isos)
                            .eq('cell_category', leader_cell_category)
                            .execute()
                        )
                        for row in (batch_tr.data or []):
                            nk = _parse_meeting_date_value(row.get('meeting_date'))
                            if nk:
                                tutorials_by_iso.setdefault(nk.isoformat(), []).append(row)
                    except Exception:
                        pass
                for slot in parsed_slots:
                    parsed_date = slot['parsed_date']
                    meeting_date_iso = slot['meeting_date_iso']
                    rows_for_day = tutorials_by_iso.get(meeting_date_iso, [])
                    if not rows_for_day:
                        for alt_k, alt_rows in tutorials_by_iso.items():
                            if alt_k.startswith(meeting_date_iso) or meeting_date_iso.startswith(alt_k[:10]):
                                rows_for_day = alt_rows
                                break
                    has_tutorial = len(rows_for_day) > 0
                    tutorial_record = rows_for_day[0] if has_tutorial else None
                    is_placeholder_tutorial = False
                    if has_tutorial and tutorial_record:
                        is_placeholder_tutorial = (
                            tutorial_record.get('title') == 'No Tutorial Uploaded'
                            or tutorial_record.get('title') == ''
                        )
                    is_upcoming = parsed_date > today
                    tutorial_list.append({
                        'date': parsed_date.strftime("%B %d, %Y"),
                        'date_iso': meeting_date_iso,
                        'has_tutorial': has_tutorial,
                        'is_placeholder': is_placeholder_tutorial,
                        'is_upcoming': is_upcoming,
                        'status': 'updated' if has_tutorial and not is_placeholder_tutorial else 'not_updated',
                        'tutorial_name': tutorial_record.get('title', 'No Tutorial') if has_tutorial else None,
                        'description': tutorial_record.get('description', '') if has_tutorial else None,
                        'sort_date': parsed_date
                    })
                tutorial_list.sort(key=lambda x: (not x['is_upcoming'], -x['sort_date'].toordinal()))
            except Exception as e:
                print(f"Error fetching tutorial list: {e}")
                tutorial_list = []
        except Exception as e:
            print(f"Error fetching dashboard data: {e}")
            member_count = 0
            next_meeting_date = get_tutorial_meeting_date_corrected()
            current_attendance_date = get_attendance_meeting_date_corrected()
            tutorial_list = []
            attendance_list = []
            latest_attendance = None
            leaderboard_stats = get_dashboard_leaderboard_stats(leader_id) if leader_id else {
                'rank': None,
                'total_score': 0,
                'position': '-'
            }
        past_tuesdays = get_past_tuesdays()
        today = app_now()
        
        # Get attendance status for current week (using attendance-specific date logic)
        attendance_status = 'incomplete'
        latest_attendance = None
        attendance_list = []

        try:
            current_tuesday_str = current_attendance_date.strftime("%B %d, %Y")

            query = supabase.table('cell_members').select('id,created_at').eq('leader_id', leader_id)
            query = query.lte('created_at', get_member_attendance_cutoff_iso(current_attendance_date))
            members_result = query.execute()
            eligible_members = [
                m for m in (members_result.data or [])
                if member_created_within_attendance_window(m.get('created_at'), current_attendance_date)
            ]
            total_members = len(eligible_members)

            if total_members > 0:
                member_ids = [member['id'] for member in eligible_members]
                attendance_result = supabase.table('attendance')\
                    .select('member_id')\
                    .eq('leader_id', leader_id)\
                    .eq('meeting_date', current_attendance_date.isoformat())\
                    .in_('member_id', member_ids)\
                    .execute()

                attendance_count = len(attendance_result.data) if attendance_result.data else 0

                if attendance_count == total_members:
                    attendance_status = 'complete'
                elif attendance_count > 0:
                    attendance_status = 'partial'
                else:
                    attendance_status = 'incomplete'
            else:
                attendance_status = 'incomplete'

            latest_attendance = {
                'meeting_date': current_tuesday_str,
                'meeting_date_iso': current_attendance_date.isoformat(),
                'status': attendance_status
            }

            attendance_list = []
            try:
                cm_full = supabase.table('cell_members').select('id,created_at').eq('leader_id', leader_id).execute()
                cm_rows = cm_full.data or []
                parsed_meetings = []
                for meeting in meetings_for_dashboard:
                    meeting_date_raw = meeting.get('meeting_date')
                    parsed_date = _parse_meeting_date_value(meeting_date_raw)
                    if not parsed_date:
                        continue
                    if user_created_date_dashboard and parsed_date < user_created_date_dashboard:
                        continue
                    parsed_meetings.append({
                        'parsed_date': parsed_date,
                        'meeting_date_iso': parsed_date.isoformat(),
                        'date_str': parsed_date.strftime("%B %d, %Y"),
                    })
                isos_att = [p['meeting_date_iso'] for p in parsed_meetings]
                att_by_meeting = {}
                if isos_att:
                    try:
                        att_batch = supabase.table('attendance')\
                            .select('member_id,meeting_date')\
                            .eq('leader_id', leader_id)\
                            .in_('meeting_date', isos_att)\
                            .execute()
                        for row in (att_batch.data or []):
                            nk = _parse_meeting_date_value(row.get('meeting_date'))
                            if nk:
                                att_by_meeting.setdefault(nk.isoformat(), set()).add(row['member_id'])
                    except Exception:
                        pass
                for p in parsed_meetings:
                    meeting_date_iso = p['meeting_date_iso']
                    parsed_date = p['parsed_date']
                    valid_ids = set()
                    for m in cm_rows:
                        mid = m.get('id')
                        if not mid:
                            continue
                        if member_created_within_attendance_window(m.get('created_at'), parsed_date):
                            valid_ids.add(mid)
                    meeting_total_members = len(valid_ids)
                    raw_marks = set(att_by_meeting.get(meeting_date_iso, set()))
                    if not raw_marks:
                        for alt_k, alt_set in att_by_meeting.items():
                            if alt_k.startswith(meeting_date_iso) or meeting_date_iso.startswith(alt_k[:10]):
                                raw_marks |= alt_set
                    week_attendance_count = len(raw_marks & valid_ids)
                    if meeting_total_members > 0 and week_attendance_count == meeting_total_members:
                        week_status = 'complete'
                    elif week_attendance_count > 0:
                        week_status = 'partial'
                    else:
                        week_status = 'incomplete'
                    attendance_list.append({
                        'date': p['date_str'],
                        'date_iso': meeting_date_iso,
                        'status': week_status,
                        'count': week_attendance_count,
                        'total': meeting_total_members
                    })
            except Exception as e:
                print(f"Error fetching attendance list: {e}")
                attendance_list = []
        except Exception as e:
            print(f"Error fetching attendance data: {e}")
            attendance_status = 'incomplete'
            latest_attendance = {
                'meeting_date': 'Error loading data',
                'meeting_date_iso': None,
                'status': 'incomplete'
            }
        
        try:
            # Get attendance reminder info for dashboard
            attendance_reminder = None
            attendance_countdown = None
            if current_attendance_date:
                attendance_reminder = get_attendance_reminder_info(current_attendance_date)
                attendance_countdown = get_attendance_marking_countdown_payload(current_attendance_date)
            
            template_name = f'main/dashboard{get_template_suffix()}.html'
            return render_template(template_name,
                                 user=session['user'], 
                                 next_meeting_date=next_meeting_date.strftime("%B %d, %Y"),
                                 next_meeting_date_obj=next_meeting_date,
                                 current_attendance_date=current_attendance_date.strftime("%B %d, %Y"),
                                 current_attendance_date_obj=current_attendance_date,
                                 member_count=member_count,
                                 tutorial_card=tutorial_card_data,
                                 tutorial_list=tutorial_list,
                                 attendance_list=attendance_list,
                                 current_week_date=next_meeting_date.strftime("%B %d, %Y"),
                                 week_1_date=past_tuesdays[0],
                                 week_2_date=past_tuesdays[1],
                                 week_3_date=past_tuesdays[2],
                                 week_4_date=past_tuesdays[3],
                                 latest_attendance=latest_attendance,
                                 leaderboard_stats=leaderboard_stats,
                                 attendance_reminder=attendance_reminder,
                                 attendance_countdown=attendance_countdown,
                                 today=today)
        except Exception as e:
            print(f"Error rendering dashboard template: {e}")
            flash('Error loading dashboard', 'error')
            return redirect(url_for('auth.login'))
    return redirect(url_for('auth.login'))
@main_bp.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    r = redirect_deputy_to_attendance()
    if r:
        return r
    leader_id = get_effective_leader_id()

    if request.method == 'POST':
        if session.get('user', {}).get('is_deputy'):
            flash('Only the cell leader can update cell category.', 'error')
            return redirect(url_for('main.profile'))
        cat = (request.form.get('cell_category') or '').strip()
        allowed = {'youth', 'young adult', 'adult'}
        if cat not in allowed:
            flash('Please choose a valid cell category.', 'error')
            return redirect(url_for('main.profile'))
        try:
            supabase.table('users').update({'cell_category': cat}).eq('id', leader_id).execute()
            session['user']['cell_category'] = cat
            flash('Cell category saved.', 'success')
        except Exception as e:
            print(f"Error updating cell_category: {e}")
            flash('Could not save cell category.', 'error')
        return redirect(url_for('main.profile'))

    # Create a copy of user data to add calculated fields
    user_data = dict(session['user'])
    
    # Map role for display (deputy has no role_id; they are in cell_members only)
    if user_data.get('is_deputy'):
        user_data['current_role'] = 'Deputy Leader'
    else:
        role_id = user_data.get('role_id')
        user_data['current_role'] = 'Cell Leader' if role_id == 4 else 'Cell Member'
    
    # Fetch full user row for age, branch, zone if columns exist
    try:
        user_row = supabase.table('users').select('*').eq('id', leader_id).limit(1).execute()
        if user_row.data and len(user_row.data) > 0:
            row = user_row.data[0]
            user_data['age'] = row.get('age')
            user_data['cell_category'] = row.get('cell_category')
            user_data['zone'] = row.get('zone_name') or row.get('zone')
            # Branch: resolve branch_id to branch name (branches table lookup)
            branch_id = row.get('branch_id')
            user_data['branch_name'] = None
            if branch_id is not None:
                try:
                    branch_res = supabase.table('branches').select('name').eq('id', branch_id).limit(1).execute()
                    if branch_res.data and len(branch_res.data) > 0 and branch_res.data[0].get('name'):
                        user_data['branch_name'] = branch_res.data[0].get('name')
                except Exception:
                    pass
            if row.get('zone_id') and not user_data.get('zone'):
                try:
                    zone_res = supabase.table('zones').select('name').eq('id', row['zone_id']).limit(1).execute()
                    if zone_res.data and len(zone_res.data) > 0:
                        user_data['zone'] = zone_res.data[0].get('name')
                except Exception:
                    pass
    except Exception as e:
        print(f"Error fetching user profile fields: {e}")
    
    # Calculate member count
    try:
        members_result = supabase.table('cell_members')\
            .select('id')\
            .eq('leader_id', leader_id)\
            .execute()
        user_data['members_count'] = len(members_result.data) if members_result.data else 0
    except Exception as e:
        print(f"Error counting members: {e}")
        user_data['members_count'] = 0
    
    # Calculate total meetings held and attendance rate
    meetings_count = 0
    present_count = 0
    try:
        attendance_result = supabase.table('attendance')\
            .select('meeting_date, status')\
            .eq('leader_id', leader_id)\
            .execute()
        
        if attendance_result.data:
            unique_dates = set()
            for record in attendance_result.data:
                meeting_date = record.get('meeting_date')
                if meeting_date:
                    date_str = meeting_date.split('T')[0] if isinstance(meeting_date, str) and 'T' in meeting_date else (meeting_date if isinstance(meeting_date, str) else str(meeting_date))
                    unique_dates.add(date_str)
                if record.get('status') == 'present':
                    present_count += 1
            meetings_count = len(unique_dates)
        user_data['meetings_count'] = meetings_count
    except Exception as e:
        print(f"Error counting meetings: {e}")
        user_data['meetings_count'] = 0
    
    # Attendance rate: (present records) / (meetings * members) * 100 when both > 0
    total_slots = meetings_count * user_data.get('members_count', 0)
    if total_slots > 0:
        user_data['attendance_rate'] = round((present_count / total_slots) * 100)
    else:
        user_data['attendance_rate'] = 0
    
    template_name = f'main/profile{get_template_suffix()}.html'
    return render_template(template_name, user=user_data)


@main_bp.route('/leaderboard')
def leaderboard():
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    if session.get('user', {}).get('is_deputy'):
        flash('Leaderboard is available for leaders only.', 'error')
        return redirect(url_for('main.meeting_dates'))
    template_name = f'main/leaderboard{get_template_suffix()}.html'
    return render_template(template_name, user=session['user'])


@main_bp.route('/meeting-dates')
def meeting_dates():
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    try:
        # Get leader ID - use user ID directly
        leader_id = get_effective_leader_id()
        
        # Get user's created date to filter meetings
        user_created_date = get_user_created_date(leader_id)
        
        # Query meetings from database
        # Filter meetings to only show those created after the user was created
        meetings = []
        print(f"DEBUG: Fetching meetings from database...")
        
        try:
            # Query meetings from meetings table, filtered by user's creation date
            print("DEBUG: Querying meetings table...")
            try:
                # Build query
                query = supabase.table('meetings').select('*')
                
                # Filter by meeting_date >= user_created_date if user_created_date exists
                if user_created_date:
                    query = query.gte('meeting_date', user_created_date.isoformat())
                    print(f"DEBUG: Filtering meetings where meeting_date >= {user_created_date.isoformat()}")
                
                meetings_result = query\
                    .order('meeting_date', desc=True)\
                    .limit(20)\
                    .execute()
                
                print(f"DEBUG: Meetings found: {len(meetings_result.data) if meetings_result.data else 0}")
            except Exception as order_error:
                print(f"DEBUG: Error with order clause, trying without order: {order_error}")
                # Try without order clause, but still apply date filter
                query = supabase.table('meetings').select('*')
                if user_created_date:
                    query = query.gte('meeting_date', user_created_date.isoformat())
                meetings_result = query.limit(20).execute()
                print(f"DEBUG: Meetings found (no order): {len(meetings_result.data) if meetings_result.data else 0}")
            
            if meetings_result.data:
                print(f"DEBUG: Processing {len(meetings_result.data)} meetings...")
                # Process meetings from meetings table
                for meeting in meetings_result.data:
                    meeting_date = meeting.get('meeting_date')
                    meeting_name = meeting.get('meeting_name', 'Cell Meeting')
                    meeting_number = meeting.get('meeting_number')
                    print(f"DEBUG: Processing meeting - ID: {meeting.get('id')}, Date: {meeting_date}, Name: {meeting_name}, Number: {meeting_number}")
                    
                    if meeting_date:
                        # Additional safety check: filter by user_created_date if available
                        if user_created_date:
                            try:
                                # Parse meeting date for comparison
                                if isinstance(meeting_date, str):
                                    try:
                                        meeting_date_parsed = datetime.strptime(meeting_date, "%Y-%m-%d").date()
                                    except ValueError:
                                        try:
                                            meeting_date_parsed = datetime.strptime(meeting_date, "%Y-%m-%dT%H:%M:%S").date()
                                        except ValueError:
                                            meeting_date_parsed = datetime.strptime(meeting_date.split('T')[0], "%Y-%m-%d").date()
                                else:
                                    meeting_date_parsed = meeting_date
                                
                                # Skip meetings before user creation
                                if meeting_date_parsed < user_created_date:
                                    print(f"DEBUG: Skipping meeting {meeting_date} (before user creation {user_created_date})")
                                    continue
                            except Exception as date_check_error:
                                print(f"DEBUG: Error checking meeting date: {date_check_error}, including meeting anyway")
                        try:
                            # Parse date if it's a string
                            if isinstance(meeting_date, str):
                                # Try different date formats
                                try:
                                    parsed_date = datetime.strptime(meeting_date, "%Y-%m-%d").date()
                                except ValueError:
                                    try:
                                        parsed_date = datetime.strptime(meeting_date, "%Y-%m-%dT%H:%M:%S").date()
                                    except ValueError:
                                        parsed_date = datetime.strptime(meeting_date.split('T')[0], "%Y-%m-%d").date()
                            else:
                                parsed_date = meeting_date
                            
                            meetings.append({
                                'date': parsed_date.strftime("%B %d, %Y"),
                                'date_iso': parsed_date.isoformat(),
                                'date_obj': parsed_date,  # Store date object for sorting
                                'meeting_type': meeting_name,  # Use meeting_name from database
                                'description': f"Meeting #{meeting_number}" if meeting_number else '',  # Use meeting_number as description
                                'id': meeting.get('id'),
                                'meeting_number': meeting_number,
                                'is_upcoming': False  # Will be set later
                            })
                            print(f"DEBUG: Successfully added meeting: {parsed_date.strftime('%B %d, %Y')} - {meeting_name}")
                        except Exception as e:
                            print(f"Error parsing meeting date: {e}, meeting_date value: {meeting_date}, type: {type(meeting_date)}")
                            continue
                    else:
                        print(f"DEBUG: Meeting {meeting.get('id')} has no meeting_date field")
            else:
                print("DEBUG: No meetings data returned from query")
        except Exception as e:
            print(f"Error querying meetings table: {e}")
            import traceback
            traceback.print_exc()
            # Fallback: Get unique meeting dates from attendance table
            try:
                attendance_result = supabase.table('attendance')\
                    .select('meeting_date')\
                    .eq('leader_id', leader_id)\
                    .order('meeting_date', desc=True)\
                    .execute()
                
                if attendance_result.data:
                    # Get unique meeting dates
                    unique_dates = set()
                    for record in attendance_result.data:
                        meeting_date = record.get('meeting_date')
                        if meeting_date:
                            unique_dates.add(meeting_date)
                    
                    # Convert to list and sort
                    for date_str in sorted(unique_dates, reverse=True)[:20]:
                        try:
                            parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                            meetings.append({
                                'date': parsed_date.strftime("%B %d, %Y"),
                                'date_iso': parsed_date.isoformat(),
                                'date_obj': parsed_date,  # Store date object for sorting
                                'meeting_type': 'Cell Meeting',
                                'description': '',
                                'id': None,
                                'is_upcoming': False  # Will be set later
                            })
                        except Exception as e:
                            print(f"Error parsing attendance date: {e}")
                            continue
            except Exception as e2:
                print(f"Error querying attendance table: {e2}")
        
        # If no meetings found, use fallback to past Tuesdays
        if not meetings:
            print("No meetings found in database, using calculated Tuesdays as fallback")
            past_tuesdays = get_past_tuesdays()
            for date_str in past_tuesdays:
                try:
                    parsed_date = datetime.strptime(date_str, "%B %d, %Y").date()
                    meetings.append({
                        'date': date_str,
                        'date_iso': parsed_date.isoformat(),
                        'date_obj': parsed_date,
                        'meeting_type': 'Cell Meeting',
                        'description': '',
                        'id': None,
                        'is_upcoming': False,
                    })
                except Exception as e:
                    print(f"Error parsing fallback date: {e}")
        
        # Identify upcoming meeting (latest date) and mark others as recent
        if meetings:
            # Sort by date to find the latest
            meetings.sort(key=lambda x: x['date_obj'], reverse=True)
            # Mark the first one (latest) as upcoming
            if len(meetings) > 0:
                meetings[0]['is_upcoming'] = True
                print(f"DEBUG: Upcoming meeting: {meetings[0]['date']}")
            # Mark others as recent
            for meeting in meetings[1:]:
                meeting['is_upcoming'] = False

        enrich_meetings_with_attendance_eligibility(meetings, leader_id)
        enrich_meetings_with_attendance_summary(meetings, leader_id)

        print(f"DEBUG: Final meetings count: {len(meetings)}")
        for i, meeting in enumerate(meetings, 1):
            status = "UPCOMING" if meeting.get('is_upcoming') else "RECENT"
            print(f"DEBUG: Meeting {i}: {meeting['date']} - {meeting['meeting_type']} ({status})")
        
        template_name = f'main/meeting_dates{get_template_suffix()}.html'
        return render_template(template_name, 
                             user=session['user'],
                             meetings=meetings)
    except Exception as e:
        print(f"Error fetching meeting dates: {e}")
        import traceback
        traceback.print_exc()
        flash('Error loading meeting dates', 'error')
        # Fallback to calculated Tuesdays
        past_tuesdays = get_past_tuesdays()
        meetings = []
        for date_str in past_tuesdays:
            try:
                parsed_date = datetime.strptime(date_str, "%B %d, %Y").date()
                meetings.append({
                    'date': date_str,
                    'date_iso': parsed_date.isoformat(),
                    'date_obj': parsed_date,  # Store date object for sorting
                    'meeting_type': 'Cell Meeting',
                    'description': '',
                    'id': None,
                    'is_upcoming': False  # Will be set later
                })
            except Exception as e:
                print(f"Error parsing fallback date: {e}")
        
        # Identify upcoming meeting (latest date) and mark others as recent
        if meetings:
            # Sort by date to find the latest
            meetings.sort(key=lambda x: x['date_obj'], reverse=True)
            # Mark the first one (latest) as upcoming
            if len(meetings) > 0:
                meetings[0]['is_upcoming'] = True
            # Mark others as recent
            for meeting in meetings[1:]:
                meeting['is_upcoming'] = False

        enrich_meetings_with_attendance_eligibility(meetings, leader_id)
        enrich_meetings_with_attendance_summary(meetings, leader_id)

        template_name = f'main/meeting_dates{get_template_suffix()}.html'
        return render_template(template_name, 
                             user=session['user'],
                             meetings=meetings)

@main_bp.route('/attendance/<meeting_date>')
def attendance_detail(meeting_date):
    """Display attendance page for a specific meeting date"""
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    
    try:
        # Get leader ID - use user ID directly
        leader_id = get_effective_leader_id()
        
        # Convert meeting_date string to proper date format
        from datetime import datetime
        try:
            parsed_date = datetime.strptime(meeting_date, "%B %d, %Y").date()
            meeting_date_formatted = parsed_date.isoformat()
        except ValueError:
            meeting_date_formatted = meeting_date
            # Try to parse as ISO format if the above fails
            try:
                parsed_date = datetime.strptime(meeting_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = None
        
        # Get members eligible for this meeting week. A member is eligible if they
        # were created on or before the END of the marking window (Thursday
        # 23:59:59 local), so members added during the window (including same-day
        # Tuesday adds) appear here and can be marked present.
        query = supabase.table('cell_members').select('*').eq('leader_id', leader_id)

        # Filter at the DB layer using an end-of-window timestamp (not a bare date,
        # which would be treated as midnight and drop in-window adds).
        if parsed_date:
            query = query.lte('created_at', get_member_attendance_cutoff_iso(parsed_date))

        members_result = query.execute()
        members = members_result.data if members_result.data else []

        # Safety net: apply the same eligibility rule in Python.
        if parsed_date and members:
            members = [
                member for member in members
                if member_created_within_attendance_window(member.get('created_at'), parsed_date)
            ]

        # Show the leader's own self-row first so it's prominent on the attendance page.
        members.sort(key=lambda m: (0 if m.get('is_leader') else 1, (m.get('name') or '').lower()))
        
        # Get existing attendance data
        attendance_data = {}
        if members:
            try:
                member_ids = [member['id'] for member in members]
                attendance_result = supabase.table('attendance').select('*').eq('leader_id', leader_id).eq('meeting_date', meeting_date_formatted).in_('member_id', member_ids).execute()
                
                # Initialize all members as incomplete (marked_by_label for when they get marked)
                for member in members:
                    attendance_data[member['id']] = {
                        'present': False,
                        'absent': False,
                        'incomplete': True,
                        'marked_by': None,
                        'marked_by_label': 'Leader'
                    }
                
                # Update with actual attendance data (leader_user_uuid = leader marked; member_user_uuid = deputy marked)
                if attendance_result.data:
                    for record in attendance_result.data:
                        member_id = record['member_id']
                        status = record['status']
                        member_user_uuid = record.get('member_user_uuid')
                        if member_user_uuid:
                            marked_by_label = 'Deputy Leader'
                        else:
                            marked_by_label = 'Leader'
                        attendance_data[member_id] = {
                            'present': status == 'present',
                            'absent': status == 'absent',
                            'incomplete': False,
                            'marked_by_label': marked_by_label
                        }
            except Exception as e:
                print(f"Error fetching attendance data: {e}")
                # Initialize all as incomplete if error
                for member in members:
                    attendance_data[member['id']] = {
                        'present': False,
                        'absent': False,
                        'incomplete': True,
                        'marked_by': None,
                        'marked_by_label': 'Leader'
                    }
        
        # Leader-specific: marking window + not bulk-submitted for this week
        can_mark = False
        reminder_info = None
        attendance_submitted = False
        locked_reason = None
        attendance_locked_message = None
        if parsed_date:
            submitted_iso_set = fetch_submitted_meeting_dates(leader_id, [parsed_date.isoformat()])
            edit_state = attendance_edit_state(parsed_date, submitted_iso_set)
            can_mark = edit_state['can_mark_attendance']
            attendance_submitted = edit_state['attendance_submitted']
            locked_reason = edit_state['locked_reason']
            if not can_mark and not attendance_submitted:
                attendance_locked_message = attendance_edit_denied_message(
                    parsed_date, submitted_iso_set
                )
            reminder_info = get_attendance_reminder_info(parsed_date)

        attendance_present_count = 0
        attendance_absent_count = 0
        attendance_pending_count = 0
        for m in members:
            ad = attendance_data.get(m['id']) or {}
            if ad.get('present'):
                attendance_present_count += 1
            elif ad.get('absent'):
                attendance_absent_count += 1
            else:
                attendance_pending_count += 1

        meeting_date_iso = parsed_date.isoformat() if parsed_date else None
        visitor_attendance = None
        visitor_saved_time_label = None
        if meeting_date_iso:
            leader_uid = get_effective_leader_id()
            try:
                va_res = supabase.table(ATTENDANCE_VISITOR_COUNTS_TABLE).select(
                    'id, leader_user_id, meeting_id, meeting_date, visitor_count, reported_at'
                ).eq('leader_user_id', str(leader_uid)).eq('meeting_date', meeting_date_iso).limit(1).execute()
                if va_res.data and len(va_res.data) > 0:
                    visitor_attendance = va_res.data[0]
                    ra = visitor_attendance.get('reported_at')
                    if ra:
                        try:
                            if isinstance(ra, str):
                                dt_ra = datetime.fromisoformat(ra.replace('Z', '+00:00'))
                            else:
                                dt_ra = ra
                            visitor_saved_time_label = dt_ra.strftime('%H:%M') if hasattr(dt_ra, 'strftime') else None
                        except (ValueError, TypeError):
                            visitor_saved_time_label = None
            except Exception as ve:
                print(f"visitor_attendance fetch skipped: {ve}")

        visitor_count_total = 0
        if visitor_attendance:
            try:
                visitor_count_total = int(visitor_attendance.get('visitor_count') or 0)
            except (TypeError, ValueError):
                visitor_count_total = 0
        
        template_name = f'main/attendance_detail{get_template_suffix()}.html'
        return render_template(template_name, 
                             user=session['user'],
                             meeting_date=meeting_date,
                             meeting_date_iso=meeting_date_iso,
                             members=members,
                             attendance_data=attendance_data,
                             can_mark_attendance=can_mark,
                             attendance_submitted=attendance_submitted,
                             locked_reason=locked_reason,
                             attendance_locked_message=attendance_locked_message,
                             attendance_present_count=attendance_present_count,
                             attendance_absent_count=attendance_absent_count,
                             attendance_pending_count=attendance_pending_count,
                             visitor_count_total=visitor_count_total,
                             reminder_info=reminder_info,
                             leader_id=leader_id,
                             visitor_attendance=visitor_attendance,
                             visitor_saved_time_label=visitor_saved_time_label,
                             can_edit_visitor_count=bool(can_mark and not session['user'].get('is_deputy')))
    except Exception as e:
        print(f"Error in attendance_detail: {e}")
        flash('Error loading attendance page', 'error')
        return redirect(url_for('main.meeting_dates'))

@main_bp.route('/update_attendance/<meeting_date>', methods=['POST'])
def update_attendance(meeting_date):
    """Update attendance for a specific member and meeting date"""
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    try:
        member_id = request.form.get('member_id')
        status = request.form.get('status')  # 'present', 'absent', 'clear'
        
        if not member_id or not status:
            return jsonify({'success': False, 'message': 'Missing required data'}), 400
        
        # Get leader ID - use user ID directly
        leader_id = get_effective_leader_id()
        
        # Convert meeting_date string to proper date format
        from datetime import datetime
        try:
            parsed_date = datetime.strptime(meeting_date, "%B %d, %Y").date()
            meeting_date_formatted = parsed_date.isoformat()
        except ValueError:
            meeting_date_formatted = meeting_date
            # Try to parse as ISO format if the above fails
            try:
                parsed_date = datetime.strptime(meeting_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = None
        
        if parsed_date:
            submitted_iso_set = fetch_submitted_meeting_dates(leader_id, [parsed_date.isoformat()])
            if not leader_can_mark_attendance(leader_id, parsed_date, submitted_iso_set):
                msg = attendance_edit_denied_message(parsed_date, submitted_iso_set)
                return jsonify({'success': False, 'message': msg or 'Attendance cannot be updated.'}), 403
        
        # Get member info and validate it was created on or before meeting date
        try:
            member_result = supabase.table('cell_members').select('name, created_at').eq('id', member_id).eq('leader_id', leader_id).execute()
            if not member_result.data or len(member_result.data) == 0:
                return jsonify({'success': False, 'message': 'Member not found'}), 404
            
            member = member_result.data[0]
            member_name = member.get('name', 'Unknown')
            member_created_at = member.get('created_at')
            
            # Validate the member is eligible for this meeting week. Eligible when
            # created on or before the end of the marking window (that week's
            # Thursday), so members added during the window can be marked.
            if parsed_date and member_created_at:
                if not member_created_within_attendance_window(member_created_at, parsed_date):
                    return jsonify({'success': False, 'message': f'Cannot mark attendance: This member was added after the meeting week ({meeting_date})'}), 403
        except Exception as e:
            print(f"Error fetching member info: {e}")
            return jsonify({'success': False, 'message': 'Error fetching member information'}), 500
        
        # Get meeting_number from meetings table based on meeting_date
        meeting_number = None
        try:
            meeting_result = supabase.table('meetings').select('meeting_number').eq('meeting_date', meeting_date_formatted).limit(1).execute()
            if meeting_result.data and len(meeting_result.data) > 0:
                meeting_number = meeting_result.data[0].get('meeting_number')
        except Exception as e:
            print(f"Error fetching meeting_number: {e}")
            # If meeting not found, try to get the latest meeting number or use a default
            # For now, we'll let it fail if meeting_number is required
        
        # Handle attendance update
        if status == 'clear':
            # Delete existing attendance record
            try:
                existing_result = supabase.table('attendance').select('id').eq('leader_id', leader_id).eq('member_id', member_id).eq('meeting_date', meeting_date_formatted).execute()
                if existing_result.data and len(existing_result.data) > 0:
                    result = supabase.table('attendance').delete().eq('id', existing_result.data[0]['id']).execute()
                    # Delete operations in Supabase return the deleted record or empty list
                    # If no error was raised, the delete was successful
                    # Log activity
                    try:
                        log_activity(
                            leader_id=leader_id,
                            user_id=leader_id,
                            activity_type='attendance_marked',
                            description=f'Cleared attendance for {member_name} for {meeting_date}',
                            user_role='leader',
                            user_name=session['user'].get('name', 'Leader'),
                            source='cell_app',
                            platform='web',
                            details={'member_id': member_id, 'meeting_date': meeting_date_formatted}
                        )
                    except Exception as log_error:
                        print(f"Error logging activity: {log_error}")
                    
                    return jsonify({'success': True, 'message': f'Attendance cleared for {member_name}'})
                else:
                    return jsonify({'success': False, 'message': 'No attendance record to clear'}), 400
            except Exception as delete_error:
                print(f"Error deleting attendance: {delete_error}")
                return jsonify({'success': False, 'message': 'Error clearing attendance'}), 500
        else:
            # Insert or update attendance record
            # meeting_number is required by the schema
            if meeting_number is None:
                return jsonify({'success': False, 'message': 'Meeting not found. Cannot mark attendance.'}), 400
            
            is_deputy = session['user'].get('is_deputy')
            attendance_data = {
                'leader_id': leader_id,
                'member_id': member_id,
                'meeting_date': meeting_date_formatted,
                'meeting_number': meeting_number,
                'status': status
            }
            if is_deputy:
                attendance_data['member_user_uuid'] = session['user'].get('member_id')
            else:
                attendance_data['leader_user_uuid'] = session['user'].get('id')
            
            existing_result = supabase.table('attendance').select('id').eq('leader_id', leader_id).eq('member_id', member_id).eq('meeting_date', meeting_date_formatted).execute()
            update_payload = {'status': status, 'meeting_number': meeting_number}
            if is_deputy:
                update_payload['member_user_uuid'] = session['user'].get('member_id')
                update_payload['leader_user_uuid'] = None
            else:
                update_payload['leader_user_uuid'] = session['user'].get('id')
                update_payload['member_user_uuid'] = None
            
            if existing_result.data and len(existing_result.data) > 0:
                result = supabase.table('attendance').update(update_payload).eq('id', existing_result.data[0]['id']).execute()
            else:
                result = supabase.table('attendance').insert(attendance_data).execute()
            
            if result.data and len(result.data) > 0:
                # Log activity
                try:
                    log_activity(
                        leader_id=leader_id,
                        user_id=leader_id,
                        activity_type='attendance_marked',
                        description=f'Marked {member_name} as {status} for {meeting_date}',
                        user_role='deputy_leader' if is_deputy else 'leader',
                        user_name=session['user'].get('name', 'Leader'),
                        source='cell_app',
                        platform='web',
                        details={'member_id': member_id, 'meeting_date': meeting_date_formatted, 'status': status}
                    )
                except Exception as log_error:
                    print(f"Error logging activity: {log_error}")
                
                return jsonify({'success': True, 'message': f'{member_name} marked as {status}'})
            else:
                print(f"Error: No data returned from attendance insert/update. Result: {result}")
                return jsonify({'success': False, 'message': 'Error saving attendance. Please try again.'}), 500
        
    except Exception as e:
        print(f"Error updating attendance: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Error updating attendance: {str(e)}'}), 500

@main_bp.route('/bulk_update_attendance/<meeting_date>', methods=['POST'])
def bulk_update_attendance(meeting_date):
    """Bulk update attendance for multiple members at once"""
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    try:
        # Get attendance data from JSON
        data = request.get_json()
        attendance_list = data.get('attendance', [])  # List of {member_id, status}
        
        if not attendance_list:
            return jsonify({'success': False, 'message': 'No attendance data provided'}), 400
        
        # Get leader ID - use user ID directly
        leader_id = get_effective_leader_id()
        
        # Convert meeting_date string to proper date format
        try:
            parsed_date = datetime.strptime(meeting_date, "%B %d, %Y").date()
            meeting_date_formatted = parsed_date.isoformat()
        except ValueError:
            meeting_date_formatted = meeting_date
            # Try to parse as ISO format if the above fails
            try:
                parsed_date = datetime.strptime(meeting_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = None
        
        # Leader-specific: marking window + not already bulk-submitted
        if parsed_date:
            submitted_iso_set = fetch_submitted_meeting_dates(leader_id, [parsed_date.isoformat()])
            if not leader_can_mark_attendance(leader_id, parsed_date, submitted_iso_set):
                msg = attendance_edit_denied_message(parsed_date, submitted_iso_set)
                return jsonify({'success': False, 'message': msg or 'Attendance cannot be updated.'}), 403
        
        # Get meeting_number from meetings table
        meeting_number = None
        try:
            meeting_result = supabase.table('meetings').select('meeting_number').eq('meeting_date', meeting_date_formatted).limit(1).execute()
            if meeting_result.data and len(meeting_result.data) > 0:
                meeting_number = meeting_result.data[0].get('meeting_number')
        except Exception as e:
            print(f"Error fetching meeting_number: {e}")
        
        if meeting_number is None:
            return jsonify({'success': False, 'message': 'Meeting not found. Cannot mark attendance.'}), 400

        expected_ids = get_attendance_eligible_member_id_strings(
            leader_id, parsed_date, meeting_date_formatted
        )
        if not expected_ids:
            return jsonify({'success': False, 'message': 'No members to mark for this meeting.'}), 400

        payload_by_id = {}
        for attendance_item in attendance_list:
            member_id = attendance_item.get('member_id')
            status = attendance_item.get('status')
            if not member_id or status not in ('present', 'absent'):
                return jsonify({
                    'success': False,
                    'message': 'Each entry must include member_id and status present or absent.',
                }), 400
            sid = str(member_id)
            if sid in payload_by_id:
                return jsonify({
                    'success': False,
                    'message': 'Duplicate member entries in attendance data.',
                }), 400
            payload_by_id[sid] = status

        if set(payload_by_id.keys()) != expected_ids:
            return jsonify({
                'success': False,
                'message': 'Mark every member as present or absent before submitting.',
            }), 400

        # Process each attendance record (validated set matches eligible members)
        success_count = 0
        error_count = 0
        errors = []

        for member_id, status in payload_by_id.items():

            # Validate the member is eligible for this meeting week (created on or
            # before the end of the marking window, i.e. that week's Thursday).
            if parsed_date:
                try:
                    member_result = supabase.table('cell_members').select('created_at').eq('id', member_id).eq('leader_id', leader_id).execute()
                    if member_result.data and len(member_result.data) > 0:
                        member_created_at = member_result.data[0].get('created_at')
                        if not member_created_within_attendance_window(member_created_at, parsed_date):
                            error_count += 1
                            errors.append(f"Member {member_id}: Added after meeting week")
                            continue
                except Exception as validation_error:
                    print(f"Error validating member {member_id}: {validation_error}, allowing update")
            
            try:
                is_deputy = session['user'].get('is_deputy')
                attendance_data = {
                    'leader_id': leader_id,
                    'member_id': member_id,
                    'meeting_date': meeting_date_formatted,
                    'meeting_number': meeting_number,
                    'status': status
                }
                if is_deputy:
                    attendance_data['member_user_uuid'] = session['user'].get('member_id')
                else:
                    attendance_data['leader_user_uuid'] = session['user'].get('id')
                update_payload = {'status': status, 'meeting_number': meeting_number}
                if is_deputy:
                    update_payload['member_user_uuid'] = session['user'].get('member_id')
                    update_payload['leader_user_uuid'] = None
                else:
                    update_payload['leader_user_uuid'] = session['user'].get('id')
                    update_payload['member_user_uuid'] = None
                # Check if record exists
                existing_result = supabase.table('attendance').select('id').eq('leader_id', leader_id).eq('member_id', member_id).eq('meeting_date', meeting_date_formatted).execute()
                
                if existing_result.data and len(existing_result.data) > 0:
                    result = supabase.table('attendance').update(update_payload).eq('id', existing_result.data[0]['id']).execute()
                else:
                    result = supabase.table('attendance').insert(attendance_data).execute()
                
                if result.data and len(result.data) > 0:
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"Member {member_id}")
            except Exception as e:
                error_count += 1
                errors.append(f"Member {member_id}: {str(e)}")
                print(f"Error updating attendance for member {member_id}: {e}")
        
        # Log activity
        try:
            log_activity(
                leader_id=leader_id,
                user_id=leader_id,
                activity_type='attendance_bulk_updated',
                description=f'Bulk updated attendance for {success_count} members for {meeting_date}',
                user_role='leader',
                user_name=session['user'].get('name', 'Leader'),
                source='cell_app',
                platform='web',
                details={'meeting_date': meeting_date_formatted, 'success_count': success_count, 'error_count': error_count}
            )
        except Exception as log_error:
            print(f"Error logging activity: {log_error}")

        # Finalize this meeting week after a successful bulk submit (locks further edits)
        if error_count == 0 and success_count > 0 and meeting_date_formatted:
            if not record_attendance_week_submitted(
                leader_id,
                meeting_date_formatted,
                session['user'].get('id'),
            ):
                return jsonify({
                    'success': False,
                    'message': (
                        'Attendance was saved but this week could not be finalized (submission lock). '
                        'Apply the attendance_submissions migration in Supabase, then submit again.'
                    ),
                }), 500
        
        if error_count == 0:
            return jsonify({'success': True, 'message': f'Successfully updated attendance for {success_count} members'})
        else:
            return jsonify({
                'success': True, 
                'message': f'Updated {success_count} members, {error_count} errors',
                'errors': errors
            })
        
    except Exception as e:
        print(f"Error in bulk_update_attendance: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Error updating attendance: {str(e)}'}), 500


@main_bp.route('/update_visitor_attendance/<meeting_date>', methods=['POST'])
def update_visitor_attendance(meeting_date):
    """Persist visitor count (0-20) for the cell leader and meeting. Separate from member attendance rows."""
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Authentication required'}), 401
    if session['user'].get('is_deputy'):
        return jsonify({'success': False, 'message': 'Only cell leaders can report visitors.'}), 403
    if supabase is None:
        return jsonify({'success': False, 'message': 'Database is not configured.'}), 503

    data = request.get_json(silent=True) or {}
    if 'visitor_count' not in data:
        return jsonify({'success': True, 'message': 'No visitor update requested', 'skipped': True})

    vc_raw = data.get('visitor_count')
    if vc_raw is None or vc_raw == '':
        return jsonify({'success': True, 'message': 'No visitor update requested', 'skipped': True})

    try:
        vc = int(vc_raw)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'Visitor count must be a whole number.'}), 400

    if vc < 0 or vc > 20:
        return jsonify({'success': False, 'message': 'Visitor count must be between 0 and 20.'}), 400

    leader_uid = get_effective_leader_id()
    if not leader_uid:
        return jsonify({'success': False, 'message': 'Could not resolve leader for this session.'}), 401
    leader_id_str = str(leader_uid)

    try:
        try:
            parsed_date = datetime.strptime(meeting_date, "%B %d, %Y").date()
            meeting_date_formatted = parsed_date.isoformat()
        except ValueError:
            meeting_date_formatted = meeting_date
            try:
                parsed_date = datetime.strptime(meeting_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = None

        if parsed_date:
            submitted_iso_set = fetch_submitted_meeting_dates(leader_uid, [parsed_date.isoformat()])
            if not leader_can_mark_attendance(leader_uid, parsed_date, submitted_iso_set):
                msg = attendance_edit_denied_message(parsed_date, submitted_iso_set)
                return jsonify({'success': False, 'message': msg or 'Attendance cannot be updated.'}), 403

        meeting_number = None
        meeting_row_id = None
        try:
            meeting_result = supabase.table('meetings').select('id, meeting_number').eq('meeting_date', meeting_date_formatted).limit(1).execute()
            if meeting_result.data and len(meeting_result.data) > 0:
                mrow = meeting_result.data[0]
                meeting_number = mrow.get('meeting_number')
                meeting_row_id = mrow.get('id')
        except Exception as e:
            print(f"Error fetching meeting for visitor counts: {e}")

        if meeting_number is None or meeting_row_id is None:
            return jsonify({'success': False, 'message': 'Meeting not found. Cannot save visitor count.'}), 400

        now_iso = datetime.now(timezone.utc).isoformat()
        marked_by_uid = session['user'].get('id')
        marked_by_name = session['user'].get('name') or session['user'].get('email') or 'Leader'
        marked_uid_str = str(marked_by_uid) if marked_by_uid else leader_id_str

        row_in = {
            'leader_user_id': leader_id_str,
            'meeting_id': str(meeting_row_id),
            'meeting_date': meeting_date_formatted,
            'meeting_number': meeting_number,
            'visitor_count': vc,
            'reported_at': now_iso,
            'marked_by_user_id': marked_uid_str,
            'marked_by_name': marked_by_name,
        }

        existing = supabase.table(ATTENDANCE_VISITOR_COUNTS_TABLE).select('id').eq(
            'leader_user_id', leader_id_str
        ).eq('meeting_date', meeting_date_formatted).limit(1).execute()

        update_payload = {
            'meeting_number': meeting_number,
            'meeting_id': str(meeting_row_id),
            'visitor_count': vc,
            'reported_at': now_iso,
            'marked_by_user_id': marked_uid_str,
            'marked_by_name': marked_by_name,
        }

        if existing.data and len(existing.data) > 0:
            supabase.table(ATTENDANCE_VISITOR_COUNTS_TABLE).update(update_payload).eq('id', existing.data[0]['id']).execute()
        else:
            supabase.table(ATTENDANCE_VISITOR_COUNTS_TABLE).insert(row_in).execute()

        return jsonify({'success': True, 'message': 'Visitor count saved.'})
    except Exception as e:
        err_text = str(e)
        print(f"Error in update_visitor_attendance: {err_text}")
        traceback.print_exc()
        hint = ''
        low = err_text.lower()
        if ('attendance_visitor_counts' in low or 'visitor_attendance' in low) and ('does not exist' in low or 'not found' in low):
            hint = ' Ensure public.attendance_visitor_counts exists (see database/migrations/create_visitor_attendance_table.sql).'
        elif 'column' in low and 'does not exist' in low:
            hint = ' Check attendance_visitor_counts columns match the app (leader_user_id, meeting_id, …).'
        msg = f'Could not save visitor count: {err_text}' + hint
        return jsonify({'success': False, 'message': msg}), 500


@main_bp.route('/members')
def members():
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    r = redirect_deputy_to_attendance()
    if r:
        return r
    try:
        # Get cell members for this leader
        # Use the user ID directly as leader_id (since role_id = 4 users ARE leaders)
        leader_id = get_effective_leader_id()
        
        # Get members for this specific leader (exclude the leader's own self-row;
        # that row exists solely so the leader can mark their own attendance).
        result = (
            supabase.table('cell_members')
            .select('*')
            .eq('leader_id', leader_id)
            .neq('is_leader', True)
            .execute()
        )
        members = result.data if result.data else []
        pending_map = pending_flagged_by_member_id(supabase, leader_id, [m.get('id') for m in members])
        for m in members:
            mid = str(m.get('id') or '')
            st = pending_map.get(mid, {})
            m['delete_request_pending'] = st.get('delete_request_pending', False)
            m['flag_issue_pending'] = st.get('flag_issue_pending', False)
            m['deputy_removal_pending'] = st.get('deputy_removal_pending', False)
        # One deputy per leader; only the leader (not deputy) can assign
        has_deputy = any(m.get('deputy_leader') for m in members)
        can_assign_deputy = not has_deputy and not session['user'].get('is_deputy')
        
        template_name = f'main/members{get_template_suffix()}.html'
        return render_template(template_name, members=members, user=session['user'], has_deputy=has_deputy, can_assign_deputy=can_assign_deputy)
    except Exception as e:
        error_msg = str(e)
        print(f"Error in members route: {error_msg}")  # Enhanced logging
        
        if "relation" in error_msg.lower() and "does not exist" in error_msg.lower():
            flash('Database table not found. Please run the database migration first.', 'error')
        elif "row-level security" in error_msg.lower():
            flash('Database access denied. Please check your permissions.', 'error')
        elif "connection" in error_msg.lower():
            flash('Database connection failed. Please try again later.', 'error')
        elif "timeout" in error_msg.lower():
            flash('Request timed out. Please check your internet connection.', 'error')
        else:
            flash('Unable to load members. Please try again.', 'error')
        return redirect(url_for('main.index'))
@main_bp.route('/member/form')
def member_form():
    """Display the member form page"""
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    r = redirect_deputy_to_attendance()
    if r:
        return r
    # Get leader location context for autofill
    leader_id = get_effective_leader_id()
    leader_branch_id, leader_country, leader_branch_name = get_leader_location_context(leader_id)
    
    # Fetch zones from zones table with sector_number from sectors table
    zones = []
    try:
        # Fetch zones with sector information
        zones_result = supabase.table('zones').select('*').order('id').execute()
        if zones_result.data:
            # For each zone, fetch the sector_number from sectors table if sector_id exists
            for zone in zones_result.data:
                sector_number = None
                if zone.get('sector_id'):
                    try:
                        sector_result = supabase.table('sectors').select('sector_number').eq('id', zone['sector_id']).limit(1).execute()
                        if sector_result.data and len(sector_result.data) > 0:
                            sector_number = sector_result.data[0].get('sector_number')
                    except Exception as e:
                        print(f"Error fetching sector for zone {zone.get('id')}: {e}")
                # Add sector_number to zone data
                zone['sector_number'] = sector_number
                zones.append(zone)
    except Exception as e:
        print(f"Error fetching zones: {e}")
    
    # Check if editing an existing member
    member_id = request.args.get('edit')
    member = None
    if member_id:
        try:
            result = (
                supabase.table('cell_members')
                .select('*')
                .eq('id', member_id)
                .eq('leader_id', leader_id)
                .neq('is_leader', True)
                .execute()
            )
            if result.data:
                member = result.data[0]
        except Exception as e:
            print(f"Error loading member for edit: {e}")
    
    template_name = f'main/member_form{get_template_suffix()}.html'
    return render_template(template_name, 
                         user=session['user'], 
                         member=member, 
                         is_edit=bool(member_id),
                         leader_branch_id=leader_branch_id,
                         leader_branch_name=leader_branch_name,
                         leader_country=leader_country,
                         zones=zones)
@main_bp.route('/member/<member_id>')
def member_details(member_id):
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    r = redirect_deputy_to_attendance()
    if r:
        return r
    try:
        # Get specific member details
        # Use the user ID directly as leader_id
        leader_id = get_effective_leader_id()
        
        # Get member with leader filter (exclude leader's own self-row)
        result = (
            supabase.table('cell_members')
            .select('*')
            .eq('id', member_id)
            .eq('leader_id', leader_id)
            .neq('is_leader', True)
            .execute()
        )
        
        if result.data:
            member = result.data[0]
            
            # Fetch zone name if zone_id exists
            zone_name = None
            if member.get('zone_id'):
                try:
                    zone_result = supabase.table('zones').select('name').eq('id', member['zone_id']).limit(1).execute()
                    if zone_result.data and len(zone_result.data) > 0:
                        zone_name = zone_result.data[0].get('name')
                except Exception as e:
                    print(f"Error fetching zone name: {e}")
            
            # Add zone_name to member data for template
            member['zone_name'] = zone_name

            delete_request_pending, flag_issue_pending, deputy_removal_pending = (
                pending_flagged_state_for_member(supabase, leader_id, member_id)
            )

            template_name = f'main/member_details{get_template_suffix()}.html'
            return render_template(
                template_name,
                member=member,
                user=session['user'],
                delete_request_pending=delete_request_pending,
                flag_issue_pending=flag_issue_pending,
                deputy_removal_pending=deputy_removal_pending,
            )
        else:
            flash('Member not found', 'error')
            return redirect(url_for('main.members'))
    except Exception as e:
        print(f"Error loading member details: {e}")
        flash(f'Error loading member details: {str(e)}', 'error')
        return redirect(url_for('main.members'))
@main_bp.route('/add_member', methods=['POST'])
def add_member():
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    # Form validation
    form_errors = {}
    name = request.form.get('name', '').strip()
    age = request.form.get('age', '').strip()
    phone_number = request.form.get('phone_number', '').strip()
    # Validate required fields
    if not name:
        form_errors['name'] = 'Full name is required'
    elif not re.match(r'^[A-Za-z\s]{2,100}$', name):
        form_errors['name'] = 'Name must be 2-100 characters and contain only letters and spaces'
    # Validate age if provided
    if age:
        try:
            age_int = int(age)
            if age_int < 1 or age_int > 120:
                form_errors['age'] = 'Age must be between 1 and 120'
        except ValueError:
            form_errors['age'] = 'Age must be a valid number'
    # Validate phone number if provided
    if phone_number and not re.match(r'^[0-9]{10,15}$', phone_number):
        form_errors['phone_number'] = 'Phone number must be 10-15 digits'
    # If there are validation errors, return to form with errors
    if form_errors:
        # Get leader location context for autofill
        leader_id = get_effective_leader_id()
        leader_branch_id, leader_country, leader_branch_name = get_leader_location_context(leader_id)
        
        # Fetch zones for error case with sector_number from sectors table
        zones = []
        try:
            zones_result = supabase.table('zones').select('*').order('id').execute()
            if zones_result.data:
                # For each zone, fetch the sector_number from sectors table if sector_id exists
                for zone in zones_result.data:
                    sector_number = None
                    if zone.get('sector_id'):
                        try:
                            sector_result = supabase.table('sectors').select('sector_number').eq('id', zone['sector_id']).limit(1).execute()
                            if sector_result.data and len(sector_result.data) > 0:
                                sector_number = sector_result.data[0].get('sector_number')
                        except Exception as e:
                            print(f"Error fetching sector for zone {zone.get('id')}: {e}")
                    # Add sector_number to zone data
                    zone['sector_number'] = sector_number
                    zones.append(zone)
        except Exception as e:
            print(f"Error fetching zones: {e}")
        
        template_name = f'main/member_form{get_template_suffix()}.html'
        return render_template(template_name, 
                             user=session['user'], 
                             form_errors=form_errors,
                             leader_branch_id=leader_branch_id,
                             leader_branch_name=leader_branch_name,
                             leader_country=leader_country,
                             zones=zones)
    try:
        # Use the user ID directly as leader_id (since role_id = 4 users ARE leaders)
        leader_id = get_effective_leader_id()
        
        # Get leader location context for autofill
        leader_branch_id, leader_country, leader_branch_name = get_leader_location_context(leader_id)
        
        # Get form values or use leader's values for autofill
        country = request.form.get('country') or leader_country
        branch_id = request.form.get('branch_id') or leader_branch_id
        cell_category = request.form.get('cell_category') or None
        church = request.form.get('church') == 'true'  # Convert checkbox to boolean
        potential_leader = request.form.get('potential_leader') == 'true'  # Convert checkbox to boolean
        
        # Get zone_id and sector_number - convert empty strings to None
        zone_id = request.form.get('zone_id', '').strip()
        if zone_id:
            try:
                zone_id = int(zone_id)
            except (ValueError, TypeError):
                zone_id = None
        else:
            zone_id = None
        
        sector_number = request.form.get('sector_number', '').strip()
        if sector_number:
            try:
                sector_number = int(sector_number)
            except (ValueError, TypeError):
                sector_number = None
        else:
            sector_number = None
        
        # Prepare member data
        member_data = {
            'leader_id': leader_id,
            'name': name,
            'age': int(age) if age else None,
            'gender': request.form.get('gender') or None,
            'phone_number': phone_number or None,
            'zone_id': zone_id,
            'country': country,
            'branch_id': branch_id,
            'cell_category': cell_category,
            'church': church,
            'potential_leader': potential_leader,
            'sector_number': sector_number,
            'district': request.form.get('district') or None,
            'province': request.form.get('province') or None
        }
        # Insert into database
        print(f"Attempting to insert member with leader_id: {leader_id}")
        print(f"Member data: {member_data}")
        # Try to insert the member
        result = supabase.table('cell_members').insert(member_data).execute()
        print(f"Insert result: {result.data}")
        if result.data:
            # Log activity
            log_activity(
                leader_id=leader_id,
                user_id=leader_id,
                activity_type='member_added',
                description=f'Added new member: {name}',
                user_role='leader',
                user_name=session['user'].get('name', 'Leader'),
                source='cell_app',
                platform='web',
                details={
                    'member_name': name,
                    'member_id': result.data[0]['id'] if result.data else None
                }
            )
            flash('Member added successfully!', 'success')
            return redirect(url_for('main.members'))
        else:
            flash('Error adding member to database', 'error')
            # Get leader's branch_id and country for autofill
            leader_id = get_effective_leader_id()
            leader_branch_id = None
            leader_country = None
            try:
                user_result = supabase.table('users').select('branch_id, country').eq('id', leader_id).execute()
                if user_result.data and len(user_result.data) > 0:
                    leader_branch_id = user_result.data[0].get('branch_id')
                    leader_country = user_result.data[0].get('country')
            except Exception as e:
                print(f"Error fetching leader's branch_id and country: {e}")
            
            # Fetch zones for error case
            zones = []
            try:
                zones_result = supabase.table('zones').select('*').order('id').execute()
                if zones_result.data:
                    zones = zones_result.data
            except Exception as e:
                print(f"Error fetching zones: {e}")
            
            template_name = f'main/member_form{get_template_suffix()}.html'
            return render_template(template_name, 
                                 user=session['user'], 
                                 form_errors={'general': 'Failed to add member to database'},
                                 leader_branch_id=leader_branch_id,
                                 leader_branch_name=leader_branch_name,
                                 leader_country=leader_country,
                                 zones=zones)
    except Exception as e:
        error_msg = str(e)
        flash(f'Error adding member: {error_msg}', 'error')
        # Get leader location context for autofill
        leader_id = get_effective_leader_id()
        leader_branch_id, leader_country, leader_branch_name = get_leader_location_context(leader_id)
        
        # Fetch zones for error case with sector_number from sectors table
        zones = []
        try:
            zones_result = supabase.table('zones').select('*').order('id').execute()
            if zones_result.data:
                # For each zone, fetch the sector_number from sectors table if sector_id exists
                for zone in zones_result.data:
                    sector_number = None
                    if zone.get('sector_id'):
                        try:
                            sector_result = supabase.table('sectors').select('sector_number').eq('id', zone['sector_id']).limit(1).execute()
                            if sector_result.data and len(sector_result.data) > 0:
                                sector_number = sector_result.data[0].get('sector_number')
                        except Exception as e:
                            print(f"Error fetching sector for zone {zone.get('id')}: {e}")
                    # Add sector_number to zone data
                    zone['sector_number'] = sector_number
                    zones.append(zone)
        except Exception as e:
            print(f"Error fetching zones: {e}")
        
        template_name = f'main/member_form{get_template_suffix()}.html'
        return render_template(template_name, 
                             user=session['user'], 
                             form_errors={'general': error_msg},
                             leader_branch_id=leader_branch_id,
                             leader_branch_name=leader_branch_name,
                             leader_country=leader_country,
                             zones=zones)
@main_bp.route('/update_member/<member_id>', methods=['POST'])
def update_member(member_id):
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    try:
        # Use the user ID directly as leader_id
        leader_id = get_effective_leader_id()
        
        # Get leader's branch_id and country for autofill
        leader_branch_id = None
        leader_country = None
        try:
            user_result = supabase.table('users').select('branch_id, country').eq('id', leader_id).execute()
            if user_result.data and len(user_result.data) > 0:
                leader_branch_id = user_result.data[0].get('branch_id')
                leader_country = user_result.data[0].get('country')
        except Exception as e:
            print(f"Error fetching leader's branch_id and country: {e}")
        
        # Get form values or use leader's values for autofill
        country = request.form.get('country') or leader_country
        branch_id = request.form.get('branch_id') or leader_branch_id
        cell_category = request.form.get('cell_category') or None
        church = request.form.get('church') == 'true'  # Convert checkbox to boolean
        potential_leader = request.form.get('potential_leader') == 'true'  # Convert checkbox to boolean
        
        # Get zone_id and sector_number - convert empty strings to None
        zone_id = request.form.get('zone_id', '').strip()
        if zone_id:
            try:
                zone_id = int(zone_id)
            except (ValueError, TypeError):
                zone_id = None
        else:
            zone_id = None
        
        sector_number = request.form.get('sector_number', '').strip()
        if sector_number:
            try:
                sector_number = int(sector_number)
            except (ValueError, TypeError):
                sector_number = None
        else:
            sector_number = None
        
        member_data = {
            'name': request.form.get('name'),
            'age': int(request.form.get('age')) if request.form.get('age') else None,
            'gender': request.form.get('gender') or None,
            'phone_number': request.form.get('phone_number') or None,
            'zone_id': zone_id,
            'country': country,
            'branch_id': branch_id,
            'cell_category': cell_category,
            'church': church,
            'potential_leader': potential_leader,
            'sector_number': sector_number,
            'district': request.form.get('district') or None,
            'province': request.form.get('province') or None
        }
        result = (
            supabase.table('cell_members')
            .update(member_data)
            .eq('id', member_id)
            .eq('leader_id', leader_id)
            .neq('is_leader', True)
            .execute()
        )
        # Log activity
        if result.data:
            log_activity(
                leader_id=leader_id,
                user_id=leader_id,
                activity_type='member_updated',
                description=f'Updated member: {member_data["name"]}',
                user_role='leader',
                user_name=session['user'].get('name', 'Leader'),
                source='cell_app',
                platform='web',
                details={
                    'member_name': member_data['name'],
                    'member_id': member_id
                }
            )
        flash('Member updated successfully!', 'success')
        return redirect(url_for('main.member_details', member_id=member_id))
    except Exception as e:
        print(f"Error updating member: {e}")
        flash(f'Error updating member: {str(e)}', 'error')
        return redirect(url_for('main.member_details', member_id=member_id))
@main_bp.route('/request_delete_member/<member_id>', methods=['POST'])
def request_delete_member(member_id):
    """Create a delete request for a member using flagged_issues (issue_type='delete_request')."""
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    try:
        leader_id = get_effective_leader_id()

        # Verify member belongs to this leader and get basic info
        # (exclude the leader's own self-row so they can't delete themselves)
        member_result = (
            supabase.table('cell_members')
            .select('id, name')
            .eq('id', member_id)
            .eq('leader_id', leader_id)
            .neq('is_leader', True)
            .execute()
        )
        if not member_result.data:
            flash('Member not found or you do not have permission to request deletion for this member', 'error')
            return redirect(url_for('main.members'))

        member = member_result.data[0]
        member_name = member.get('name', 'Unknown')

        # Check if there is already a pending delete request for this member/leader
        existing_request = (
            supabase.table('flagged_issues')
            .select('id')
            .eq('member_id', member_id)
            .eq('leader_id', leader_id)
            .eq('issue_type', 'delete_request')
            .eq('status', 'pending')
            .limit(1)
            .execute()
        )
        if existing_request.data:
            flash('A delete request is already pending for this member.', 'info')
            return redirect(url_for('main.member_details', member_id=member_id))

        # Create a new flagged issue as a delete request
        flag_data = {
            'member_id': member_id,
            'leader_id': leader_id,
            'issue_type': 'delete_request',
            'description': f'Request to delete member {member_name}',
            'status': 'pending',
        }

        result = supabase.table('flagged_issues').insert(flag_data).execute()

        if result.data:
            # Log activity for audit trail
            try:
                log_activity(
                    leader_id=leader_id,
                    user_id=leader_id,
                    activity_type='member_delete_requested',
                    description=f'Requested deletion of member: {member_name}',
                    user_role='leader',
                    user_name=session['user'].get('name', 'Leader'),
                    source='cell_app',
                    platform='web',
                    details={
                        'member_id': member_id,
                        'member_name': member_name,
                        'flag_id': result.data[0].get('id') if result.data else None,
                    },
                )
            except Exception as e:
                print(f"Error logging delete request activity: {e}")

            flash('Delete request sent to cell portal for review.', 'success')
        else:
            flash('Error submitting delete request.', 'error')

        return redirect(url_for('main.member_details', member_id=member_id))
    except Exception as e:
        print(f"Error requesting member delete: {e}")
        flash(f'Error requesting member delete: {str(e)}', 'error')
        return redirect(url_for('main.member_details', member_id=member_id))
@main_bp.route('/meeting-tutorials/<meeting_date>')
def meeting_tutorials(meeting_date):
    """Display tutorial for specific meeting date or show 'No Tutorials' if none exists"""
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    r = redirect_deputy_to_attendance()
    if r:
        return r
    try:
        # Get leader ID - use user ID directly
        leader_id = get_effective_leader_id()
        
        # Convert meeting_date from URL format to database format
        from datetime import datetime
        parsed_date = None
        meeting_date_formatted = meeting_date
        try:
            # Parse the date string (e.g., "September 16, 2025")
            parsed_date = datetime.strptime(meeting_date, "%B %d, %Y").date()
            meeting_date_formatted = parsed_date.isoformat()
        except ValueError:
            # e.g. ISO "2026-04-08" from an old bookmark or manual URL
            alt = _parse_meeting_date_value(meeting_date)
            if alt:
                parsed_date = alt
                meeting_date_formatted = alt.isoformat()

        # Note: tutorials filtered by leader's users.cell_category
        leader_cat = fetch_leader_cell_category(supabase, leader_id)
        tutorials = load_tutorials_for_meeting_day(meeting_date_formatted, parsed_date, cell_category=leader_cat)
        tutorial_chip_rows = build_weekly_tutorial_dashboard_rows(tutorials)
        tutorial_legacy_sections = build_tutorial_legacy_sections(tutorials)

        # Check if this is the next meeting date (use corrected tutorial logic)
        next_meeting_date = get_tutorial_meeting_date_corrected()
        is_next_week = (
            parsed_date is not None
            and next_meeting_date is not None
            and parsed_date == next_meeting_date
        )

        # Always use meeting_tutorials.html: it fills both mobile_content and desktop_content.
        # meeting_tutorials_mobile.html only defined mobile_content, so at viewport >= 992px
        # base.html hides mobile layout and the page looked empty on tablets / desktop with a mobile UA.
        template_name = 'main/meeting_tutorials.html'
        return render_template(
            template_name,
            user=session['user'],
            meeting_date=meeting_date,
            tutorials=tutorials,
            tutorial_chip_rows=tutorial_chip_rows,
            tutorial_legacy_sections=tutorial_legacy_sections,
            leader_cell_category=leader_cat,
            is_next_week=is_next_week,
            is_latest=False,
            no_tutorial_uploaded=len(tutorials) == 0,
        )
                                 
    except Exception as e:
        traceback.print_exc()
        print(f"Error fetching tutorials: {e}")
        flash('Error loading tutorials', 'error')
        return redirect(url_for('main.index'))
@main_bp.route('/upload-tutorial/<meeting_date>', methods=['POST'])
def upload_tutorial(meeting_date):
    """Upload tutorial for a specific meeting date"""
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    try:
        tutorial_name = request.form.get('tutorial_name')
        tutorial_description = request.form.get('tutorial_description', '')
        if not tutorial_name:
            flash('Tutorial name is required', 'error')
            return redirect(url_for('main.meeting_tutorials', meeting_date=meeting_date))
        # Get leader ID - use user ID directly
        leader_id = get_effective_leader_id()
        # Convert meeting_date string to proper date format
        from datetime import datetime
        try:
            parsed_date = datetime.strptime(meeting_date, "%B %d, %Y").date()
            meeting_date_formatted = parsed_date.isoformat()
        except ValueError:
            meeting_date_formatted = meeting_date
        # Insert tutorial into database
        # Note: tutorials table doesn't have leader_id column, so we don't include it
        tutorial_data = {
            'tutorial_name': tutorial_name,
            'description': tutorial_description,
            'meeting_date': meeting_date_formatted,
            'uploaded_at': datetime.now().isoformat()
        }
        result = supabase.table('tutorials').insert(tutorial_data).execute()
        if result.data:
            # Log tutorial upload activity
            log_activity(
                leader_id=leader_id,
                user_id=leader_id,
                activity_type='tutorial_uploaded',
                description=f'Uploaded tutorial: {tutorial_name} for {meeting_date}',
                user_role='leader',
                user_name=session['user'].get('name', 'Leader'),
                source='cell_app',
                platform='web',
                details={
                    'tutorial_name': tutorial_name,
                    'meeting_date': meeting_date,
                    'description': tutorial_description
                }
            )
            flash('Tutorial uploaded successfully!', 'success')
        else:
            flash('Error uploading tutorial', 'error')
        return redirect(url_for('main.meeting_tutorials', meeting_date=meeting_date))
    except Exception as e:
        print(f"Error uploading tutorial: {e}")
        flash('Error uploading tutorial', 'error')
        return redirect(url_for('main.meeting_tutorials', meeting_date=meeting_date))

@main_bp.route('/tutorials-list')
def tutorials_list():
    """Show all tutorials with status - only for meetings in meetings table"""
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    r = redirect_deputy_to_attendance()
    if r:
        return r
    try:
        # Get pagination parameters
        page = request.args.get('page', 1, type=int)
        per_page = 5  # 5 tutorials per page
        
        # Get leader ID - use user ID directly
        leader_id = get_effective_leader_id()
        
        leader_cat = fetch_leader_cell_category(supabase, leader_id)
        tutorial_list = []
        try:
            # Get user's created date to filter meetings
            user_created_date = get_user_created_date(leader_id)
            
            # Get ALL meetings from meetings table (no limit yet), filtered by user's creation date
            query = supabase.table('meetings').select('*')
            if user_created_date:
                query = query.gte('meeting_date', user_created_date.isoformat())
            meetings_result = query\
                .order('meeting_date', desc=True)\
                .execute()
            
            if not meetings_result.data:
                print("No meetings found in database for tutorials")
                template_name = f'main/tutorials_list{get_template_suffix()}.html'
                return render_template(template_name,
                                     tutorial_list=[],
                                     user=session['user'])
            
            today = app_today()
            
            # Process each meeting from the meetings table
            for meeting in meetings_result.data:
                meeting_date = meeting.get('meeting_date')
                if not meeting_date:
                    continue
                
                # Parse meeting date
                try:
                    if isinstance(meeting_date, str):
                        try:
                            parsed_date = datetime.strptime(meeting_date, "%Y-%m-%d").date()
                        except ValueError:
                            try:
                                parsed_date = datetime.strptime(meeting_date, "%Y-%m-%dT%H:%M:%S").date()
                            except ValueError:
                                parsed_date = datetime.strptime(meeting_date.split('T')[0], "%Y-%m-%d").date()
                    else:
                        parsed_date = meeting_date
                    
                    # Additional safety check: skip meetings before user creation
                    if user_created_date and parsed_date < user_created_date:
                        continue
                    
                    meeting_date_iso = parsed_date.isoformat()
                    
                    has_tutorial = False
                    is_placeholder_tutorial = False
                    tutorial_record = None
                    if leader_cat:
                        tutorial_result = (
                            supabase.table('tutorials')
                            .select('*')
                            .eq('meeting_date', meeting_date_iso)
                            .eq('cell_category', leader_cat)
                            .execute()
                        )
                        has_tutorial = len(tutorial_result.data) > 0 if tutorial_result.data else False
                        if has_tutorial and tutorial_result.data:
                            tutorial_record = tutorial_result.data[0]
                            is_placeholder_tutorial = (
                                tutorial_record.get('title') == 'No Tutorial Uploaded'
                                or tutorial_record.get('title') == ''
                            )
                    
                    # Determine if this is upcoming or past
                    is_upcoming = parsed_date > today
                    
                    tutorial_list.append({
                        'date': parsed_date.strftime("%B %d, %Y"),
                        'date_iso': meeting_date_iso,
                        'has_tutorial': has_tutorial,
                        'is_placeholder': is_placeholder_tutorial,
                        'is_upcoming': is_upcoming,
                        'status': 'updated' if has_tutorial and not is_placeholder_tutorial else 'not_updated',
                        'tutorial_name': tutorial_record.get('title', 'No Tutorial') if has_tutorial else None,
                        'description': tutorial_record.get('description', '') if has_tutorial else None,
                        'sort_date': parsed_date  # Add sort_date for sorting
                    })
                except Exception as date_error:
                    print(f"Error parsing meeting date {meeting_date}: {date_error}")
                    continue
            
            # Sort tutorials: upcoming first, then past tutorials (most recent first)
            tutorial_list.sort(key=lambda x: (not x['is_upcoming'], -x['sort_date'].toordinal()))
            
        except Exception as e:
            print(f"Error fetching tutorial list: {e}")
            import traceback
            traceback.print_exc()
            tutorial_list = []
        
        # Calculate pagination
        total_tutorials = len(tutorial_list)
        total_pages = (total_tutorials + per_page - 1) // per_page  # Ceiling division
        
        # Ensure page is within valid range
        if page < 1:
            page = 1
        elif page > total_pages and total_pages > 0:
            page = total_pages
        
        # Get tutorials for current page
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_tutorials = tutorial_list[start_idx:end_idx]
        
        template_name = f'main/tutorials_list{get_template_suffix()}.html'
        return render_template(template_name,
                             tutorial_list=paginated_tutorials,
                             page=page,
                             total_pages=total_pages,
                             total_tutorials=total_tutorials,
                             user=session['user'])
    except Exception as e:
        print(f"Error fetching tutorials list: {e}")
        import traceback
        traceback.print_exc()
        flash('Error loading tutorials list', 'error')
        return redirect(url_for('main.index'))

@main_bp.route('/attendance-list')
def attendance_list():
    """Show attendance list with marked (complete) and unmarked (incomplete) tabs"""
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    
    try:
        # Get pagination parameters for marked section
        marked_page = request.args.get('marked_page', 1, type=int)
        per_page = 5  # 5 attendance records per page
        
        # Get leader ID - use user ID directly
        leader_id = get_effective_leader_id()
        
        # Get user's created date to filter meetings
        user_created_date = get_user_created_date(leader_id)
        
        # Get ALL meetings from meetings table, filtered by user's creation date
        query = supabase.table('meetings').select('*')
        if user_created_date:
            query = query.gte('meeting_date', user_created_date.isoformat())
        
        meetings_result = query\
            .order('meeting_date', desc=True)\
            .execute()
        
        unmarked_list = []
        marked_list = []
        
        if meetings_result.data:
            for meeting in meetings_result.data:
                meeting_date = meeting.get('meeting_date')
                if not meeting_date:
                    continue
                
                try:
                    # Parse meeting date
                    if isinstance(meeting_date, str):
                        try:
                            parsed_date = datetime.strptime(meeting_date, "%Y-%m-%d").date()
                        except ValueError:
                            try:
                                parsed_date = datetime.strptime(meeting_date, "%Y-%m-%dT%H:%M:%S").date()
                            except ValueError:
                                parsed_date = datetime.strptime(meeting_date.split('T')[0], "%Y-%m-%d").date()
                    else:
                        parsed_date = meeting_date
                    
                    # Skip meetings before user creation
                    if user_created_date and parsed_date < user_created_date:
                        continue
                    
                    meeting_date_str = parsed_date.strftime("%B %d, %Y")
                    meeting_date_iso = parsed_date.isoformat()
                    
                    # Get members eligible for this meeting week (created on or before
                    # the end of the marking window, i.e. that week's Thursday).
                    meeting_members_query = supabase.table('cell_members').select('id,created_at').eq('leader_id', leader_id)
                    meeting_members_query = meeting_members_query.lte('created_at', get_member_attendance_cutoff_iso(parsed_date))
                    meeting_members_result = meeting_members_query.execute()
                    meeting_member_ids = [
                        member['id'] for member in (meeting_members_result.data or [])
                        if member_created_within_attendance_window(member.get('created_at'), parsed_date)
                    ]
                    meeting_total_members = len(meeting_member_ids)
                    
                    # Get attendance records for this meeting
                    week_attendance_count = 0
                    present_count = 0
                    absent_count = 0
                    
                    if meeting_member_ids:
                        week_attendance_result = supabase.table('attendance')\
                            .select('*')\
                            .eq('leader_id', leader_id)\
                            .eq('meeting_date', meeting_date_iso)\
                            .in_('member_id', meeting_member_ids)\
                            .execute()
                        
                        week_attendance_count = len(week_attendance_result.data) if week_attendance_result.data else 0
                        
                        # Count present/absent
                        if week_attendance_result.data:
                            for record in week_attendance_result.data:
                                if record.get('status') == 'present':
                                    present_count += 1
                                elif record.get('status') == 'absent':
                                    absent_count += 1
                    
                    # Determine status for this meeting
                    if meeting_total_members > 0 and week_attendance_count == meeting_total_members:
                        week_status = 'complete'
                    elif week_attendance_count > 0:
                        week_status = 'partial'
                    else:
                        week_status = 'incomplete'
                    
                    # Check if this is an upcoming meeting
                    today = app_today()
                    is_upcoming = parsed_date > today
                    
                    attendance_item = {
                        'date': meeting_date_str,
                        'date_iso': meeting_date_iso,
                        'date_obj': parsed_date,
                        'status': week_status,
                        'count': week_attendance_count,
                        'total': meeting_total_members,
                        'present_count': present_count,
                        'absent_count': absent_count,
                        'is_upcoming': is_upcoming
                    }
                    
                    # Separate into marked (complete) and unmarked (incomplete/partial)
                    if week_status == 'complete':
                        marked_list.append(attendance_item)
                    else:
                        unmarked_list.append(attendance_item)
                        
                except Exception as date_error:
                    print(f"Error parsing meeting date {meeting_date}: {date_error}")
                    continue
        
        # Sort: unmarked by date (most recent first), marked by date (most recent first)
        unmarked_list.sort(key=lambda x: x.get('date_obj', app_today()), reverse=True)
        marked_list.sort(key=lambda x: x.get('date_obj', app_today()), reverse=True)

        all_iso = []
        for item in unmarked_list:
            if item.get('date_iso'):
                all_iso.append(item['date_iso'])
        for item in marked_list:
            if item.get('date_iso'):
                all_iso.append(item['date_iso'])
        submitted_iso_set = fetch_submitted_meeting_dates(leader_id, all_iso)
        for item in unmarked_list:
            st = attendance_edit_state(item['date_obj'], submitted_iso_set)
            item['can_mark_attendance'] = st['can_mark_attendance']
            item['attendance_submitted'] = st['attendance_submitted']
            item['locked_reason'] = st['locked_reason']
        for item in marked_list:
            st = attendance_edit_state(item['date_obj'], submitted_iso_set)
            item['can_mark_attendance'] = st['can_mark_attendance']
            item['attendance_submitted'] = st['attendance_submitted']
            item['locked_reason'] = st['locked_reason']
        
        # Calculate pagination for marked list
        total_marked = len(marked_list)
        total_marked_pages = (total_marked + per_page - 1) // per_page  # Ceiling division
        
        # Ensure marked_page is within valid range
        if marked_page < 1:
            marked_page = 1
        elif marked_page > total_marked_pages and total_marked_pages > 0:
            marked_page = total_marked_pages
        
        # Get marked attendance for current page
        start_idx = (marked_page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_marked_list = marked_list[start_idx:end_idx]
        
        template_name = f'main/attendance_list{get_template_suffix()}.html'
        return render_template(template_name,
                             attendance_list=unmarked_list,  # For backward compatibility
                             unmarked_list=unmarked_list,
                             marked_list=paginated_marked_list,
                             marked_page=marked_page,
                             total_marked_pages=total_marked_pages,
                             total_marked=total_marked,
                             user=session['user'])
    except Exception as e:
        print(f"Error fetching attendance list: {e}")
        import traceback
        traceback.print_exc()
        flash('Error loading attendance list', 'error')
        return redirect(url_for('main.index'))

@main_bp.route('/flag_member/<member_id>', methods=['POST'])
def flag_member(member_id):
    """Handle flagging a member with an issue"""
    if 'user' not in session:
        return redirect(url_for('auth.login'))
    
    try:
        leader_id = get_effective_leader_id()
        
        # Verify member belongs to this leader (exclude leader's own self-row)
        member_result = (
            supabase.table('cell_members')
            .select('*')
            .eq('id', member_id)
            .eq('leader_id', leader_id)
            .neq('is_leader', True)
            .execute()
        )
        
        if not member_result.data:
            flash('Member not found or you do not have permission to flag this member', 'error')
            return redirect(url_for('main.member_details', member_id=member_id))

        _, flag_pend, _ = pending_flagged_state_for_member(supabase, leader_id, member_id)
        if flag_pend:
            flash('A flag is already pending review for this member.', 'info')
            return redirect(url_for('main.member_details', member_id=member_id))
        
        # Get form data
        issue_type = request.form.get('issue_type', '').strip()
        description = request.form.get('description', '').strip()
        
        # Validate required fields
        if not description:
            flash('Description is required', 'error')
            return redirect(url_for('main.member_details', member_id=member_id))
        
        # Prepare flag data
        flag_data = {
            'member_id': member_id,
            'leader_id': leader_id,
            'issue_type': issue_type if issue_type else None,
            'description': description,
            'status': 'pending'
        }
        
        # Insert into database
        result = supabase.table('flagged_issues').insert(flag_data).execute()
        
        if result.data:
            # Log activity
            try:
                log_activity(
                    leader_id=leader_id,
                    user_id=leader_id,
                    activity_type='member_flagged',
                    description=f'Flagged issue for member: {member_result.data[0].get("name", "Unknown")}',
                    user_role='leader',
                    user_name=session['user'].get('name', 'Leader'),
                    source='cell_app',
                    platform='web',
                    details={
                        'member_id': member_id,
                        'member_name': member_result.data[0].get('name', 'Unknown'),
                        'issue_type': issue_type,
                        'flag_id': result.data[0]['id'] if result.data else None
                    }
                )
            except Exception as e:
                print(f"Error logging activity: {e}")
            
            flash('Member flagged successfully!', 'success')
        else:
            flash('Error flagging member', 'error')
        
        return redirect(url_for('main.member_details', member_id=member_id))
        
    except Exception as e:
        print(f"Error flagging member: {e}")
        import traceback
        traceback.print_exc()
        flash(f'Error flagging member: {str(e)}', 'error')
        return redirect(url_for('main.member_details', member_id=member_id))

@main_bp.route('/toggle_potential_leader/<member_id>', methods=['POST'])
def toggle_potential_leader(member_id):
    """Toggle potential_leader status for a member"""
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    try:
        leader_id = get_effective_leader_id()
        
        # Get current member data to verify ownership (skip leader's own self-row)
        member_result = (
            supabase.table('cell_members')
            .select('potential_leader')
            .eq('id', member_id)
            .eq('leader_id', leader_id)
            .neq('is_leader', True)
            .execute()
        )
        
        if not member_result.data or len(member_result.data) == 0:
            return jsonify({'success': False, 'message': 'Member not found or access denied'}), 404
        
        # Get the new potential_leader value from request
        data = request.get_json()
        new_value = data.get('potential_leader', False)
        
        # Update the potential_leader status (skip leader's own self-row)
        result = (
            supabase.table('cell_members')
            .update({'potential_leader': bool(new_value)})
            .eq('id', member_id)
            .eq('leader_id', leader_id)
            .neq('is_leader', True)
            .execute()
        )
        
        if result.data:
            # Log activity
            try:
                member_name = result.data[0].get('name', 'Unknown')
                log_activity(
                    leader_id=leader_id,
                    user_id=leader_id,
                    activity_type='member_updated',
                    description=f'Updated potential leader status for {member_name}',
                    user_role='leader',
                    user_name=session['user'].get('name', 'Leader'),
                    source='cell_app',
                    platform='web',
                    details={
                        'member_id': member_id,
                        'member_name': member_name,
                        'potential_leader': bool(new_value)
                    }
                )
            except Exception as e:
                print(f"Error logging activity: {e}")
            
            return jsonify({
                'success': True,
                'message': f'Potential leader status updated successfully',
                'potential_leader': bool(new_value)
            })
        else:
            return jsonify({'success': False, 'message': 'Failed to update potential leader status'}), 500
            
    except Exception as e:
        print(f"Error toggling potential leader: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Error updating status: {str(e)}'}), 500


@main_bp.route('/set_deputy_leader/<member_id>', methods=['POST'])
def set_deputy_leader(member_id):
    """Assign a member as deputy leader when none is set. Replacing a deputy requires portal-approved removal first."""
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    if session['user'].get('is_deputy'):
        return jsonify({'success': False, 'message': 'Only the leader can assign a deputy.'}), 403
    try:
        leader_id = get_effective_leader_id()
        if leader_has_pending_deputy_removal_request(supabase, leader_id):
            return jsonify({
                'success': False,
                'message': 'A deputy change is already waiting for cell portal approval. Wait for it to finish before assigning.',
            }), 400

        member_result = (
            supabase.table('cell_members')
            .select('id, name, deputy_leader')
            .eq('id', member_id)
            .eq('leader_id', leader_id)
            .neq('is_leader', True)
            .execute()
        )
        if not member_result.data or len(member_result.data) == 0:
            return jsonify({'success': False, 'message': 'Member not found or access denied'}), 404
        member = member_result.data[0]
        if member.get('deputy_leader'):
            return jsonify({'success': False, 'message': 'This member is already the deputy leader.'}), 400

        try:
            existing = supabase.table('cell_members').select('id').eq('leader_id', leader_id).eq('deputy_leader', True).execute()
            if existing.data and str(existing.data[0]['id']) != str(member_id):
                return jsonify({
                    'success': False,
                    'message': (
                        'A deputy is already assigned. Use “Request change of deputy” on their card '
                        'and wait for cell portal approval to remove them before choosing a new deputy.'
                    ),
                }), 400
        except Exception as ex:
            print(f"Error checking existing deputy: {ex}")

        result = (
            supabase.table('cell_members')
            .update({'deputy_leader': True, 'can_login': True})
            .eq('id', member_id)
            .eq('leader_id', leader_id)
            .neq('is_leader', True)
            .execute()
        )
        if result.data:
            try:
                log_activity(
                    leader_id=leader_id,
                    user_id=leader_id,
                    activity_type='deputy_leader_assigned',
                    description=f'Assigned {member.get("name", "Unknown")} as deputy leader',
                    user_role='leader',
                    user_name=session['user'].get('name', 'Leader'),
                    source='cell_app',
                    platform='web',
                    details={'member_id': member_id, 'member_name': member.get('name')}
                )
            except Exception as e:
                print(f"Error logging activity: {e}")
            return jsonify({'success': True, 'message': 'Deputy leader assigned.'})
        return jsonify({'success': False, 'message': 'Failed to assign deputy leader'}), 500
    except Exception as e:
        print(f"Error setting deputy leader: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@main_bp.route('/request_deputy_removal/<member_id>', methods=['POST'])
def request_deputy_removal(member_id):
    """
    Leader requests removal of the current deputy via flagged_issues (issue_type=deputy_removal_request).
    Cell portal should approve, then clear deputy_leader/can_login on that member row.
    """
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    if session['user'].get('is_deputy'):
        return jsonify({'success': False, 'message': 'Only the cell leader can request a deputy change.'}), 403
    try:
        leader_id = get_effective_leader_id()
        member_result = (
            supabase.table('cell_members')
            .select('id, name, deputy_leader')
            .eq('id', member_id)
            .eq('leader_id', leader_id)
            .neq('is_leader', True)
            .execute()
        )
        if not member_result.data:
            return jsonify({'success': False, 'message': 'Member not found or access denied.'}), 404
        member = member_result.data[0]
        if not member.get('deputy_leader'):
            return jsonify({'success': False, 'message': 'This member is not the deputy leader.'}), 400

        if leader_has_pending_deputy_removal_request(supabase, leader_id):
            return jsonify({'success': False, 'message': 'A deputy change request is already pending for your cell.'}), 400

        member_name = member.get('name', 'Unknown')
        flag_data = {
            'member_id': member_id,
            'leader_id': leader_id,
            'issue_type': 'deputy_removal_request',
            'description': f'Leader requested removal of deputy status for {member_name} (assign new deputy after approval).',
            'status': 'pending',
        }
        result = supabase.table('flagged_issues').insert(flag_data).execute()
        if result.data:
            try:
                log_activity(
                    leader_id=leader_id,
                    user_id=leader_id,
                    activity_type='deputy_removal_requested',
                    description=f'Requested deputy removal for: {member_name}',
                    user_role='leader',
                    user_name=session['user'].get('name', 'Leader'),
                    source='cell_app',
                    platform='web',
                    details={'member_id': member_id, 'member_name': member_name, 'flag_id': result.data[0].get('id')},
                )
            except Exception as e:
                print(f"Error logging deputy removal request: {e}")
            return jsonify({
                'success': True,
                'message': 'Deputy change request sent to the cell portal for approval.',
            })
        return jsonify({'success': False, 'message': 'Could not submit request.'}), 500
    except Exception as e:
        print(f"Error requesting deputy removal: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


