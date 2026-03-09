-- Add marked_by_member_id for deputy leaders (they are in cell_members only, no users row).
-- When a deputy marks attendance, we store their cell_members.id here.
ALTER TABLE attendance
ADD COLUMN IF NOT EXISTS marked_by_member_id uuid NULL REFERENCES cell_members(id);

COMMENT ON COLUMN attendance.marked_by_member_id IS 'Set when a deputy leader marks attendance; null when leader (users.id) marks via marked_by';
