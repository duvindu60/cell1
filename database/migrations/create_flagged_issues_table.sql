-- ===========================================
-- Flagged Issues Table
-- Records issues flagged by leaders for cell members
-- ===========================================

-- Create flagged_issues table
CREATE TABLE IF NOT EXISTS flagged_issues (
    id SERIAL PRIMARY KEY,
    member_id TEXT NOT NULL,
    leader_id TEXT NOT NULL,
    issue_type TEXT,
    description TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    response TEXT,
    response_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ===========================================
-- INDEXES for Fast Queries
-- ===========================================

-- Index for member-based queries
CREATE INDEX IF NOT EXISTS idx_flagged_issues_member_id ON flagged_issues(member_id);

-- Index for leader-based queries
CREATE INDEX IF NOT EXISTS idx_flagged_issues_leader_id ON flagged_issues(leader_id);

-- Index for status-based queries
CREATE INDEX IF NOT EXISTS idx_flagged_issues_status ON flagged_issues(status);

-- Index for date-based queries
CREATE INDEX IF NOT EXISTS idx_flagged_issues_created_at ON flagged_issues(created_at DESC);

-- Composite index for common queries: member + status
CREATE INDEX IF NOT EXISTS idx_flagged_issues_member_status ON flagged_issues(member_id, status);

-- Composite index for leader + status queries
CREATE INDEX IF NOT EXISTS idx_flagged_issues_leader_status ON flagged_issues(leader_id, status);

-- ===========================================
-- COMMENTS for Documentation
-- ===========================================

COMMENT ON TABLE flagged_issues IS 'Records issues flagged by leaders for cell members';
COMMENT ON COLUMN flagged_issues.member_id IS 'ID of the cell member being flagged (UUID or string)';
COMMENT ON COLUMN flagged_issues.leader_id IS 'ID of the leader who flagged the issue (UUID or string)';
COMMENT ON COLUMN flagged_issues.issue_type IS 'Type of issue (e.g., attendance, behavior, spiritual, other)';
COMMENT ON COLUMN flagged_issues.description IS 'Detailed description of the issue';
COMMENT ON COLUMN flagged_issues.status IS 'Status of the issue: pending, reviewed, resolved, dismissed';
COMMENT ON COLUMN flagged_issues.response IS 'Response or resolution to the flagged issue';
COMMENT ON COLUMN flagged_issues.response_by IS 'ID of the user who responded to the issue (UUID or string)';
COMMENT ON COLUMN flagged_issues.created_at IS 'Timestamp when the issue was flagged';
COMMENT ON COLUMN flagged_issues.updated_at IS 'Timestamp when the issue was last updated';

