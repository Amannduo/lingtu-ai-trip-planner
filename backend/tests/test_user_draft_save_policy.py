"""Draft-save policy for user-edited trips (B4).

Saving a user's own edit is deliberately laxer than publishing it. These tests
pin the three things that must stay distinct:

1. a ``needs_review`` plan is still savable as the user's draft;
2. savable never implies publishable — publishability is decided only by
   ``resolve_plan_quality_status``;
3. the draft path does not relax identity, ownership or unforgeable-field
   checks.
"""

from __future__ import annotations

import pytest

from app.api.routes.trip import can_save_user_draft
from app.models.schemas import (
    TripPlanQualityIssue,
    TripPlanQualityResult,
    TripPlan,
)
from app.services.trip_plan_quality_service import resolve_plan_quality_status


def quality(
    *,
    status: str = "warning",
    score: int = 60,
    issues: list[TripPlanQualityIssue] | None = None,
    publishable: bool = False,
    review_required: bool = True,
) -> TripPlanQualityResult:
    return TripPlanQualityResult(
        status=status,
        score=score,
        issues=issues or [],
        publishable=publishable,
        review_required=review_required,
    )


def issue(severity: str, code: str = "SOME_CODE") -> TripPlanQualityIssue:
    return TripPlanQualityIssue(
        code=code, severity=severity, path="days", message="测试问题"
    )


def plan_with(result: TripPlanQualityResult) -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-02",
        overall_suggestions="测试",
        days=[],
        quality=result,
    )


# ── 1. needs_review stays savable as a draft ──────────────────────────


def test_needs_review_plan_is_savable_as_a_draft() -> None:
    result = quality(
        status="warning",
        score=60,
        publishable=True,
        review_required=True,
    )
    assert can_save_user_draft(result) is True


def test_warning_severity_issues_do_not_block_a_draft() -> None:
    """Blocking codes are warning-severity; they stop publishing, not saving."""
    result = quality(
        issues=[issue("warning", "HOTEL_GAP"), issue("info", "SEMANTIC_PENDING_FIELDS")]
    )
    assert can_save_user_draft(result) is True


# ── 2. savable != publishable ─────────────────────────────────────────


def test_saving_a_draft_does_not_imply_publishable() -> None:
    result = quality(
        status="warning",
        score=60,
        issues=[issue("warning", "HOTEL_GAP")],
        publishable=False,
        review_required=True,
    )
    assert can_save_user_draft(result) is True
    # The publish decision has exactly one owner, and it says no.
    assert resolve_plan_quality_status(plan_with(result)) != "publishable"


def test_publishable_plan_is_also_savable() -> None:
    result = quality(
        status="passed",
        score=95,
        publishable=True,
        review_required=False,
    )
    assert can_save_user_draft(result) is True
    assert resolve_plan_quality_status(plan_with(result)) == "publishable"


# ── 3. error / failed results are refused ─────────────────────────────


@pytest.mark.parametrize(
    "result",
    [
        quality(
            status="failed",
            score=0,
            publishable=False,
            review_required=True,
        ),
        # Coherent authoritative pair, as refresh_quality_gate would produce it.
        quality(
            status="failed",
            issues=[issue("error", "POI_DESTINATION_MISMATCH")],
            publishable=False,
            review_required=True,
        ),
    ],
    ids=["failed-evaluation", "error-severity-issue"],
)
def test_error_results_are_not_savable(result: TripPlanQualityResult) -> None:
    assert can_save_user_draft(result) is False
    assert resolve_plan_quality_status(plan_with(result)) == "blocked"


def test_missing_quality_is_not_savable() -> None:
    """No evaluation means no evidence — refuse rather than assume."""
    assert can_save_user_draft(None) is False


# ── the draft path does not weaken the trust boundary ─────────────────


def test_draft_policy_is_independent_of_identity_and_trust_checks() -> None:
    """``can_save_user_draft`` decides quality only.

    Identity immutability, POI forgery and ownership are enforced by separate
    guards that run before it, so a savable draft cannot be a way in.
    """
    import inspect

    from app.api.routes import trip as trip_routes

    source = inspect.getsource(trip_routes.update_trip_history)
    save_index = source.index("can_save_user_draft")
    for guard in (
        "_reject_identity_mutation",
        "_restore_verified_plan_facts",
        "if-match",
    ):
        assert guard in source, f"{guard} missing from the edit-save path"
        assert source.index(guard) < save_index, (
            f"{guard} must run before the draft-save decision"
        )
