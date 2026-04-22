-- Add is_leader flag + one self-row per leader, so leaders appear in attendance.
-- Safe/idempotent: can be re-run. Rolls back cleanly by reversing the steps at the
-- bottom of this file.

-- 1. Column (nullable default false). Existing rows will be FALSE.
ALTER TABLE cell_members
    ADD COLUMN IF NOT EXISTS is_leader BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN cell_members.is_leader IS
    'TRUE when this cell_members row represents the cell leader themselves (used so the leader can have their own attendance marked). Exactly one such row per leader_id.';

-- 2. One leader self-row per existing leader, only if none exists yet.
--    role_id = 4 matches the leader convention used in routes/auth.py.
INSERT INTO cell_members (leader_id, name, phone_number, country, branch_id, created_at, is_leader)
SELECT u.id,
       COALESCE(u.name, 'Cell Leader'),
       u.phone_number,
       u.country,
       u.branch_id,
       COALESCE(u.created_at, NOW()),
       TRUE
FROM users u
WHERE u.role_id = 4
  AND NOT EXISTS (
        SELECT 1
        FROM cell_members cm
        WHERE cm.leader_id = u.id
          AND cm.is_leader = TRUE
  );

-- 3. Enforce at most one self-row per leader going forward.
CREATE UNIQUE INDEX IF NOT EXISTS cell_members_leader_self_uniq
    ON cell_members(leader_id)
    WHERE is_leader;

-- ---------------------------------------------------------------------------
-- ROLLBACK (uncomment to revert):
-- DELETE FROM attendance
--  WHERE member_id IN (SELECT id FROM cell_members WHERE is_leader = TRUE);
-- DELETE FROM cell_members WHERE is_leader = TRUE;
-- DROP INDEX IF EXISTS cell_members_leader_self_uniq;
-- ALTER TABLE cell_members DROP COLUMN IF EXISTS is_leader;
