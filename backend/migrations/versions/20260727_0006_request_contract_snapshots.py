"""Add travel_plans.request_json / contract_json generation-time snapshots.

Versioned JSON snapshots of the TripRequest and the server-built semantic
contract, so the edit path can re-run a quality gate of the same strength
as generation time.  Nullable with no backfill: legacy rows fall back to
weak column reconstruction and are marked ``validation_mode=legacy_weak``
(never auto-upgraded to publishable).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_0006"
down_revision = "20260727_0005"
branch_labels = None
depends_on = None


def _travel_plan_columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if "travel_plans" not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns("travel_plans")}


def upgrade() -> None:
    columns = _travel_plan_columns(op.get_bind())
    if not columns:
        return
    if "request_json" not in columns:
        op.add_column(
            "travel_plans", sa.Column("request_json", sa.Text(), nullable=True)
        )
    if "contract_json" not in columns:
        op.add_column(
            "travel_plans", sa.Column("contract_json", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    columns = _travel_plan_columns(op.get_bind())
    if "contract_json" in columns:
        op.drop_column("travel_plans", "contract_json")
    if "request_json" in columns:
        op.drop_column("travel_plans", "request_json")
