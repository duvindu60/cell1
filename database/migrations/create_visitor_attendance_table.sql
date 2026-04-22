-- Reference schema for public.attendance_visitor_counts (aligns with production Supabase).
-- Table name in app: ATTENDANCE_VISITOR_COUNTS_TABLE = 'attendance_visitor_counts'
CREATE TABLE IF NOT EXISTS attendance_visitor_counts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    leader_user_id UUID NOT NULL,
    meeting_id UUID NOT NULL,
    meeting_date DATE NOT NULL,
    meeting_number BIGINT,
    visitor_count INTEGER NOT NULL CHECK (visitor_count >= 0 AND visitor_count <= 20),
    reported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    marked_by_user_id UUID,
    marked_by_name TEXT,
    UNIQUE (leader_user_id, meeting_date)
);

CREATE INDEX IF NOT EXISTS idx_attendance_visitor_counts_meeting_date ON attendance_visitor_counts (meeting_date);
CREATE INDEX IF NOT EXISTS idx_attendance_visitor_counts_leader ON attendance_visitor_counts (leader_user_id);
