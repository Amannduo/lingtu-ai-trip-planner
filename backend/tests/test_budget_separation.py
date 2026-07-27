"""S2 regression tests: user budget constraint vs system estimate.

Before the fix, ``save_trip_plan`` overwrote the ``budget`` column with the
computed estimate total and ``get_trip_request`` hardcoded ``budget=None``,
so the edit-path quality gate re-evaluated plans against a request with no
budget at all — a strictly weaker check than at generation time.

Pinned here:

1. the user's constraint survives a save → rebuild roundtrip;
2. the estimate total still lands in the legacy ``budget`` column
   (analytics SQL reads it — no behavior change there);
3. legacy rows without ``user_budget`` keep the current behavior
   (``budget=None`` → budget checks skipped, never a fabricated value);
4. the compatibility migration adds the column idempotently.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.schemas import Budget, DayPlan, TripPlan, TripRequest
from app.services.database_service import execute, fetch_one
from app.services.schema import init_db
from app.services.travel_plan_data_service import (
    TravelPlanDataService,
    _as_optional_budget,
)


def _request(budget: int | None) -> TripRequest:
    return TripRequest(
        origin_city="上海",
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-02",
        travel_days=1,
        travelers=2,
        budget=budget,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
        free_text_input="",
    )


def _plan(estimate_total: int | None) -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-02",
        overall_suggestions="正常",
        days=[
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="d1",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            )
        ],
        budget=Budget(total=estimate_total) if estimate_total else None,
    )


@pytest.fixture
def service():
    init_db()
    return TravelPlanDataService()


@pytest.fixture
def test_user():
    user_id = f"budget-test-{uuid.uuid4().hex[:10]}"
    yield user_id
    execute(
        "DELETE FROM travel_plans WHERE user_id = :user_id",
        {"user_id": user_id},
    )
    execute(
        "DELETE FROM user_profiles WHERE user_id = :user_id",
        {"user_id": user_id},
    )


def test_user_constraint_survives_save_and_rebuild(service, test_user) -> None:
    plan_no = service.save_trip_plan(
        _request(budget=3000), _plan(estimate_total=5200), user_id=test_user
    )

    rebuilt = service.get_trip_request(plan_no, test_user)
    assert rebuilt is not None
    assert rebuilt.budget == 3000

    row = fetch_one(
        "SELECT budget, user_budget FROM travel_plans WHERE plan_no = :p",
        {"p": plan_no},
    )
    # Analytics column keeps the estimate; the constraint lives beside it.
    assert float(row["budget"]) == 5200.0
    assert float(row["user_budget"]) == 3000.0


def test_request_without_budget_stays_none(service, test_user) -> None:
    plan_no = service.save_trip_plan(
        _request(budget=None), _plan(estimate_total=4100), user_id=test_user
    )
    rebuilt = service.get_trip_request(plan_no, test_user)
    assert rebuilt is not None
    assert rebuilt.budget is None


def test_legacy_row_without_user_budget_keeps_current_behavior(
    service, test_user
) -> None:
    plan_no = service.save_trip_plan(
        _request(budget=3000), _plan(estimate_total=5200), user_id=test_user
    )
    # Simulate a pre-migration row: constraint column empty.
    execute(
        "UPDATE travel_plans SET user_budget = NULL WHERE plan_no = :p",
        {"p": plan_no},
    )

    rebuilt = service.get_trip_request(plan_no, test_user)
    assert rebuilt is not None
    assert rebuilt.budget is None  # budget checks skip, nothing fabricated


def test_compat_migration_added_column_idempotently() -> None:
    """init_db's compatibility ALTER must have added user_budget exactly
    once; a second init is a no-op."""
    from sqlalchemy import inspect

    from app.services.database_service import engine

    init_db()
    columns = [
        c["name"] for c in inspect(engine).get_columns("travel_plans")
    ]
    assert columns.count("user_budget") == 1


def test_budget_coercion_guards() -> None:
    assert _as_optional_budget(None) is None
    assert _as_optional_budget("3000.00") == 3000
    assert _as_optional_budget(2500.4) == 2500
    assert _as_optional_budget("not-a-number") is None
    assert _as_optional_budget(-10) is None
