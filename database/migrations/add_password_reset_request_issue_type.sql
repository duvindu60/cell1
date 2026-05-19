-- Password reset uses existing flagged_issues table (no new columns required).
-- Cell app creates rows with issue_type = 'password_reset_request', status = 'pending'.
-- member_id = leader's cell_members row (is_leader=TRUE); leader_id = users.id.
--
-- Portal admin approve flow:
--   1. Set users.password = bcrypt(temp password)
--   2. Set flagged_issues.status = 'approved_pending_password'  (NOT resolved yet)
--
-- Cell app after user signs in with temp password:
--   Redirect to /set-password → user sets own password → status = 'resolved'

COMMENT ON COLUMN flagged_issues.issue_type IS
    'Type of issue (e.g., attendance, behavior, spiritual, other, delete_request, deputy_removal_request, password_reset_request). '
    'password_reset_request: member_id is cell_members.id (leader self-row); leader_id is users.id. '
    'Portal approve: status=approved_pending_password + temp users.password. '
    'Cell app: user sets password at /set-password → status=resolved.';

COMMENT ON COLUMN flagged_issues.status IS
    'Status: pending, reviewed, resolved, dismissed, approved_pending_password. '
    'approved_pending_password = admin set temp password; awaiting user to set own password in Cell App.';
