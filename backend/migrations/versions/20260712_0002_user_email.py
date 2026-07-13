"""Add a unique optional email to user accounts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260712_0002"
down_revision = "20260712_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "email" not in columns:
        op.add_column("users", sa.Column("email", sa.String(length=254), nullable=True))
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_users_email_lower ON users (lower(email))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_email_lower")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("email")
