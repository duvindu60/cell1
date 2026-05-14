-- Cell category on leader profile; must match tutorials.cell_category (youth | young adult | adult).

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS cell_category TEXT;

COMMENT ON COLUMN users.cell_category IS
    'youth | young adult | adult — filters weekly tutorials for this leader.';
