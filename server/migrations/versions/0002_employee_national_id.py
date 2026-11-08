"""employees.national_id (T.C. Kimlik No) - desktop schema v12.

Nullable: records made before it have none. One person, one record: a
national ID belongs to at most one employee. The API never sends the full
number back, only a masked form.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE employees ADD COLUMN IF NOT EXISTS national_id TEXT")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_employees_national_id ON employees (national_id) "
               "WHERE national_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_employees_national_id")
    op.execute("ALTER TABLE employees DROP COLUMN IF EXISTS national_id")
