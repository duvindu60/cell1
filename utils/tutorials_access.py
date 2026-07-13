"""Filter tutorials by leader users.cell_category — shared by routes and API."""
import re
from datetime import date, datetime

# Legacy single-resource titles, e.g. "Tutorial PDF (Tamil) — Weekly Meeting - July 14, 2026"
_LEGACY_RESOURCE_TITLE_RE = re.compile(
    r'(?i)^\s*tutorial\s+(pdf|video)\b'
)
# Generic weekly labels we rebuild ourselves (with consistent date formatting)
_GENERIC_WEEKLY_TITLE_RE = re.compile(
    r'(?i)^\s*weekly\s+meeting(\b|[\s\-—–:].*)?$'
)
_PLACEHOLDER_TITLES = frozenset({
    '',
    'tutorial',
    'no tutorial',
    'no tutorial uploaded',
})


def parse_tutorial_meeting_date(meeting_date):
    """Parse tutorials.meeting_date to a date; return None if invalid."""
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


def str_url(val):
    if val is None:
        return ''
    return str(val).strip()


def is_legacy_tutorial_resource_title(title):
    """True for old single PDF/video titles that should not be shown as section headings."""
    if title is None:
        return False
    s = str(title).strip()
    if not s:
        return False
    return bool(_LEGACY_RESOURCE_TITLE_RE.match(s))


def usable_custom_tutorial_title(title):
    """
    Return a stripped custom title worth showing, or None.
    Skips placeholders, legacy "Tutorial PDF/Video (...)" names, and generic Weekly Meeting labels.
    """
    if title is None:
        return None
    s = str(title).strip()
    if not s:
        return None
    if s.lower() in _PLACEHOLDER_TITLES:
        return None
    if is_legacy_tutorial_resource_title(s):
        return None
    if _GENERIC_WEEKLY_TITLE_RE.match(s):
        return None
    return s


def format_tutorial_section_heading(meeting_date, *, include_date=True, raw_title=None):
    """
    Clean section heading for tutorial list cards and language-chip panels.

    Prefer a real custom title when present; otherwise "Weekly Meeting"
    (optionally with meeting date). Never surfaces legacy resource titles.
    """
    custom = usable_custom_tutorial_title(raw_title)
    if custom:
        return custom

    pd = meeting_date if isinstance(meeting_date, date) and not isinstance(meeting_date, datetime) else parse_tutorial_meeting_date(meeting_date)
    if include_date and pd:
        return f"Weekly Meeting — {pd.strftime('%B %d, %Y')}"
    return 'Weekly Meeting'


def tutorial_row_raw_title(row):
    """Best raw title field from a tutorials row (does not clean)."""
    if not isinstance(row, dict):
        return None
    return row.get('title') or row.get('tutorial_name')


def sinhala_pdf_url(row):
    if not isinstance(row, dict):
        return ''
    u = str_url(row.get('pdf_url'))
    if not u:
        u = str_url(row.get('pdf_url_1'))
    return u


def sinhala_video_url(row):
    if not isinstance(row, dict):
        return ''
    u = str_url(row.get('video_url_1'))
    if not u:
        u = str_url(row.get('video_url'))
    return u


def fetch_leader_cell_category(supabase, leader_id):
    if not supabase or not leader_id:
        return None
    try:
        res = (
            supabase.table('users')
            .select('cell_category')
            .eq('id', leader_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        c = res.data[0].get('cell_category')
        if c is None:
            return None
        s = str(c).strip()
        return s or None
    except Exception as e:
        print(f"fetch_leader_cell_category: {e}")
        return None


def fetch_tutorials_for_my_cell(supabase, leader_id):
    """
    Returns { 'cell_category': str | None, 'data': list }.
    Rows sorted by meeting_date descending.
    """
    cat = fetch_leader_cell_category(supabase, leader_id)
    if not cat:
        return {'cell_category': None, 'data': []}
    try:
        res = (
            supabase.table('tutorials')
            .select('*')
            .eq('cell_category', cat)
            .order('meeting_date', desc=True)
            .execute()
        )
        return {'cell_category': cat, 'data': res.data or []}
    except Exception as e:
        print(f"fetch_tutorials_for_my_cell: {e}")
        return {'cell_category': cat, 'data': []}


def build_weekly_tutorial_dashboard_rows(tutorial_rows):
    """
    Template-friendly rows: heading, date_str (for meeting_tutorials URL), slots with pdf/video per language.
    """
    rows = []
    for row in tutorial_rows or []:
        if not isinstance(row, dict):
            continue
        pd = parse_tutorial_meeting_date(row.get('meeting_date'))
        date_str = pd.strftime("%B %d, %Y") if pd else None

        heading = format_tutorial_section_heading(
            pd,
            include_date=True,
            raw_title=tutorial_row_raw_title(row),
        )

        slots = [
            {'label': 'Sinhala', 'pdf': sinhala_pdf_url(row) or None, 'video': sinhala_video_url(row) or None},
            {'label': 'English', 'pdf': str_url(row.get('pdf_url_2')) or None, 'video': str_url(row.get('video_url_2')) or None},
            {'label': 'Tamil', 'pdf': str_url(row.get('pdf_url_3')) or None, 'video': str_url(row.get('video_url_3')) or None},
        ]
        rows.append({
            'heading': heading,
            'date_str': date_str,
            'uploaded_at': row.get('uploaded_at'),
            'slots': slots,
        })
    return rows
