# CellApp - Complete Architecture Documentation

## Table of Contents
1. [Project Overview](#project-overview)
2. [System Architecture](#system-architecture)
3. [Database Schema](#database-schema)
4. [Route Documentation](#route-documentation)
5. [Function Documentation](#function-documentation)
6. [Utility Functions](#utility-functions)
7. [Template Structure](#template-structure)
8. [Issues & Bugs Found](#issues--bugs-found)
9. [Unused Code](#unused-code)
10. [Duplicate Logic](#duplicate-logic)
11. [Refactoring Recommendations](#refactoring-recommendations)
12. [Folder Restructuring](#folder-restructuring)
13. [Naming Convention Improvements](#naming-convention-improvements)
14. [Role-Based Access Control](#role-based-access-control)
15. [Security Considerations](#security-considerations)

---

## Project Overview

**CellApp** is a Flask-based web application for managing cell group members, attendance, tutorials, and meetings. The application uses Supabase as the backend database and implements a mobile-first responsive design.

### Key Technologies
- **Framework**: Flask 2.3.3
- **Database**: Supabase (PostgreSQL)
- **Session Management**: Flask-Session
- **Template Engine**: Jinja2
- **Deployment**: Gunicorn (WSGI)

### Application Features
1. **Authentication**: Mobile number + password login
2. **Member Management**: CRUD operations for cell members
3. **Attendance Tracking**: Mark attendance for meetings
4. **Tutorial Management**: Upload and view tutorials for meetings
5. **Meeting Management**: View and manage meeting dates
6. **Activity Logging**: Comprehensive activity tracking
7. **Member Flagging**: Flag members with issues/concerns
8. **Dashboard**: Overview of members, attendance, and tutorials

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Flask Application                      │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Auth BP    │  │   Main BP    │  │    API BP    │      │
│  │  (auth.py)   │  │  (main.py)   │  │   (api.py)   │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                  │                  │              │
│         └──────────────────┼──────────────────┘              │
│                            │                                  │
│  ┌────────────────────────┼──────────────────────────┐      │
│  │                    Utils Layer                      │      │
│  │  ┌──────────────┐         ┌──────────────┐         │      │
│  │  │ Activity     │         │ Device       │         │      │
│  │  │ Logger       │         │ Detector     │         │      │
│  │  └──────────────┘         └──────────────┘         │      │
│  └────────────────────────────────────────────────────┘      │
│                            │                                  │
└────────────────────────────┼──────────────────────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │   Supabase      │
                    │   (PostgreSQL)  │
                    └─────────────────┘
```

### Blueprint Structure
- **auth_bp**: Authentication routes (`/login`, `/logout`)
- **main_bp**: Main application routes (dashboard, members, attendance, tutorials)
- **api_bp**: API endpoints (`/api/user`, `/api/health`, `/api/test`)

---

## Database Schema

### Tables

#### 1. `users`
- **Purpose**: User authentication and basic info
- **Key Fields**: `id`, `phone_number`, `password`, `role_id`, `name`, `email`
- **Notes**: `role_id = 4` indicates a leader

#### 2. `leaders`
- **Purpose**: Leader information
- **Key Fields**: `id` (UUID), `user_id`, `name`, `email`
- **Relations**: Links to `users` table

#### 3. `cell_members`
- **Purpose**: Cell group members
- **Key Fields**: `id`, `leader_id`, `name`, `age`, `gender`, `phone_number`, `ministry`, `zone`, `created_at`
- **Relations**: `leader_id` → `leaders.id`

#### 4. `meetings`
- **Purpose**: Meeting dates and information
- **Key Fields**: `id`, `meeting_date`, `meeting_name`, `meeting_number`
- **Notes**: No `leader_id` column (shared across all leaders)

#### 5. `attendance`
- **Purpose**: Attendance records
- **Key Fields**: `id`, `leader_id`, `member_id`, `meeting_date`, `meeting_number`, `status` (present/absent)
- **Relations**: `leader_id` → `leaders.id`, `member_id` → `cell_members.id`

#### 6. `tutorials`
- **Purpose**: Tutorial materials for meetings
- **Key Fields**: `id`, `meeting_date`, `tutorial_name`/`title`, `description`, `uploaded_at`
- **Notes**: No `leader_id` column (shared across all leaders)

#### 7. `activities`
- **Purpose**: Activity logging and audit trail
- **Key Fields**: `id` (UUID), `leader_id`, `user_id`, `activity_type`, `activity_category`, `description`, `source`, `platform`, `activity_date`, `activity_time`, `created_at`, `details` (JSONB), `metadata` (JSONB)
- **Relations**: `leader_id` → `leaders.id`

#### 8. `flagged_issues`
- **Purpose**: Issues flagged for members
- **Key Fields**: `id`, `member_id`, `leader_id`, `issue_type`, `description`, `status`, `response`, `response_by`, `created_at`, `updated_at`
- **Relations**: `member_id` → `cell_members.id`, `leader_id` → `leaders.id`

---

## Route Documentation

### Authentication Routes (`routes/auth.py`)

#### `GET/POST /login`
- **Purpose**: User login
- **Method**: GET (display form), POST (process login)
- **Authentication**: None (public)
- **Parameters**: 
  - POST: `mobile` (10 digits), `password`
- **Validation**: 
  - Mobile: 10 digits, numeric
  - Password: Required
- **Logic**:
  1. Validates mobile format
  2. Queries `users` table for `role_id = 4` and matching credentials
  3. Creates session with user data
  4. Logs activity (`user_login`)
  5. Redirects to dashboard
- **Error Handling**: Flash messages for validation errors

#### `GET /logout`
- **Purpose**: User logout
- **Method**: GET
- **Authentication**: None (clears session)
- **Logic**: Removes user from session, redirects to login

---

### Main Application Routes (`routes/main.py`)

#### `GET /`
- **Purpose**: Dashboard/home page
- **Authentication**: Required (redirects to login if not authenticated)
- **Data Fetched**:
  - Member count
  - Next meeting date (tutorial logic)
  - Current attendance date
  - Tutorial status for next meeting
  - Attendance status for current week
  - Quick access lists (tutorials, attendance)
- **Templates**: `main/dashboard.html` or `main/dashboard_mobile.html`

#### `GET /profile`
- **Purpose**: User profile page
- **Authentication**: Required
- **Templates**: `main/profile.html` or `main/profile_mobile.html`

#### `GET /meeting-dates`
- **Purpose**: List all meeting dates
- **Authentication**: Required
- **Data Fetched**: Meetings from `meetings` table (last 20)
- **Fallback**: Calculated past Tuesdays if no meetings found
- **Templates**: `main/meeting_dates.html` or `main/meeting_dates_mobile.html`

#### `GET /attendance/<meeting_date>`
- **Purpose**: Display attendance page for specific meeting
- **Authentication**: Required
- **Parameters**: `meeting_date` (format: "September 16, 2025")
- **Data Fetched**:
  - All members for current leader
  - Existing attendance records for the meeting date
- **Templates**: `main/attendance_detail.html` or `main/attendance_detail_mobile.html`

#### `POST /update_attendance/<meeting_date>`
- **Purpose**: Update attendance for a single member
- **Authentication**: Required
- **Method**: POST (JSON response)
- **Parameters**: 
  - `member_id`: Member ID
  - `status`: 'present', 'absent', or 'clear'
- **Logic**:
  1. Validates member belongs to leader
  2. Gets meeting_number from meetings table
  3. Inserts or updates attendance record
  4. Logs activity (`attendance_marked`)
- **Returns**: JSON response with success/error

#### `POST /bulk_update_attendance/<meeting_date>`
- **Purpose**: Bulk update attendance for multiple members
- **Authentication**: Required
- **Method**: POST (JSON)
- **Parameters**: JSON body with `attendance` array: `[{member_id, status}, ...]`
- **Logic**: Processes each attendance record, logs bulk activity
- **Returns**: JSON with success count and errors

#### `GET /members`
- **Purpose**: List all cell members
- **Authentication**: Required
- **Data Fetched**: All members for current leader
- **Templates**: `main/members.html` or `main/members_mobile.html`

#### `GET /member/form`
- **Purpose**: Display member add/edit form
- **Authentication**: Required
- **Parameters**: `?edit=<member_id>` (optional, for editing)
- **Templates**: `main/member_form.html` or `main/member_form_mobile.html`

#### `GET /member/<member_id>`
- **Purpose**: Display member details
- **Authentication**: Required
- **Parameters**: `member_id` (URL parameter)
- **Data Fetched**: Member details (validated against leader)
- **Templates**: `main/member_details.html` or `main/member_details_mobile.html`

#### `POST /add_member`
- **Purpose**: Add new member
- **Authentication**: Required
- **Method**: POST
- **Parameters**: `name`, `age`, `gender`, `phone_number`, `ministry`, `zone`
- **Validation**:
  - Name: 2-100 chars, letters and spaces only
  - Age: 1-120 (optional)
  - Phone: 10-15 digits (optional)
- **Logic**:
  1. Validates form data
  2. Inserts into `cell_members` table
  3. Logs activity (`member_added`)
- **Redirects**: To `/members` on success

#### `POST /update_member/<member_id>`
- **Purpose**: Update existing member
- **Authentication**: Required
- **Method**: POST
- **Parameters**: Same as add_member
- **Logic**: Updates member record, logs activity (`member_updated`)
- **Redirects**: To `/member/<member_id>` on success

#### `POST /delete_member/<member_id>`
- **Purpose**: Delete member
- **Authentication**: Required
- **Method**: POST
- **Logic**: 
  1. Validates member belongs to leader
  2. Deletes member
  3. Logs activity (`member_deleted`)
- **Redirects**: To `/members` on success

#### `GET /meeting-tutorials/<meeting_date>`
- **Purpose**: Display tutorials for a meeting
- **Authentication**: Required
- **Parameters**: `meeting_date` (format: "September 16, 2025")
- **Data Fetched**: Tutorials for the meeting date
- **Templates**: `main/meeting_tutorials.html` or `main/meeting_tutorials_mobile.html`

#### `POST /upload-tutorial/<meeting_date>`
- **Purpose**: Upload tutorial for a meeting
- **Authentication**: Required
- **Method**: POST
- **Parameters**: `tutorial_name`, `tutorial_description`
- **Logic**: Inserts tutorial, logs activity (`tutorial_uploaded`)
- **Redirects**: To `/meeting-tutorials/<meeting_date>`

#### `GET /tutorials-list`
- **Purpose**: List all tutorials with status
- **Authentication**: Required
- **Data Fetched**: Tutorials for meetings from `meetings` table
- **Templates**: `main/tutorials_list.html` or `main/tutorials_list_mobile.html`

#### `GET /attendance-list`
- **Purpose**: List all attendance records with status
- **Authentication**: Required
- **Data Fetched**: Attendance records for meetings from `meetings` table
- **Templates**: `main/attendance_list.html` or `main/attendance_list_mobile.html`

#### `POST /flag_member/<member_id>`
- **Purpose**: Flag a member with an issue
- **Authentication**: Required
- **Method**: POST
- **Parameters**: `issue_type` (optional), `description` (required)
- **Validation**: Description is required
- **Logic**:
  1. Validates member belongs to leader
  2. Inserts into `flagged_issues` table
  3. Logs activity (`member_flagged`)
- **Redirects**: To `/member/<member_id>` on success

---

### API Routes (`routes/api.py`)

#### `GET /api/user`
- **Purpose**: Get current user information
- **Authentication**: Required (decorator: `@login_required`)
- **Returns**: JSON with user session data

#### `GET /api/health`
- **Purpose**: Health check endpoint
- **Authentication**: None (public)
- **Returns**: JSON with status

#### `GET /api/test`
- **Purpose**: Test endpoint for authenticated users
- **Authentication**: Required
- **Returns**: JSON with test message and user_id

---

## Function Documentation

### Helper Functions (`routes/main.py`)

#### `get_uuid_from_user_id(user_id)`
- **Purpose**: Convert user_id to leader UUID
- **Status**: ⚠️ **DEFINED BUT UNUSED**
- **Logic**:
  1. Queries `leaders` table for matching `user_id`
  2. If not found, creates new leader record
  3. Fallback: Generates UUID from MD5 hash
- **Issue**: Function is defined but never called. Code uses `session['user']['id']` directly as `leader_id`

#### `get_tutorial_meeting_date_corrected()`
- **Purpose**: Calculate next Tuesday for tutorials
- **Logic**:
  - Tuesday 12:00 AM - 11:59 PM: Current Tuesday
  - Wednesday 12:00 AM: Switch to next Tuesday
  - Wednesday - Monday: Next Tuesday
- **Returns**: `date` object

#### `get_attendance_meeting_date_corrected()`
- **Purpose**: Calculate current Tuesday for attendance
- **Logic**:
  - Tuesday: Current Tuesday
  - Wednesday - Monday: Same Tuesday (current week)
  - Monday midnight: Switch to next Tuesday
- **Returns**: `date` object

#### `get_past_tuesdays()`
- **Purpose**: Calculate past 4 Tuesday dates
- **Returns**: List of 4 date strings (formatted: "September 16, 2025")

---

## Utility Functions

### Activity Logger (`utils/activity_logger.py`)

#### `log_activity(...)`
- **Purpose**: Log activities to database
- **Parameters**:
  - `leader_id`: UUID of leader
  - `user_id`: User ID from session
  - `activity_type`: Type of activity
  - `description`: Human-readable description
  - `user_role`: Role (default: 'leader')
  - `user_name`: Name of user
  - `source`: 'cell_app' or 'cell_portal'
  - `platform`: 'web', 'mobile', etc.
  - `details`: Optional JSON dict
  - `metadata`: Optional JSON dict
- **Returns**: `bool` (success/failure)

#### `get_recent_activities(...)`
- **Purpose**: Get recent activities with filters
- **Parameters**: `leader_id`, `limit`, optional filters
- **Returns**: List of activities

#### `get_activities_by_date(...)`
- **Purpose**: Get activities for specific date
- **Returns**: List of activities

#### `get_activities_by_role(...)`
- **Purpose**: Get activities by user role
- **Returns**: List of activities

#### `get_activities_by_type(...)`
- **Purpose**: Get activities by activity type
- **Returns**: List of activities

#### `get_activities_by_category(...)`
- **Purpose**: Get activities by category
- **Returns**: List of activities

#### `get_todays_activities(leader_id)`
- **Purpose**: Get today's activities
- **Returns**: List of activities

#### `get_activity_statistics(...)`
- **Purpose**: Get activity statistics
- **Returns**: Dictionary with counts by type, category, role, source, date

### Device Detector (`utils/device_detector.py`)

#### `is_mobile_device()`
- **Purpose**: Detect if request is from mobile device
- **Logic**: Checks User-Agent string against mobile patterns
- **Returns**: `bool`

#### `get_device_type()`
- **Purpose**: Get device type as string
- **Returns**: `'mobile'` or `'desktop'`

#### `get_template_suffix()`
- **Purpose**: Get template suffix for device-specific templates
- **Returns**: `'_mobile'` or `''`

---

## Template Structure

### Base Template
- `templates/base.html`: Base template with navigation, mobile/desktop blocks

### Authentication Templates
- `templates/auth/login.html`: Desktop login form
- `templates/auth/login_mobile.html`: Mobile login form

### Main Application Templates
All templates have mobile and desktop versions:

#### Dashboard
- `templates/main/dashboard.html`
- `templates/main/dashboard_mobile.html`

#### Members
- `templates/main/members.html`
- `templates/main/members_mobile.html`
- `templates/main/member_form.html`
- `templates/main/member_form_mobile.html`
- `templates/main/member_details.html`
- `templates/main/member_details_mobile.html`

#### Attendance
- `templates/main/attendance_detail.html`
- `templates/main/attendance_detail_mobile.html`
- `templates/main/attendance_list.html`
- `templates/main/attendance_list_mobile.html`

#### Meetings
- `templates/main/meeting_dates.html`
- `templates/main/meeting_dates_mobile.html`

#### Tutorials
- `templates/main/meeting_tutorials.html`
- `templates/main/meeting_tutorials_mobile.html`
- `templates/main/tutorials_list.html`
- `templates/main/tutorials_list_mobile.html`

#### Profile
- `templates/main/profile.html`
- `templates/main/profile_mobile.html`

### Macros
- `templates/macros/components.html`: Reusable template components

---

## Issues & Bugs Found

### Critical Bugs

#### 1. **Undefined Variable: `session_user_id`**
- **Location**: `routes/main.py` (lines 747, 797, 905, 1242)
- **Issue**: Variable `session_user_id` is used but never defined
- **Impact**: Will cause `NameError` when logging activities
- **Fix**: Replace with `session['user']['id']` or `leader_id`

```python
# Current (BROKEN):
user_id=session_user_id,

# Should be:
user_id=leader_id,  # or session['user']['id']
```

#### 2. **Unused Function: `get_uuid_from_user_id`**
- **Location**: `routes/main.py` (lines 21-48)
- **Issue**: Function is defined but never called
- **Impact**: Dead code, unnecessary complexity
- **Recommendation**: Remove or implement if needed for future use

### Code Quality Issues

#### 3. **Duplicate Date Parsing Logic**
- **Location**: Multiple places in `routes/main.py`
- **Issue**: Date parsing code is repeated in multiple functions
- **Example**: Lines 214-224, 348-357, 476-485, 1300-1310, etc.
- **Recommendation**: Extract to utility function

#### 4. **Inconsistent Error Handling**
- **Location**: Throughout `routes/main.py`
- **Issue**: Some functions use try/except, others don't
- **Recommendation**: Standardize error handling

#### 5. **Hardcoded Role ID**
- **Location**: `routes/auth.py` (line 42)
- **Issue**: `role_id = 4` is hardcoded
- **Recommendation**: Move to configuration constant

#### 6. **Missing Input Validation**
- **Location**: Various routes
- **Issue**: Some routes don't validate input properly
- **Example**: `member_id` in URL parameters not validated for format

---

## Unused Code

### Functions
1. **`get_uuid_from_user_id()`** - Defined but never called
   - Location: `routes/main.py:21-48`
   - Status: Can be removed or documented for future use

### Variables
1. **`session_user_id`** - Referenced but never defined
   - Location: `routes/main.py` (multiple locations)
   - Status: Bug - needs to be fixed

### Imports
- All imports appear to be used

---

## Duplicate Logic

### 1. Date Parsing
**Occurrences**: Lines 214-224, 348-357, 476-485, 636-639, 708-713, 838-842, 1176-1182, 1224-1228, 1300-1310, 1408-1418, 1465-1474

**Pattern**:
```python
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
```

**Recommendation**: Extract to utility function:
```python
def parse_meeting_date(date_input):
    """Parse meeting date from various formats"""
    if isinstance(date_input, str):
        for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%B %d, %Y"]:
            try:
                return datetime.strptime(date_input.split('T')[0] if 'T' in date_input else date_input, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Unable to parse date: {date_input}")
    return date_input
```

### 2. Meeting Date Formatting
**Occurrences**: Multiple locations
**Pattern**: `parsed_date.strftime("%B %d, %Y")`
**Recommendation**: Use constant or utility function

### 3. Leader ID Retrieval
**Occurrences**: Every route in `main.py`
**Pattern**: `leader_id = session['user']['id']`
**Recommendation**: Extract to decorator or helper function

### 4. Authentication Check
**Occurrences**: Every protected route
**Pattern**: 
```python
if 'user' not in session:
    return redirect(url_for('auth.login'))
```
**Recommendation**: Create decorator `@login_required`

### 5. Template Name Generation
**Occurrences**: Every route
**Pattern**: `f'main/{template_name}{get_template_suffix()}.html'`
**Recommendation**: Extract to helper function

---

## Refactoring Recommendations

### High Priority

1. **Fix `session_user_id` Bug**
   - Replace all occurrences with `leader_id` or `session['user']['id']`
   - Affected lines: 747, 797, 905, 1242

2. **Extract Date Parsing Utility**
   - Create `utils/date_helpers.py`
   - Move all date parsing logic there
   - Update all routes to use utility

3. **Create Authentication Decorator**
   - Add to `utils/decorators.py` or `routes/decorators.py`
   - Replace repeated auth checks

4. **Extract Leader ID Helper**
   - Create helper function for getting leader_id
   - Centralize leader validation logic

### Medium Priority

5. **Standardize Error Handling**
   - Create error handling decorator
   - Consistent error responses

6. **Extract Configuration Constants**
   - Move hardcoded values to `config.py`
   - Example: `ROLE_ID_LEADER = 4`

7. **Create Service Layer**
   - Extract business logic from routes
   - Create `services/` directory
   - Examples: `MemberService`, `AttendanceService`, `TutorialService`

8. **Improve Input Validation**
   - Create validation utilities
   - Use Flask-WTF for form validation (optional)

### Low Priority

9. **Remove Unused Code**
   - Remove `get_uuid_from_user_id` or document its purpose

10. **Optimize Database Queries**
    - Review N+1 query patterns
    - Add database query logging

11. **Add Type Hints**
    - Add type hints to all functions
    - Improve IDE support and documentation

---

## Folder Restructuring

### Current Structure
```
cellapp/
├── app.py
├── config.py
├── wsgi.py
├── routes/
│   ├── __init__.py
│   ├── auth.py
│   ├── main.py
│   └── api.py
├── utils/
│   ├── activity_logger.py
│   └── device_detector.py
├── templates/
│   ├── auth/
│   ├── main/
│   └── macros/
├── static/
└── database/
    └── migrations/
```

### Recommended Structure
```
cellapp/
├── app.py                    # Application factory
├── config.py                # Configuration
├── wsgi.py                  # WSGI entry point
├── routes/                   # Route handlers (thin controllers)
│   ├── __init__.py
│   ├── auth.py
│   ├── main.py
│   └── api.py
├── services/                 # Business logic layer (NEW)
│   ├── __init__.py
│   ├── member_service.py
│   ├── attendance_service.py
│   ├── tutorial_service.py
│   └── meeting_service.py
├── utils/                    # Utility functions
│   ├── __init__.py
│   ├── activity_logger.py
│   ├── device_detector.py
│   ├── date_helpers.py      # NEW: Date parsing utilities
│   ├── decorators.py        # NEW: Decorators (auth, error handling)
│   └── validators.py        # NEW: Input validation
├── models/                   # Data models (NEW - optional)
│   ├── __init__.py
│   ├── member.py
│   └── attendance.py
├── templates/
│   ├── auth/
│   ├── main/
│   └── macros/
├── static/
├── database/
│   └── migrations/
└── tests/                    # NEW: Test directory
    ├── __init__.py
    ├── test_auth.py
    ├── test_members.py
    └── test_attendance.py
```

### Benefits
- **Separation of Concerns**: Business logic separated from routes
- **Testability**: Services can be tested independently
- **Maintainability**: Easier to find and modify code
- **Scalability**: Easier to add new features

---

## Naming Convention Improvements

### Current Issues

1. **Inconsistent Function Names**
   - `get_tutorial_meeting_date_corrected()` - "corrected" is unclear
   - `get_attendance_meeting_date_corrected()` - same issue
   - **Recommendation**: `get_next_tutorial_meeting_date()`, `get_current_attendance_meeting_date()`

2. **Inconsistent Variable Names**
   - `member_id` vs `memberId` (should be consistent)
   - `leader_id` vs `leaderId`
   - **Recommendation**: Use snake_case consistently (Python convention)

3. **Unclear Route Names**
   - `/add_member` - should be `/members` (POST)
   - `/update_member/<id>` - should be `/members/<id>` (PUT)
   - `/delete_member/<id>` - should be `/members/<id>` (DELETE)
   - **Recommendation**: RESTful naming

4. **Template Naming**
   - Current: `member_details.html`, `member_details_mobile.html`
   - **Recommendation**: Keep current (clear and consistent)

### Recommended Naming

#### Functions
- Use verb_noun pattern: `get_member()`, `create_member()`, `update_member()`
- Boolean functions: `is_mobile_device()`, `has_tutorial()`
- Date functions: `get_next_meeting_date()`, `parse_meeting_date()`

#### Variables
- Use snake_case: `member_id`, `leader_id`, `meeting_date`
- Boolean: `is_authenticated`, `has_permission`
- Collections: `members_list`, `attendance_records`

#### Routes
- RESTful: `/members` (GET list, POST create), `/members/<id>` (GET, PUT, DELETE)
- Clear actions: `/attendance/<date>` (GET), `/attendance/<date>` (POST for update)

---

## Role-Based Access Control

### Current Implementation

#### Authentication
- **Method**: Session-based
- **Role Check**: `role_id = 4` indicates leader
- **Session Data**: `session['user']` contains `id`, `mobile`, `name`, `email`, `role_id`

#### Access Control
- **Current**: All authenticated users have same access (all are leaders)
- **Leader ID**: Uses `session['user']['id']` directly as `leader_id`
- **Data Isolation**: All queries filter by `leader_id` to ensure data isolation

### Issues

1. **No Role Hierarchy**
   - Only one role (leader) is implemented
   - No admin, super_admin, or other roles

2. **Hardcoded Role ID**
   - `role_id = 4` is hardcoded in auth
   - Should be in configuration

3. **No Permission System**
   - No granular permissions
   - All leaders have same permissions

4. **No Role Decorators**
   - No `@admin_required` or `@super_admin_required`
   - All routes use same auth check

### Recommendations

1. **Create Role Constants**
   ```python
   # config.py or constants.py
   class Roles:
       LEADER = 4
       ADMIN = 3
       SUPER_ADMIN = 2
   ```

2. **Create Role Decorators**
   ```python
   # utils/decorators.py
   def role_required(*roles):
       def decorator(f):
           @wraps(f)
           def decorated_function(*args, **kwargs):
               if 'user' not in session:
                   return redirect(url_for('auth.login'))
               if session['user'].get('role_id') not in roles:
                   flash('Access denied', 'error')
                   return redirect(url_for('main.index'))
               return f(*args, **kwargs)
           return decorated_function
       return decorator
   ```

3. **Create Permission System**
   - Define permissions: `view_members`, `edit_members`, `delete_members`, etc.
   - Check permissions before actions

4. **Separate Admin Routes**
   - Create `routes/admin.py` for admin-only routes
   - Use role decorators

---

## Security Considerations

### Current Security Measures

1. **Session Management**
   - HTTPOnly cookies
   - SameSite protection
   - Session timeout (1 hour)

2. **Input Validation**
   - Mobile number format validation
   - Name validation (regex)
   - Age range validation

3. **Data Isolation**
   - All queries filter by `leader_id`
   - Members can only be accessed by their leader

### Security Issues

1. **Password Storage**
   - ⚠️ **CRITICAL**: Passwords stored in plain text
   - **Fix**: Use password hashing (bcrypt, argon2)

2. **SQL Injection**
   - Using Supabase client (parameterized queries)
   - **Status**: ✅ Protected

3. **CSRF Protection**
   - ⚠️ **MISSING**: No CSRF tokens
   - **Fix**: Implement Flask-WTF CSRF protection

4. **XSS Protection**
   - Using Jinja2 auto-escaping
   - **Status**: ✅ Protected (default)

5. **Rate Limiting**
   - ⚠️ **MISSING**: No rate limiting on login
   - **Fix**: Implement rate limiting

6. **Session Fixation**
   - Session regenerated on login
   - **Status**: ✅ Protected (Flask default)

### Recommendations

1. **Implement Password Hashing**
   ```python
   from werkzeug.security import generate_password_hash, check_password_hash
   
   # On registration/update
   hashed_password = generate_password_hash(password)
   
   # On login
   if check_password_hash(stored_hash, password):
       # Login successful
   ```

2. **Add CSRF Protection**
   ```python
   from flask_wtf.csrf import CSRFProtect
   
   csrf = CSRFProtect(app)
   ```

3. **Add Rate Limiting**
   ```python
   from flask_limiter import Limiter
   
   limiter = Limiter(app, key_func=get_remote_address)
   
   @limiter.limit("5 per minute")
   @auth_bp.route('/login', methods=['POST'])
   def login():
       # ...
   ```

4. **Add Input Sanitization**
   - Sanitize all user inputs
   - Use HTML escaping for user-generated content

5. **Add Security Headers**
   ```python
   from flask_talisman import Talisman
   
   Talisman(app, force_https=False)  # Set True in production
   ```

---

## Summary

### Statistics
- **Total Routes**: 20
- **Total Functions**: 50+
- **Templates**: 24 (12 desktop + 12 mobile)
- **Database Tables**: 8
- **Critical Bugs**: 1 (`session_user_id`)
- **Unused Functions**: 1 (`get_uuid_from_user_id`)
- **Duplicate Code Blocks**: 5+ major patterns

### Priority Actions

1. **Immediate** (Critical Bugs):
   - Fix `session_user_id` undefined variable
   - Fix password storage (security)

2. **Short Term** (Code Quality):
   - Extract duplicate date parsing logic
   - Create authentication decorator
   - Remove unused code

3. **Medium Term** (Architecture):
   - Create service layer
   - Implement CSRF protection
   - Add rate limiting

4. **Long Term** (Enhancements):
   - Add role-based permissions
   - Implement comprehensive testing
   - Add API documentation

---

## Appendix: Route Summary Table

| Route | Method | Auth | Purpose | Template |
|-------|--------|------|---------|----------|
| `/login` | GET/POST | No | Login | `auth/login*.html` |
| `/logout` | GET | No | Logout | Redirect |
| `/` | GET | Yes | Dashboard | `main/dashboard*.html` |
| `/profile` | GET | Yes | Profile | `main/profile*.html` |
| `/meeting-dates` | GET | Yes | Meeting list | `main/meeting_dates*.html` |
| `/attendance/<date>` | GET | Yes | Attendance page | `main/attendance_detail*.html` |
| `/update_attendance/<date>` | POST | Yes | Update attendance | JSON |
| `/bulk_update_attendance/<date>` | POST | Yes | Bulk update | JSON |
| `/members` | GET | Yes | Member list | `main/members*.html` |
| `/member/form` | GET | Yes | Member form | `main/member_form*.html` |
| `/member/<id>` | GET | Yes | Member details | `main/member_details*.html` |
| `/add_member` | POST | Yes | Add member | Redirect |
| `/update_member/<id>` | POST | Yes | Update member | Redirect |
| `/delete_member/<id>` | POST | Yes | Delete member | Redirect |
| `/meeting-tutorials/<date>` | GET | Yes | Tutorials | `main/meeting_tutorials*.html` |
| `/upload-tutorial/<date>` | POST | Yes | Upload tutorial | Redirect |
| `/tutorials-list` | GET | Yes | Tutorial list | `main/tutorials_list*.html` |
| `/attendance-list` | GET | Yes | Attendance list | `main/attendance_list*.html` |
| `/flag_member/<id>` | POST | Yes | Flag member | Redirect |
| `/api/user` | GET | Yes | User info | JSON |
| `/api/health` | GET | No | Health check | JSON |
| `/api/test` | GET | Yes | Test endpoint | JSON |

---

**Document Generated**: 2025-01-27
**Version**: 1.0
**Author**: Senior Software Architect Analysis

