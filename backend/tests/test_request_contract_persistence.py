"""S3 regression tests: generation-time request/contract snapshots.

Before the fix the edit path rebuilt a strictly weaker request from
denormalized columns: the semantic contract, acknowledgment flag, weekend
semantics and destination source were all lost, so the post-edit quality
gate could not match generation-time strength.

Pinned here:

1. save → restore roundtrip returns the full request (ack flag, schedule
   semantics, free text, budget) plus the server-built contract, marked
   ``validation_mode="full"``;
2. legacy rows (NULL snapshots) fall back to the weak reconstruction and
   are marked ``legacy_weak``;
3. corrupt or future-versioned snapshots degrade to ``legacy_weak``
   instead of crashing or half-parsing;
4. the legacy_weak product rule: still savable, visibly marked, never
   auto-upgraded to publishable, with a regenerate hint.
"""

from __future__ import annotations

import uuid

import pytest

from app.api.routes.trip import _mark_legacy_weak_validation
from app.models.schemas import (
    Budget,
    DayPlan,
    TripPlan,
    TripPlanQualityResult,
    TripRequest,
)
from app.services.database_service import execute
from app.services.schema import init_db
from app.services.semantic_contract_service import build_generation_contract
from app.services.travel_plan_data_service import TravelPlanDataService


def _request() -> TripRequest:
    base = TripRequest(
        origin_city="上海",
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        travel_days=2,
        travelers=2,
        budget=3000,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=["自然风光"],
        free_text_input="两个人想去杭州，节奏轻松一点",
        semantic_risks_acknowledged=True,
        date_pattern="weekend",
        weekend_style="sat_sun",
        destination_source="recommendation",
    )
    attached, _ = build_generation_contract(base)
    return attached


def _plan() -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        overall_suggestions="正常",
        days=[
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="d1",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            ),
            DayPlan(
                date="2030-08-03",
                day_index=1,
                description="d2",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            ),
        ],
        budget=Budget(total=2800),
    )


@pytest.fixture
def service():
    init_db()
    return TravelPlanDataService()


@pytest.fixture
def test_user():
    user_id = f"snapshot-test-{uuid.uuid4().hex[:10]}"
    yield user_id
    execute(
        "DELETE FROM travel_plans WHERE user_id = :user_id",
        {"user_id": user_id},
    )
    execute(
        "DELETE FROM user_profiles WHERE user_id = :user_id",
        {"user_id": user_id},
    )


def test_snapshot_roundtrip_restores_full_request_and_contract(
    service, test_user
) -> None:
    saved_request = _request()
    plan_no = service.save_trip_plan(saved_request, _plan(), user_id=test_user)

    restored, mode = service.get_trip_request_with_context(plan_no, test_user)

    assert mode == "full"
    assert restored is not None
    assert restored.semantic_risks_acknowledged is True
    assert restored.date_pattern == "weekend"
    assert restored.weekend_style == "sat_sun"
    assert restored.destination_source == "recommendation"
    assert restored.budget == 3000
    assert restored.free_text_input == saved_request.free_text_input
    assert restored.semantic_contract is not None
    contract = restored.semantic_contract
    assert contract.destination_city.value == "杭州"
    # Free text "想去杭州" agrees with the form → merge keeps the explicit
    # user label; the snapshot must restore provenance faithfully.
    assert contract.destination_city.source == "user_explicit"
    assert contract.travelers.value == 2
    assert contract.pace.is_known()  # "节奏轻松一点" survived the roundtrip


def test_legacy_row_without_snapshot_is_marked_weak(service, test_user) -> None:
    plan_no = service.save_trip_plan(_request(), _plan(), user_id=test_user)
    execute(
        "UPDATE travel_plans SET request_json = NULL, contract_json = NULL "
        "WHERE plan_no = :p",
        {"p": plan_no},
    )

    restored, mode = service.get_trip_request_with_context(plan_no, test_user)

    assert mode == "legacy_weak"
    assert restored is not None
    # Weak reconstruction: contract and ack state are unavailable.
    assert restored.semantic_contract is None
    assert restored.semantic_risks_acknowledged is False
    # S2's budget column still survives independently of S3.
    assert restored.budget == 3000


@pytest.mark.parametrize(
    "bad_snapshot",
    [
        "not-json{",
        '{"schema_version": 999, "request": {}}',
        '{"schema_version": 1}',
    ],
)
def test_corrupt_or_future_snapshot_degrades_to_weak(
    service, test_user, bad_snapshot
) -> None:
    plan_no = service.save_trip_plan(_request(), _plan(), user_id=test_user)
    execute(
        "UPDATE travel_plans SET request_json = :bad WHERE plan_no = :p",
        {"bad": bad_snapshot, "p": plan_no},
    )

    restored, mode = service.get_trip_request_with_context(plan_no, test_user)
    assert mode == "legacy_weak"
    assert restored is not None


def test_missing_row_returns_none_weak(service) -> None:
    restored, mode = service.get_trip_request_with_context(
        "P-DOES-NOT-EXIST", "nobody"
    )
    assert restored is None
    assert mode == "legacy_weak"


def test_legacy_weak_rule_demotes_publishable_and_hints_regenerate() -> None:
    quality = TripPlanQualityResult(
        status="passed",
        score=92,
        publishable=True,
        quality_status="publishable",
    )

    _mark_legacy_weak_validation(quality)

    assert quality.validation_mode == "legacy_weak"
    assert quality.quality_status == "needs_review"
    assert quality.publishable is False
    legacy = next(
        i for i in quality.issues if i.code == "LEGACY_WEAK_VALIDATION"
    )
    assert legacy.severity == "info"
    assert "重新生成" in legacy.suggestion
    # Idempotent: marking twice must not duplicate the issue.
    _mark_legacy_weak_validation(quality)
    assert (
        sum(1 for i in quality.issues if i.code == "LEGACY_WEAK_VALIDATION")
        == 1
    )


def test_legacy_weak_rule_keeps_needs_review_and_blocked_untouched() -> None:
    for status, publishable in (("needs_review", False), ("blocked", False)):
        quality = TripPlanQualityResult(
            status="warning",
            score=60,
            publishable=publishable,
            quality_status=status,
        )
        _mark_legacy_weak_validation(quality)
        assert quality.quality_status == status
        assert quality.validation_mode == "legacy_weak"
