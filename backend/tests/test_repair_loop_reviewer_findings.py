"""Regression tests for reviewer findings on the quality-repair loop (P0-6).

Pins the review-round fixes:

1. ``_repair_cap`` must never empty a day — dropping a day's only
   attraction raises EMPTY_DAY, a blocking issue strictly worse than the
   museum/park cap violation the repair is meant to relieve.
2. ``_select_final_plan`` ranks deliverability before score: a repair-round
   plan that became blocked must never displace a deliverable needs_review
   snapshot just because its number is higher.
3. A failed budget refresh after a repair keeps the previous budget and
   demotes the plan to needs_review (instead of clearing the budget and
   letting blocking BUDGET_MISSING reject the whole generation).
4. Slot-level capacity errors inside a job worker surface as a retryable
   ``capacity_exhausted`` SSE event, not a generic generation failure.
"""

from __future__ import annotations

import pytest

import app.agents.graph.trip_planning_graph as graph_module
from app.agents.graph.trip_planning_graph import TripPlanningAgentGraph
from app.models.schemas import (
    Attraction,
    Budget,
    DayPlan,
    Location,
    Meal,
    TripPlan,
    TripPlanQualityIssue,
    TripPlanQualityResult,
    TripRequest,
)
from app.services.trip_generation_job_service import (
    TripGenerationCapacityError,
    TripGenerationJobService,
)


def _request() -> TripRequest:
    return TripRequest(
        city="北京",
        start_date="2030-01-01",
        end_date="2030-01-02",
        travel_days=2,
        travelers=1,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
    )


def _attraction(name: str, category: str = "博物馆") -> Attraction:
    return Attraction(
        name=name,
        address="北京市东城区",
        location=Location(longitude=116.397, latitude=39.918),
        visit_duration=120,
        description="参观",
        category=category,
        poi_id=f"amap-{name}",
        coordinate_source="amap_poi",
    )


def _day(index: int, attractions: list[Attraction]) -> DayPlan:
    return DayPlan(
        date=f"2030-01-0{index + 1}",
        day_index=index,
        description=f"第{index + 1}天",
        transportation="公共交通",
        accommodation="经济型酒店",
        attractions=attractions,
        meals=[
            Meal(type="breakfast", name="早餐"),
            Meal(type="lunch", name="午餐"),
            Meal(type="dinner", name="晚餐"),
        ],
    )


def _plan(days: list[DayPlan]) -> TripPlan:
    return TripPlan(
        city="北京",
        start_date="2030-01-01",
        end_date=f"2030-01-0{len(days)}",
        generation_mode="primary",
        overall_suggestions="按计划出行。",
        days=days,
    )


def test_repair_cap_never_empties_a_day() -> None:
    """The last museum sits alone on day 2; the cap repair must drop a
    museum from the crowded day instead of emptying day 2."""
    plan = _plan(
        [
            _day(0, [
                _attraction("一号博物馆"),
                _attraction("二号博物馆"),
                _attraction("三号博物馆"),
                _attraction("中心公园", category="公园"),
            ]),
            _day(1, [_attraction("四号博物馆")]),
        ]
    )
    graph = TripPlanningAgentGraph.__new__(TripPlanningAgentGraph)
    actions: list[str] = []

    repaired = graph._repair_cap(plan, "TOO_MANY_MUSEUMS", actions)

    assert actions, "cap repair should have acted"
    for day in repaired.days:
        assert len(day.attractions) >= 1, "repair must never empty a day"
    museums = [
        a
        for day in repaired.days
        for a in day.attractions
        if "博物馆" in f"{a.name} {a.category or ''}"
    ]
    assert len(museums) == 3


def test_select_final_plan_prefers_deliverable_over_higher_scoring_blocked() -> None:
    blocked = _plan([_day(0, [_attraction("一号博物馆")])])
    blocked.quality = TripPlanQualityResult(
        status="failed",
        score=59,
        publishable=False,
        quality_status="blocked",
        issues=[
            TripPlanQualityIssue(
                code="EMPTY_DAY",
                severity="error",
                path="days[1]",
                message="第2天没有任何安排。",
            )
        ],
    )
    deliverable = _plan([_day(0, [_attraction("一号博物馆")])])
    deliverable.quality = TripPlanQualityResult(
        status="warning",
        score=55,
        publishable=False,
        quality_status="needs_review",
    )

    state = {
        "trip_plan": blocked,
        "best_trip_plan": deliverable,
        "best_quality_score": 55,
    }
    assert TripPlanningAgentGraph._select_final_plan(state) is deliverable


class _AmapStub:
    @staticmethod
    def get_weather(*_args, **_kwargs):
        return []


class _BudgetRefreshFailurePlanner:
    """Budget estimate succeeds during enrich, fails during repair refresh."""

    def __init__(self):
        self.amap_service = _AmapStub()
        self.budget_calls = 0

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
        return _plan(
            [
                _day(0, [
                    _attraction("一号博物馆"),
                    _attraction("中心公园", category="公园"),
                ]),
            ]
        )

    @staticmethod
    def _normalize_plan_dates_and_weather(_request, plan, *_args, **_kwargs):
        return plan

    @staticmethod
    def _ground_trip_plan(_request, plan, *_args, **_kwargs):
        return plan

    @staticmethod
    def _finalize_generated_content(_request, plan):
        return plan

    @staticmethod
    def _apply_route_planning(_request, plan):
        return plan

    def _apply_budget_estimate(self, _request, plan):
        self.budget_calls += 1
        if self.budget_calls > 1:
            raise TimeoutError("budget estimator unavailable")
        plan.budget = Budget(total=100)
        return plan

    @staticmethod
    def _apply_web_guide(_request, plan):
        return plan

    @staticmethod
    def _create_fallback_plan(_request, _pois):  # pragma: no cover
        raise AssertionError("fallback must not run here")


class _NeedsReviewQualityStub:
    def __init__(self, scores: list[int]):
        self.scores = scores
        self.calls = 0

    def evaluate(self, _request, _plan) -> TripPlanQualityResult:
        self.calls += 1
        idx = min(self.calls - 1, len(self.scores) - 1)
        return TripPlanQualityResult(
            status="warning",
            score=self.scores[idx],
            publishable=False,
            quality_status="needs_review",
            issues=[
                TripPlanQualityIssue(
                    code="DAY_SCHEDULE_OVERLOAD",
                    severity="warning",
                    path="days[0]",
                    message="当日行程时长过载。",
                )
            ],
        )


def test_budget_refresh_failure_keeps_budget_and_demotes(monkeypatch) -> None:
    # Rising scores keep the post-refresh round as the best snapshot, so the
    # returned plan is the one that actually lived through the failed budget
    # refresh (a falling sequence would legitimately restore the pre-failure
    # snapshot, which never saw the enrichment error).
    quality = _NeedsReviewQualityStub(scores=[70, 80, 80])
    monkeypatch.setattr(
        graph_module, "get_trip_plan_quality_service", lambda: quality
    )
    planner = _BudgetRefreshFailurePlanner()
    graph = TripPlanningAgentGraph(planner)

    result = graph.run(_request())

    # The stale-but-present budget survives the failed refresh …
    assert result.budget is not None
    assert result.budget.total == 100
    # … and the degradation demotes the plan instead of blocking it.
    assert result.quality is not None
    assert result.quality.quality_status == "needs_review"
    assert any(
        issue.code == "PIPELINE_ENRICHMENT_PARTIAL"
        for issue in result.quality.issues
    )


def test_job_worker_capacity_error_streams_retryable_event() -> None:
    service = TripGenerationJobService(ttl_seconds=30, max_workers=1)
    try:
        def worker(_progress):
            raise TripGenerationCapacityError("all generation slots are busy")

        job = service.start("user:capacity", worker)
        error = None
        for event in service.events(job, after_id=0):
            if event is None:
                continue
            if event["type"] == "error":
                error = event
                break

        assert error is not None
        assert error["error_type"] == "capacity_exhausted"
        assert "稍后重试" in error["message"]
        assert "slots" not in error["message"]
        assert job.status == "failed"
    finally:
        service.shutdown()
