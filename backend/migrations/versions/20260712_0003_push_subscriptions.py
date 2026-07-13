"""Add authenticated multi-device Web Push subscriptions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260712_0003"
down_revision = "20260712_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "push_subscriptions" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "push_subscriptions",
        sa.Column("subscription_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("endpoint_hash", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("expiration_time", sa.BigInteger(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "failure_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_success_at", sa.String(length=32), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("subscription_id"),
        sa.UniqueConstraint(
            "endpoint_hash",
            name="uq_push_subscriptions_endpoint_hash",
        ),
    )
    op.create_index(
        "idx_push_subscriptions_user",
        "push_subscriptions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "idx_push_subscriptions_expiration",
        "push_subscriptions",
        ["expiration_time"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "push_subscriptions" in sa.inspect(bind).get_table_names():
        op.drop_table("push_subscriptions")
