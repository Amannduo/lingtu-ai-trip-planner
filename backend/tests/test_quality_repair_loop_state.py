"""Regression tests for quality-repair-loop state handling (P0-1).

Under LangGraph, values written by ``state[...] = ...`` inside a node or a
conditional-edge function are NOT merged back into the graph channels — only
the dict a node *returns* is.  These tests pin the required behavior:

1. ``repair_count`` accumulates across rounds, so the repair loop is strictly
   bounded by ``_MAX_QUALITY_REPAIRS`` (no GraphRecursionError, no silent
   checkpoint-recovery detour).
2. ``repaired_issue_codes`` survives across rounds, so each issue code is
   attempted at most once.
3. The best-scoring plan seen across rounds is preserved and restored when a
   repair round makes the plan worse.
4. The LangGraph path and the sequential fallback path produce identical
   observable behavior for the same scenario.
"""

from __future__ import annotations

import pytest

import app.agents.graph.trip_planning_graph as graph_module
from app.agents.graph.trip_planning_graph import TripPlanningAgentGraph
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


def _attraction(name: str, duration: int) -> Attraction:
    return Attraction(
        name=name,
        address="北京市东城区",
        location=Location(longitude=116.397, latitude=39.918),
        visit_duration=duration,
        description="历史文化参观",
        category="博物馆",
        poi_id=f"amap-{name}",
        coordinate_source="amap_poi",
    )


def _plan_with_two_attractions() -> TripPlan:
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
                    _attraction("故宫博物院", 240),
                    _attraction("景山公园", 90),
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
    """Minimal planner double; parse always yields a two-attraction plan."""

    def __init__(self):
        self.amap_service = _AmapStub()
        self.primary_calls = 0
        self.route_calls = 0
        self.budget_calls = 0
        self.web_calls = 0

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

    def _run_primary_planner(self, *_args, **_kwargs):
        self.primary_calls += 1
        return "good"

    @staticmethod
    def _parse_response(_response, _request):
        return _plan_with_two_attractions()

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
        return plan

    def _apply_budget_estimate(self, _request, plan):
        self.budget_calls += 1
        return plan

    def _apply_web_guide(self, _request, plan):
        self.web_calls += 1
        return plan

    @staticmethod
    def _create_fallback_plan(_request, _pois):  # pragma: no cover - not hit
        raise AssertionError("fallback must not run in these scenarios")

    def _repair_planner_response(self, _request, _response):  # pragma: no cover
        raise AssertionError("structural repair must not run in these scenarios")


class _QualityStub:
    """Always reports needs_review with one repairable issue.

    ``scores[i]`` is the score returned by the (i+1)-th evaluation; the last
    entry repeats if there are more evaluations than entries.
    """

    def __init__(self, scores: list[int]):
        self.scores = scores
        self.calls = 0

    def evaluate(self, _request, _plan) -> TripPlanQualityResult:
        self.calls += 1
        idx = min(self.calls - 1, len(self.scores) - 1)
        return TripPlanQualityResult(
            status="warning",
            score=self.scores[idx],
            publishable=True,
            review_required=True,
            checked_items=["日程负载"],
            issues=[
                TripPlanQualityIssue(
                    code="DAY_SCHEDULE_OVERLOAD",
                    severity="warning",
                    path="days[0]",
                    message="当日行程时长过载。",
                    suggestion="减少当日景点数量。",
                )
            ],
        )


def _build_graph(monkeypatch, scores: list[int]) -> tuple[TripPlanningAgentGraph, _PlannerStub, _QualityStub]:
    quality = _QualityStub(scores)
    monkeypatch.setattr(
        graph_module, "get_trip_plan_quality_service", lambda: quality
    )
    planner = _PlannerStub()
    graph = TripPlanningAgentGraph(planner)
    return graph, planner, quality


def test_langgraph_repair_loop_is_strictly_bounded(monkeypatch) -> None:
    """The loop must stop after _MAX_QUALITY_REPAIRS rounds, i.e. exactly
    3 quality evaluations (initial + 2 repair rounds), without falling into
    GraphRecursionError / checkpoint recovery."""
    graph, planner, quality = _build_graph(monkeypatch, scores=[70])
    assert graph.graph_available is True

    result = graph.run(_request())

    assert isinstance(result, TripPlan)
    assert planner.primary_calls == 1
    # initial evaluation + one per repair round — nothing more.
    assert quality.calls == 3


def test_repaired_issue_codes_survive_across_rounds(monkeypatch) -> None:
    """Round 1 repairs DAY_SCHEDULE_OVERLOAD (drops one attraction).  The
    stub keeps reporting the same code, but round 2 must NOT repair it again:
    the two-attraction day loses exactly one attraction in total."""
    graph, planner, quality = _build_graph(monkeypatch, scores=[70, 74, 74])

    result = graph.run(_request())

    total_attractions = sum(len(d.attractions or []) for d in result.days)
    assert total_attractions == 1
    # enrich(1) + refresh after the acting round(1); the no-action round must
    # not re-run route planning.
    assert planner.route_calls == 2
    assert planner.budget_calls == 2


def test_best_plan_is_restored_when_repair_makes_it_worse(monkeypatch) -> None:
    """Scores: 70 (2 attractions) → 60 after repair → 55.  The final answer
    must be the best round: score 70 with both attractions intact."""
    graph, planner, quality = _build_graph(monkeypatch, scores=[70, 60, 55])

    result = graph.run(_request())

    assert result.quality is not None
    assert result.quality.score == 70
    assert sum(len(d.attractions or []) for d in result.days) == 2


def test_sequential_fallback_matches_langgraph_behavior(monkeypatch) -> None:
    """The sequential degradation path must show the same observable behavior
    as the LangGraph path for the identical scenario."""
    lg_graph, lg_planner, lg_quality = _build_graph(
        monkeypatch, scores=[70, 60, 55]
    )
    lg_result = lg_graph.run(_request())

    seq_graph, seq_planner, seq_quality = _build_graph(
        monkeypatch, scores=[70, 60, 55]
    )
    seq_graph.graph_available = False
    seq_graph._compiled_graph = None
    seq_result = seq_graph.run(_request())

    assert seq_quality.calls == lg_quality.calls == 3
    assert seq_result.quality.score == lg_result.quality.score == 70
    assert (
        sum(len(d.attractions or []) for d in seq_result.days)
        == sum(len(d.attractions or []) for d in lg_result.days)
        == 2
    )
    assert seq_planner.route_calls == lg_planner.route_calls
    assert seq_planner.budget_calls == lg_planner.budget_calls
