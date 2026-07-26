"""Add travel_plans.user_budget — the user's stated budget constraint.

The pre-existing ``budget`` column stores the system estimate total
(analytics read it); the user's own constraint was previously overwritten
by that estimate and lost on save.  Nullable with no backfill: a
constraint cannot be recovered from an estimate, and NULL keeps legacy
rows on the current (budget-check skipped) behavior.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_0005"
down_revision = "20260712_0004"
branch_labels = None
depends_on = None


def _travel_plan_columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if "travel_plans" not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns("travel_plans")}


def upgrade() -> None:
    columns = _travel_plan_columns(op.get_bind())
    if columns and "user_budget" not in columns:
        op.add_column(
            "travel_plans",
            sa.Column("user_budget", sa.Numeric(14, 2), nullable=True),
        )


def downgrade() -> None:
    columns = _travel_plan_columns(op.get_bind())
    if "user_budget" in columns:
        op.drop_column("travel_plans", "user_budget")
