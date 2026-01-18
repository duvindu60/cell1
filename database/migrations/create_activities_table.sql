-- ===========================================
-- Comprehensive Activity Tracking Table
-- Tracks all activities from Cell App and Cell Portal
-- ===========================================

-- Drop existing activities table if it exists (use with caution in production)
-- DROP TABLE IF EXISTS activities CASCADE;

-- Create new comprehensive activities table
CREATE TABLE IF NOT EXISTS activities (
    -- Primary Key
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- User and Leader Information
    leader_id UUID NOT NULL REFERENCES leaders(id) ON DELETE CASCADE,
    user_id VARCHAR(255) NOT NULL, -- User ID from session/auth
    user_role VARCHAR(50) NOT NULL DEFAULT 'leader', -- leader, admin, super_admin, etc.
    user_name VARCHAR(255), -- Name of the user who performed the action
    
    -- Activity Information
    activity_type VARCHAR(100) NOT NULL, -- member_added, attendance_marked, tutorial_uploaded, etc.
    activity_category VARCHAR(50) NOT NULL, -- member, attendance, tutorial, meeting, profile, system, etc.
    description TEXT NOT NULL, -- Human-readable description
    
    -- Source Information
    source VARCHAR(50) NOT NULL DEFAULT 'cell_app', -- cell_app or cell_portal
    platform VARCHAR(50), -- web, mobile, api, etc.
    
    -- Activity Date and Time
    activity_date DATE NOT NULL DEFAULT CURRENT_DATE, -- Date of the activity (for date-wise tracking)
    activity_time TIME NOT NULL DEFAULT CURRENT_TIME, -- Time of the activity
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(), -- Full timestamp
    
    -- Additional Data
    details JSONB, -- Flexible JSON for additional activity-specific data
    metadata JSONB, -- Additional metadata (IP address, user agent, etc.)
    
    -- Status and Flags
    is_important BOOLEAN DEFAULT FALSE, -- Mark important activities
    is_system BOOLEAN DEFAULT FALSE, -- System-generated activities
    
    -- Indexes for performance
    CONSTRAINT activities_leader_id_fkey FOREIGN KEY (leader_id) REFERENCES leaders(id) ON DELETE CASCADE
);

-- ===========================================
-- INDEXES for Fast Queries
-- ===========================================

-- Index for leader-based queries
CREATE INDEX IF NOT EXISTS idx_activities_leader_id ON activities(leader_id);

-- Index for date-wise tracking
CREATE INDEX IF NOT EXISTS idx_activities_activity_date ON activities(activity_date DESC);

-- Index for role-wise tracking
CREATE INDEX IF NOT EXISTS idx_activities_user_role ON activities(user_role);

-- Index for activity type tracking
CREATE INDEX IF NOT EXISTS idx_activities_activity_type ON activities(activity_type);

-- Index for activity category tracking
CREATE INDEX IF NOT EXISTS idx_activities_activity_category ON activities(activity_category);

-- Index for source tracking (cell_app vs cell_portal)
CREATE INDEX IF NOT EXISTS idx_activities_source ON activities(source);

-- Composite index for common queries: leader + date
CREATE INDEX IF NOT EXISTS idx_activities_leader_date ON activities(leader_id, activity_date DESC);

-- Composite index for role + date queries
CREATE INDEX IF NOT EXISTS idx_activities_role_date ON activities(user_role, activity_date DESC);

-- Composite index for activity type + date queries
CREATE INDEX IF NOT EXISTS idx_activities_type_date ON activities(activity_type, activity_date DESC);

-- Index for timestamp queries
CREATE INDEX IF NOT EXISTS idx_activities_created_at ON activities(created_at DESC);

-- Index for important activities
CREATE INDEX IF NOT EXISTS idx_activities_important ON activities(is_important) WHERE is_important = TRUE;

-- ===========================================
-- COMMENTS for Documentation
-- ===========================================

COMMENT ON TABLE activities IS 'Comprehensive activity tracking table for Cell App and Cell Portal';
COMMENT ON COLUMN activities.leader_id IS 'Foreign key to leaders table';
COMMENT ON COLUMN activities.user_id IS 'User ID from authentication system';
COMMENT ON COLUMN activities.user_role IS 'Role of the user: leader, admin, super_admin, etc.';
COMMENT ON COLUMN activities.activity_type IS 'Type of activity: member_added, attendance_marked, etc.';
COMMENT ON COLUMN activities.activity_category IS 'Category: member, attendance, tutorial, meeting, profile, system';
COMMENT ON COLUMN activities.source IS 'Source of activity: cell_app or cell_portal';
COMMENT ON COLUMN activities.activity_date IS 'Date of the activity for date-wise tracking';
COMMENT ON COLUMN activities.details IS 'JSONB field for activity-specific details';
COMMENT ON COLUMN activities.metadata IS 'JSONB field for additional metadata (IP, user agent, etc.)';

-- ===========================================
-- SAMPLE ACTIVITY TYPES
-- ===========================================

-- Member Activities
-- member_added, member_updated, member_deleted, member_viewed

-- Attendance Activities
-- attendance_marked, attendance_updated, attendance_bulk_updated, attendance_viewed, attendance_exported

-- Tutorial Activities
-- tutorial_uploaded, tutorial_updated, tutorial_deleted, tutorial_viewed, tutorial_downloaded

-- Meeting Activities
-- meeting_created, meeting_updated, meeting_deleted, meeting_viewed

-- Profile Activities
-- profile_updated, profile_viewed, password_changed

-- System Activities
-- user_login, user_logout, user_registered, session_started, session_ended

-- Portal Activities (if different from app)
-- report_generated, export_created, bulk_operation_performed

