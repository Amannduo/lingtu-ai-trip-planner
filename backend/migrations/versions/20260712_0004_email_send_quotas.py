"""Add durable per-user and per-IP email send quotas."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260712_0004"
down_revision = "20260712_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "email_send_quotas" in sa.inspect(bind).get_table_names():
        return

    op.create_table(
        "email_send_quotas",
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.String(length=32), nullable=False),
        sa.Column(
            "send_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.String(length=32),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.String(length=32),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope_type IN ('user', 'ip')",
            name="ck_email_send_quotas_valid_scope",
        ),
        sa.CheckConstraint(
            "send_count >= 0",
            name="ck_email_send_quotas_nonnegative_count",
        ),
        sa.PrimaryKeyConstraint(
            "scope_type",
            "scope_hash",
            "period_start",
            name="pk_email_send_quotas",
        ),
    )
    op.create_index(
        "idx_email_send_quotas_expires_at",
        "email_send_quotas",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "email_send_quotas" in sa.inspect(bind).get_table_names():
        op.drop_table("email_send_quotas")
