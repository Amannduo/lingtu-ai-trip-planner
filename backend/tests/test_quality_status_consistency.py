"""Regression tests for unified quality_status derivation (P0-2).

Before the fix there were three independent derivations of the gate
decision (quality service, graph quality node, HTTP route helper) that
could disagree:

- the graph's quality node flipped ``publishable`` after enrichment
  failures but never recomputed ``quality_status`` — a plan could carry
  ``publishable=False`` with ``quality_status="publishable"`` and still be
  persisted/delivered;
- the agent public gate and the HTTP helper used different fallback rules
  for stub/legacy quality objects, so the same plan could pass one gate
  and be rejected by the other.

These tests pin the invariant: the triple (publishable, quality_status)
is always coherent, and every gate reads the same resolver.
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
            score=100,
            publishable=True,
            review_required=False,
            quality_status="publishable",
            checked_items=["基础检查"],
            issues=[],
        )


def test_enrichment_failure_demotes_quality_status_not_only_publishable(
    monkeypatch,
) -> None:
    """A publishable evaluation + failed enrichment must end as
    needs_review (reviewable model: still savable, but the review flag and
    quality_status must both reflect the degradation)."""
    monkeypatch.setattr(
        graph_module,
        "get_trip_plan_quality_service",
        lambda: _PublishableQualityStub(),
    )
    graph = TripPlanningAgentGraph(_PlannerStub())

    result = graph.run(_request())

    assert result.quality is not None
    assert result.quality.review_required is True
    assert result.quality.quality_status == "needs_review"
    assert resolve_plan_quality_status(result) == "needs_review"


def test_gate_triple_stays_coherent_after_graph_run(monkeypatch) -> None:
    """Invariant: quality_status=='publishable' iff publishable without
    the review flag; 'blocked' iff not publishable."""
    monkeypatch.setattr(
        graph_module,
        "get_trip_plan_quality_service",
        lambda: _PublishableQualityStub(),
    )
    graph = TripPlanningAgentGraph(_PlannerStub())
    result = graph.run(_request())

    quality = result.quality
    assert (quality.quality_status == "publishable") == bool(
        quality.publishable and not quality.review_required
    )
    assert (quality.quality_status == "blocked") == (not quality.publishable)


def test_agent_gate_and_route_gate_agree_on_stub_quality_objects() -> None:
    """A stub/legacy quality object (publishable=True with the default
    quality_status='blocked') must pass BOTH the agent public gate and the
    HTTP resolver — previously the agent gate rejected it while the route
    accepted it."""
    plan = _plan()
    plan.quality = TripPlanQualityResult(publishable=True, score=90)
    assert plan.quality.quality_status == "blocked"  # field default

    assert _resolve_quality_status(plan) == "publishable"

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    gated = planner._enforce_public_quality_gate(_request(), plan)
    assert gated is plan


@pytest.mark.parametrize(
    "score,severity,mode,force,expected_status,expected_publishable",
    [
        (100, None, "primary", False, "publishable", True),
        (95, None, "primary", False, "needs_review", True),
        (60, None, "primary", False, "needs_review", True),
        (95, "error", "primary", False, "blocked", False),
        (95, None, "map_fallback", False, "needs_review", True),
        (95, None, "primary", True, "needs_review", True),
    ],
)
def test_refresh_quality_gate_matrix(
    score, severity, mode, force, expected_status, expected_publishable
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
        quality, generation_mode=mode, force_unpublishable=force
    )

    assert quality.quality_status == expected_status
    assert quality.publishable is expected_publishable


def test_soft_budget_warning_is_advisory_not_blocking() -> None:
    """Integrated disposition policy: a warning-severity budget gap keeps
    the plan deliverable (advisory + review), it no longer hard-blocks."""
    quality = TripPlanQualityResult(
        score=95,
        issues=[
            TripPlanQualityIssue(
                code="BUDGET_MISSING",
                severity="warning",
                path="budget",
                message="预算缺失",
            )
        ],
    )
    refresh_quality_gate(quality, generation_mode="primary")
    assert quality.quality_status == "needs_review"
    assert quality.publishable is True
    assert quality.review_required is True


def test_structural_code_blocks_even_with_warning_severity() -> None:
    """Structural codes in BLOCKING_ISSUE_CODES block regardless of
    severity or score."""
    quality = TripPlanQualityResult(
        score=95,
        issues=[
            TripPlanQualityIssue(
                code="EMPTY_DAY",
                severity="warning",
                path="days[0]",
                message="预算缺失",
            )
        ],
    )
    refresh_quality_gate(quality, generation_mode="primary")
    assert quality.quality_status == "blocked"
    assert quality.publishable is False
