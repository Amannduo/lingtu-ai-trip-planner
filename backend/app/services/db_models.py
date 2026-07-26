"""Portable SQLAlchemy schema shared by SQLite and PostgreSQL."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    CheckConstraint,
    Column,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    func,
    text,
)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

users = Table(
    "users",
    metadata,
    Column("user_id", String(32), primary_key=True),
    Column("username", String(32), nullable=False),
    Column("email", String(254)),
    Column("password_hash", Text, nullable=False),
    Column("role", String(16), nullable=False, server_default=text("'user'")),
    Column("is_active", Integer, nullable=False, server_default=text("1")),
    Column("token_version", Integer, nullable=False, server_default=text("0")),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("last_login_at", String(32)),
    CheckConstraint("role IN ('user', 'manager', 'admin')", name="valid_role"),
)
Index("uq_users_username_lower", func.lower(users.c.username), unique=True)
Index("uq_users_email_lower", func.lower(users.c.email), unique=True)
Index("idx_users_role", users.c.role)

push_subscriptions = Table(
    "push_subscriptions",
    metadata,
    Column("subscription_id", String(32), primary_key=True),
    Column(
        "user_id",
        String(32),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("endpoint_hash", String(64), nullable=False, unique=True),
    Column("endpoint", Text, nullable=False),
    Column("p256dh", Text, nullable=False),
    Column("auth", Text, nullable=False),
    Column("expiration_time", BigInteger),
    Column("user_agent", String(512)),
    Column("failure_count", Integer, nullable=False, server_default=text("0")),
    Column("last_success_at", String(32)),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
Index("idx_push_subscriptions_user", push_subscriptions.c.user_id)
Index("idx_push_subscriptions_expiration", push_subscriptions.c.expiration_time)

email_send_quotas = Table(
    "email_send_quotas",
    metadata,
    Column("scope_type", String(16), primary_key=True),
    Column("scope_hash", String(64), primary_key=True),
    Column("period_start", String(32), primary_key=True),
    Column("send_count", Integer, nullable=False, server_default=text("0")),
    Column("expires_at", String(32), nullable=False),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint(
        "scope_type IN ('user', 'ip')",
        name="valid_scope",
    ),
    CheckConstraint(
        "send_count >= 0",
        name="nonnegative_count",
    ),
)
Index("idx_email_send_quotas_expires_at", email_send_quotas.c.expires_at)

travel_plans = Table(
    "travel_plans",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("plan_no", String(64), nullable=False, unique=True),
    Column("user_id", String(32), nullable=False, server_default=text("''")),
    Column("user_role", String(16), nullable=False, server_default=text("'user'")),
    Column("origin_city", String(100)),
    Column("destination", String(100), nullable=False),
    Column("start_date", String(10), nullable=False),
    Column("end_date", String(10), nullable=False),
    Column("travel_days", Integer, nullable=False),
    Column("travelers", Integer, nullable=False, server_default=text("1")),
    Column("budget", Numeric(14, 2)),
    # The user's stated budget constraint; ``budget`` stores the system
    # estimate total (analytics read it) and must never overwrite this.
    Column("user_budget", Numeric(14, 2)),
    Column("actual_cost", Numeric(14, 2)),
    Column("transportation", String(100)),
    Column("accommodation", String(100)),
    Column("preferences", Text, nullable=False, server_default=text("'[]'")),
    Column("free_text", Text),
    Column("summary", Text),
    Column("plan_json", Text, nullable=False, server_default=text("'{}'")),
    # Versioned generation-time snapshots; NULL on legacy rows → the edit
    # path falls back to weak column reconstruction (validation_mode
    # legacy_weak, never auto-upgraded to publishable).
    Column("request_json", Text),
    Column("contract_json", Text),
    Column("status", String(32), nullable=False, server_default=text("'completed'")),
    Column("source", String(64), nullable=False, server_default=text("'generated'")),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
Index("idx_plans_user", travel_plans.c.user_id)
Index("idx_plans_dest", travel_plans.c.destination)
Index("idx_plans_date", travel_plans.c.start_date)

user_profiles = Table(
    "user_profiles",
    metadata,
    Column("user_id", String(32), primary_key=True),
    Column("plan_count", Integer, nullable=False, server_default=text("0")),
    Column("top_tags", Text, nullable=False, server_default=text("'[]'")),
    Column("fav_cities", Text, nullable=False, server_default=text("'[]'")),
    Column("avg_budget", Numeric(14, 2)),
    Column("avg_days", Numeric(8, 2)),
    Column("traveler_type", String(100)),
    Column("updated_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

audit_logs = Table(
    "audit_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(32)),
    Column("user_role", String(16)),
    Column("message", Text),
    Column("agent", String(100)),
    Column("tool", String(100)),
    Column("allowed", Integer, nullable=False, server_default=text("1")),
    Column("sensitive_hit", Integer, nullable=False, server_default=text("0")),
    Column("detail", Text, nullable=False, server_default=text("'{}'")),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

query_logs = Table(
    "query_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(32)),
    Column("user_role", String(16)),
    Column("question", Text, nullable=False),
    Column("intent", String(100)),
    Column("sql_text", Text),
    Column("result_summary", Text),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)