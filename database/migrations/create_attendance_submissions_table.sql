-- Final submission per leader per meeting week (Tuesday date).
-- Written when the leader completes bulk "Submit Attendance"; locks further edits until deadline logic allows nothing anyway.
CREATE TABLE IF NOT EXISTS attendance_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    leader_id UUID NOT NULL,
    meeting_date DATE NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_by_user_id UUID NULL,
    UNIQUE (leader_id, meeting_date)
);

CREATE INDEX IF NOT EXISTS idx_attendance_submissions_leader ON attendance_submissions (leader_id);
CREATE INDEX IF NOT EXISTS idx_attendance_submissions_meeting_date ON attendance_submissions (meeting_date);
