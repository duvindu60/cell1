"""
Comprehensive Activity Logging Utility
Tracks all activities from Cell App and Cell Portal
Supports date-wise, role-wise, and activity-wise tracking
"""

from supabase import create_client, Client
import os
from datetime import datetime, date, time
from typing import Optional, Dict, Any
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Supabase client
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_ANON_KEY")
supabase: Client = create_client(url, key) if url and key else None

# Debug: Check if Supabase is initialized
if not supabase:
    print("Warning: Supabase client not initialized in activity_logger.py")
    print(f"URL: {url}")
    print(f"Key: {'Present' if key else 'Missing'}")
else:
    print("Supabase client initialized successfully in activity_logger.py")

# Activity category mapping
ACTIVITY_CATEGORIES = {
    # Member activities
    'member_added': 'member',
    'member_updated': 'member',
    'member_deleted': 'member',
    'member_viewed': 'member',
    'member_imported': 'member',
    
    # Attendance activities
    'attendance_marked': 'attendance',
    'attendance_updated': 'attendance',
    'attendance_bulk_updated': 'attendance',
    'attendance_viewed': 'attendance',
    'attendance_exported': 'attendance',
    'attendance_sheet_updated': 'attendance',
    'attendance_sheet_completed': 'attendance',
    'attendance_incomplete': 'attendance',
    
    # Tutorial activities
    'tutorial_uploaded': 'tutorial',
    'tutorial_updated': 'tutorial',
    'tutorial_deleted': 'tutorial',
    'tutorial_viewed': 'tutorial',
    'tutorial_downloaded': 'tutorial',
    
    # Meeting activities
    'meeting_created': 'meeting',
    'meeting_updated': 'meeting',
    'meeting_deleted': 'meeting',
    'meeting_viewed': 'meeting',
    
    # Profile activities
    'profile_updated': 'profile',
    'profile_viewed': 'profile',
    'password_changed': 'profile',
    'settings_updated': 'profile',
    
    # System activities
    'user_login': 'system',
    'user_logout': 'system',
    'user_registered': 'system',
    'session_started': 'system',
    'session_ended': 'system',
    'password_reset_requested': 'system',
    'password_reset_completed': 'system',
    
    # Portal activities
    'report_generated': 'portal',
    'export_created': 'portal',
    'bulk_operation_performed': 'portal',
    'dashboard_viewed': 'portal',
    'analytics_viewed': 'portal',
}

def log_activity(
    leader_id: str,
    user_id: str,
    activity_type: str,
    description: str,
    user_role: str = 'leader',
    user_name: Optional[str] = None,
    source: str = 'cell_app',
    platform: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    activity_date: Optional[date] = None,
    is_important: bool = False,
    is_system: bool = False
) -> bool:
    """
    Log a comprehensive activity to the database
    
    Args:
        leader_id: UUID of the leader
        user_id: User ID from session/auth
        activity_type: Type of activity (member_added, attendance_marked, etc.)
        description: Human-readable description
        user_role: Role of the user (leader, admin, super_admin, etc.)
        user_name: Name of the user who performed the action
        source: Source of activity ('cell_app' or 'cell_portal')
        platform: Platform used ('web', 'mobile', 'api', 'admin_panel')
        details: Optional activity-specific details as JSON
        metadata: Optional metadata (IP address, user agent, etc.)
        activity_date: Date of the activity (defaults to today)
        is_important: Mark as important activity
        is_system: Mark as system-generated activity
    
    Returns:
        bool: True if successful, False otherwise
    """
    if not supabase:
        print("Warning: Supabase client not initialized, activity not logged")
        return False
    
    # Get activity category
    activity_category = ACTIVITY_CATEGORIES.get(activity_type, 'system')
    
    # Use current date/time if not provided
    now = datetime.now()
    if activity_date is None:
        activity_date = now.date()
    
    try:
        activity_data = {
            'leader_id': leader_id,
            'user_id': user_id,
            'user_role': user_role,
            'user_name': user_name,
            'activity_type': activity_type,
            'activity_category': activity_category,
            'description': description,
            'source': source,
            'platform': platform or ('mobile' if platform is None else 'web'),
            'activity_date': activity_date.isoformat(),
            'activity_time': now.time().isoformat(),
            'created_at': now.isoformat(),
            'details': details if details else {},
            'metadata': metadata if metadata else {},
            'is_important': is_important,
            'is_system': is_system
        }
        
        result = supabase.table('activities').insert(activity_data).execute()
        return True
        
    except Exception as e:
        print(f"Error logging activity: {e}")
        import traceback
        traceback.print_exc()
        return False

def get_recent_activities(
    leader_id: str,
    limit: int = 10,
    activity_type: Optional[str] = None,
    activity_category: Optional[str] = None,
    user_role: Optional[str] = None,
    source: Optional[str] = None
) -> list:
    """
    Get recent activities with optional filters
    
    Args:
        leader_id: UUID of the leader
        limit: Maximum number of activities to return
        activity_type: Filter by activity type
        activity_category: Filter by activity category
        user_role: Filter by user role
        source: Filter by source (cell_app or cell_portal)
    
    Returns:
        list: List of recent activities
    """
    if not supabase:
        return []
    
    try:
        query = supabase.table('activities')\
            .select('*')\
            .eq('leader_id', leader_id)
        
        if activity_type:
            query = query.eq('activity_type', activity_type)
        if activity_category:
            query = query.eq('activity_category', activity_category)
        if user_role:
            query = query.eq('user_role', user_role)
        if source:
            query = query.eq('source', source)
        
        result = query.order('created_at', desc=True).limit(limit).execute()
        return result.data if result.data else []
        
    except Exception as e:
        print(f"Error fetching activities: {e}")
        return []

def get_activities_by_date(
    leader_id: str,
    activity_date: date,
    activity_type: Optional[str] = None,
    user_role: Optional[str] = None
) -> list:
    """
    Get activities for a specific date (date-wise tracking)
    
    Args:
        leader_id: UUID of the leader
        activity_date: Date to filter activities
        activity_type: Optional filter by activity type
        user_role: Optional filter by user role
    
    Returns:
        list: List of activities for the date
    """
    if not supabase:
        return []
    
    try:
        query = supabase.table('activities')\
            .select('*')\
            .eq('leader_id', leader_id)\
            .eq('activity_date', activity_date.isoformat())
        
        if activity_type:
            query = query.eq('activity_type', activity_type)
        if user_role:
            query = query.eq('user_role', user_role)
        
        result = query.order('activity_time', desc=True).execute()
        return result.data if result.data else []
        
    except Exception as e:
        print(f"Error fetching activities by date: {e}")
        return []

def get_activities_by_role(
    leader_id: str,
    user_role: str,
    limit: int = 50,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> list:
    """
    Get activities by user role (role-wise tracking)
    
    Args:
        leader_id: UUID of the leader
        user_role: Role to filter (leader, admin, etc.)
        limit: Maximum number of activities to return
        start_date: Optional start date filter
        end_date: Optional end date filter
    
    Returns:
        list: List of activities by role
    """
    if not supabase:
        return []
    
    try:
        query = supabase.table('activities')\
            .select('*')\
            .eq('leader_id', leader_id)\
            .eq('user_role', user_role)
        
        if start_date:
            query = query.gte('activity_date', start_date.isoformat())
        if end_date:
            query = query.lte('activity_date', end_date.isoformat())
        
        result = query.order('created_at', desc=True).limit(limit).execute()
        return result.data if result.data else []
        
    except Exception as e:
        print(f"Error fetching activities by role: {e}")
        return []

def get_activities_by_type(
    leader_id: str,
    activity_type: str,
    limit: int = 50,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> list:
    """
    Get activities by activity type (activity-wise tracking)
    
    Args:
        leader_id: UUID of the leader
        activity_type: Type of activity to filter
        limit: Maximum number of activities to return
        start_date: Optional start date filter
        end_date: Optional end date filter
    
    Returns:
        list: List of activities by type
    """
    if not supabase:
        return []
    
    try:
        query = supabase.table('activities')\
            .select('*')\
            .eq('leader_id', leader_id)\
            .eq('activity_type', activity_type)
        
        if start_date:
            query = query.gte('activity_date', start_date.isoformat())
        if end_date:
            query = query.lte('activity_date', end_date.isoformat())
        
        result = query.order('created_at', desc=True).limit(limit).execute()
        return result.data if result.data else []
        
    except Exception as e:
        print(f"Error fetching activities by type: {e}")
        return []

def get_activities_by_category(
    leader_id: str,
    activity_category: str,
    limit: int = 50,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> list:
    """
    Get activities by category (member, attendance, tutorial, etc.)
    
    Args:
        leader_id: UUID of the leader
        activity_category: Category to filter
        limit: Maximum number of activities to return
        start_date: Optional start date filter
        end_date: Optional end date filter
    
    Returns:
        list: List of activities by category
    """
    if not supabase:
        return []
    
    try:
        query = supabase.table('activities')\
            .select('*')\
            .eq('leader_id', leader_id)\
            .eq('activity_category', activity_category)
        
        if start_date:
            query = query.gte('activity_date', start_date.isoformat())
        if end_date:
            query = query.lte('activity_date', end_date.isoformat())
        
        result = query.order('created_at', desc=True).limit(limit).execute()
        return result.data if result.data else []
        
    except Exception as e:
        print(f"Error fetching activities by category: {e}")
        return []

def get_todays_activities(leader_id: str) -> list:
    """
    Get today's activities for a leader
    
    Args:
        leader_id: UUID of the leader
    
    Returns:
        list: List of today's activities
    """
    today = date.today()
    return get_activities_by_date(leader_id, today)

def get_activity_statistics(
    leader_id: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> Dict[str, Any]:
    """
    Get activity statistics (counts by type, category, role, etc.)
    
    Args:
        leader_id: UUID of the leader
        start_date: Optional start date
        end_date: Optional end date
    
    Returns:
        dict: Statistics dictionary
    """
    if not supabase:
        return {}
    
    try:
        query = supabase.table('activities')\
            .select('activity_type, activity_category, user_role, source, activity_date')\
            .eq('leader_id', leader_id)
        
        if start_date:
            query = query.gte('activity_date', start_date.isoformat())
        if end_date:
            query = query.lte('activity_date', end_date.isoformat())
        
        result = query.execute()
        activities = result.data if result.data else []
        
        stats = {
            'total_activities': len(activities),
            'by_type': {},
            'by_category': {},
            'by_role': {},
            'by_source': {},
            'by_date': {}
        }
        
        for activity in activities:
            # Count by type
            activity_type = activity.get('activity_type', 'unknown')
            stats['by_type'][activity_type] = stats['by_type'].get(activity_type, 0) + 1
            
            # Count by category
            category = activity.get('activity_category', 'unknown')
            stats['by_category'][category] = stats['by_category'].get(category, 0) + 1
            
            # Count by role
            role = activity.get('user_role', 'unknown')
            stats['by_role'][role] = stats['by_role'].get(role, 0) + 1
            
            # Count by source
            source = activity.get('source', 'unknown')
            stats['by_source'][source] = stats['by_source'].get(source, 0) + 1
            
            # Count by date
            act_date = activity.get('activity_date', 'unknown')
            stats['by_date'][act_date] = stats['by_date'].get(act_date, 0) + 1
        
        return stats
        
    except Exception as e:
        print(f"Error fetching activity statistics: {e}")
        return {}

