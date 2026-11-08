"""accounts.is_root - desktop schema v13.

The root user is a flag, so root can add other administrators (IAM admins) who cannot manage accounts.
The oldest active administrator becomes root.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS is_root BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("UPDATE accounts SET is_root = TRUE WHERE id = (SELECT id FROM accounts WHERE role = 'admin' "
               "ORDER BY is_active DESC, id LIMIT 1) AND NOT EXISTS (SELECT 1 FROM accounts WHERE is_root)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_one_root ON accounts (is_root) WHERE is_root")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_accounts_one_root")
    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS is_root")
