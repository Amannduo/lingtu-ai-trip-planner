"""Create the frozen base authentication and travel schema."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260712_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("user_id", sa.String(length=32), nullable=False),
            sa.Column("username", sa.String(length=32), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column(
                "role",
                sa.String(length=16),
                server_default=sa.text("'user'"),
                nullable=False,
            ),
            sa.Column(
                "is_active",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            ),
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
            sa.Column("last_login_at", sa.String(length=32), nullable=True),
            sa.CheckConstraint(
                "role IN ('user', 'manager', 'admin')",
                name="ck_users_valid_role",
            ),
            sa.PrimaryKeyConstraint("user_id", name="pk_users"),
        )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_users_username_lower ON users (lower(username))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_role ON users (role)"
    )

    if "travel_plans" not in tables:
        op.create_table(
            "travel_plans",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("plan_no", sa.String(length=64), nullable=False),
            sa.Column(
                "user_id",
                sa.String(length=32),
                server_default=sa.text("''"),
                nullable=False,
            ),
            sa.Column(
                "user_role",
                sa.String(length=16),
                server_default=sa.text("'user'"),
                nullable=False,
            ),
            sa.Column("origin_city", sa.String(length=100), nullable=True),
            sa.Column("destination", sa.String(length=100), nullable=False),
            sa.Column("start_date", sa.String(length=10), nullable=False),
            sa.Column("end_date", sa.String(length=10), nullable=False),
            sa.Column("travel_days", sa.Integer(), nullable=False),
            sa.Column(
                "travelers",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            ),
            sa.Column("budget", sa.Numeric(precision=14, scale=2), nullable=True),
            sa.Column(
                "actual_cost",
                sa.Numeric(precision=14, scale=2),
                nullable=True,
            ),
            sa.Column("transportation", sa.String(length=100), nullable=True),
            sa.Column("accommodation", sa.String(length=100), nullable=True),
            sa.Column(
                "preferences",
                sa.Text(),
                server_default=sa.text("'[]'"),
                nullable=False,
            ),
            sa.Column("free_text", sa.Text(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column(
                "plan_json",
                sa.Text(),
                server_default=sa.text("'{}'"),
                nullable=False,
            ),
            sa.Column(
                "status",
                sa.String(length=32),
                server_default=sa.text("'completed'"),
                nullable=False,
            ),
            sa.Column(
                "source",
                sa.String(length=64),
                server_default=sa.text("'generated'"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.String(length=32),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id", name="pk_travel_plans"),
            sa.UniqueConstraint(
                "plan_no",
                name="uq_travel_plans_plan_no",
            ),
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_plans_user ON travel_plans (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_plans_dest ON travel_plans (destination)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_plans_date ON travel_plans (start_date)"
    )

    if "user_profiles" not in tables:
        op.create_table(
            "user_profiles",
            sa.Column("user_id", sa.String(length=32), nullable=False),
            sa.Column(
                "plan_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column(
                "top_tags",
                sa.Text(),
                server_default=sa.text("'[]'"),
                nullable=False,
            ),
            sa.Column(
                "fav_cities",
                sa.Text(),
                server_default=sa.text("'[]'"),
                nullable=False,
            ),
            sa.Column("avg_budget", sa.Numeric(precision=14, scale=2)),
            sa.Column("avg_days", sa.Numeric(precision=8, scale=2)),
            sa.Column("traveler_type", sa.String(length=100)),
            sa.Column(
                "updated_at",
                sa.String(length=32),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("user_id", name="pk_user_profiles"),
        )

    if "audit_logs" not in tables:
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.String(length=32)),
            sa.Column("user_role", sa.String(length=16)),
            sa.Column("message", sa.Text()),
            sa.Column("agent", sa.String(length=100)),
            sa.Column("tool", sa.String(length=100)),
            sa.Column(
                "allowed",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            ),
            sa.Column(
                "sensitive_hit",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column(
                "detail",
                sa.Text(),
                server_default=sa.text("'{}'"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.String(length=32),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        )

    if "query_logs" not in tables:
        op.create_table(
            "query_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.String(length=32)),
            sa.Column("user_role", sa.String(length=16)),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("intent", sa.String(length=100)),
            sa.Column("sql_text", sa.Text()),
            sa.Column("result_summary", sa.Text()),
            sa.Column(
                "created_at",
                sa.String(length=32),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id", name="pk_query_logs"),
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table_name in (
        "query_logs",
        "audit_logs",
        "user_profiles",
        "travel_plans",
        "users",
    ):
        if table_name in tables:
            op.drop_table(table_name)
