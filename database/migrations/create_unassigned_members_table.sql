-- unassigned_members: same member details as cell_members + ex_leader_id (references users; leaders identified by role_id).
CREATE TABLE IF NOT EXISTS unassigned_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    age INTEGER,
    gender TEXT,
    phone_number TEXT,
    zone_id INTEGER,
    country TEXT,
    branch_id TEXT,
    cell_category TEXT,
    church BOOLEAN,
    potential_leader BOOLEAN,
    sector_number INTEGER,
    district TEXT,
    province TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    ex_leader_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    unassigned_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_unassigned_members_ex_leader_id ON unassigned_members(ex_leader_id);
CREATE INDEX IF NOT EXISTS idx_unassigned_members_unassigned_at ON unassigned_members(unassigned_at DESC);

COMMENT ON TABLE unassigned_members IS 'Members removed from a cell; ex_leader_id is the user id of the leader they were under.';
COMMENT ON COLUMN unassigned_members.ex_leader_id IS 'Previous leader (users.id of the cell leader) this member was assigned to.';
