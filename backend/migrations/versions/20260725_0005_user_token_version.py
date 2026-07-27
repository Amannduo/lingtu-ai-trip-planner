"""Add users.token_version for access-token revocation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260725_0005"
down_revision = "20260712_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "token_version" in columns:
        return

    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "token_version" not in columns:
        return
    op.drop_column("users", "token_version")
