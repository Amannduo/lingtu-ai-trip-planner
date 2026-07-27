"""Regression tests for the unified quality gate decision (P0-2).

Before the fix there were three independent derivations of the gate
decision (quality service, graph quality node, HTTP route helper) that
could disagree:

- the graph's quality node could leave ``publishable`` and
  ``review_required`` describing different decisions after enrichment
  failures;
- the agent public gate and the HTTP helper used different fallback rules
  for stub/legacy quality objects, so the same plan could pass one gate
  and be rejected by the other.

These tests pin the invariant: ``publishable`` + ``review_required`` are
authoritative, and every compatibility label is derived from that pair.
"""

from __future__ import annotations

import pytest

import app.agents.graph.trip_planning_graph as graph_module
from app.agents.graph.trip_planning_graph import TripPlanningAgentGraph
from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.api.routes.trip import _resolve_quality_status
from app.models.schemas import (
    Attraction,
    DayPlan,
    Location,
    Meal,
    TripPlan,
    TripPlanQualityIssue,
    TripPlanQualityResult,
    TripRequest,
)
from app.services.trip_plan_quality_service import (
    refresh_quality_gate,
    resolve_plan_quality_status,
)


def _request() -> TripRequest:
    return TripRequest(
        city="北京",
        start_date="2030-01-01",
        end_date="2030-01-01",
        travel_days=1,
        travelers=1,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
    )


def _plan() -> TripPlan:
    return TripPlan(
        city="北京",
        start_date="2030-01-01",
        end_date="2030-01-01",
        generation_mode="primary",
        overall_suggestions="按计划出行。",
        days=[
            DayPlan(
                date="2030-01-01",
                day_index=0,
                description="城市漫游",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[
                    Attraction(
                        name="故宫博物院",
                        address="北京市东城区",
                        location=Location(longitude=116.397, latitude=39.918),
                        visit_duration=180,
                        description="历史文化参观",
                        category="博物馆",
                        poi_id="amap-1",
                        coordinate_source="amap_poi",
                    )
                ],
                meals=[
                    Meal(type="breakfast", name="早餐"),
                    Meal(type="lunch", name="午餐"),
                    Meal(type="dinner", name="晚餐"),
                ],
            )
        ],
    )


class _AmapStub:
    @staticmethod
    def get_weather(*_args, **_kwargs):
        return []


class _PlannerStub:
    def __init__(self):
        self.amap_service = _AmapStub()
        self.route_calls = 0

    @staticmethod
    def _search_attractions(_request):
        return []

    @staticmethod
    def _format_pois_for_prompt(*_args, **_kwargs):
        return "候选地点"

    @staticmethod
    def _format_weather_for_prompt(*_args, **_kwargs):
        return "天气未覆盖"

    @staticmethod
    def _search_hotels(*_args, **_kwargs):
        return []

    @staticmethod
    def _run_primary_planner(*_args, **_kwargs):
        return "good"

    @staticmethod
    def _parse_response(_response, _request):
        return _plan()

    @staticmethod
    def _normalize_plan_dates_and_weather(_request, plan, *_args, **_kwargs):
        return plan

    @staticmethod
    def _ground_trip_plan(_request, plan, *_args, **_kwargs):
        return plan

    @staticmethod
    def _finalize_generated_content(_request, plan):
        return plan

    def _apply_route_planning(self, _request, plan):
        self.route_calls += 1
        raise TimeoutError("route service unavailable")

    @staticmethod
    def _apply_budget_estimate(_request, plan):
        return plan

    @staticmethod
    def _apply_web_guide(_request, plan):
        return plan

    @staticmethod
    def _create_fallback_plan(_request, _pois):  # pragma: no cover
        raise AssertionError("fallback must not run here")


class _PublishableQualityStub:
    """Quality service double that reports a fully publishable plan."""

    def evaluate(self, _request, _plan) -> TripPlanQualityResult:
        return TripPlanQualityResult(
            status="passed",
            score=95,
            publishable=True,
            review_required=False,
            checked_items=["基础检查"],
            issues=[],
        )


def test_enrichment_failure_marks_deliverable_plan_for_review(
    monkeypatch,
) -> None:
    """Partial enrichment remains deliverable but requires review."""
    monkeypatch.setattr(
        graph_module,
        "get_trip_plan_quality_service",
        lambda: _PublishableQualityStub(),
    )
    graph = TripPlanningAgentGraph(_PlannerStub())

    result = graph.run(_request())

    assert result.quality is not None
    assert result.quality.publishable is True
    assert result.quality.review_required is True
    assert resolve_plan_quality_status(result) == "needs_review"


def test_gate_pair_stays_coherent_after_graph_run(monkeypatch) -> None:
    """A partial-enrichment result is deliverable and explicitly reviewable."""
    monkeypatch.setattr(
        graph_module,
        "get_trip_plan_quality_service",
        lambda: _PublishableQualityStub(),
    )
    graph = TripPlanningAgentGraph(_PlannerStub())
    result = graph.run(_request())

    assert result.quality.publishable is True
    assert result.quality.review_required is True
    assert resolve_plan_quality_status(result) == "needs_review"


def test_agent_gate_and_route_gate_agree_on_authoritative_pair() -> None:
    """Both public gates read the authoritative boolean pair."""
    plan = _plan()
    plan.quality = TripPlanQualityResult(
        publishable=True,
        review_required=False,
        score=90,
    )

    assert _resolve_quality_status(plan) == "publishable"

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    gated = planner._enforce_public_quality_gate(_request(), plan)
    assert gated is plan


@pytest.mark.parametrize(
    "score,severity,mode,force,expected_publishable,expected_review_required",
    [
        (100, None, "primary", False, True, False),
        (95, None, "primary", False, True, True),
        (60, None, "primary", False, True, True),
        (95, "error", "primary", False, False, True),
        (95, None, "map_fallback", False, True, True),
        (95, None, "primary", True, True, True),
    ],
)
def test_refresh_quality_gate_matrix(
    score,
    severity,
    mode,
    force,
    expected_publishable,
    expected_review_required,
) -> None:
    issues = []
    if severity:
        issues.append(
            TripPlanQualityIssue(
                code="WEATHER_GAP",
                severity=severity,
                path="weather",
                message="测试问题",
            )
        )
    quality = TripPlanQualityResult(score=score, issues=issues)

    refresh_quality_gate(
        quality, generation_mode=mode, force_review=force
    )

    assert quality.publishable is expected_publishable
    assert quality.review_required is expected_review_required


def test_structural_blocking_code_blocks_even_with_warning_severity() -> None:
    """Structural blocker codes block regardless of severity or score."""
    quality = TripPlanQualityResult(
        score=95,
        issues=[
            TripPlanQualityIssue(
                code="EMPTY_DAY",
                severity="warning",
                path="days[0]",
                message="当日没有任何可执行安排",
            )
        ],
    )
    refresh_quality_gate(quality, generation_mode="primary")
    assert quality.publishable is False
    assert quality.review_required is True
