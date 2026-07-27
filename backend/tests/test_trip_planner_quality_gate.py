"""Public MultiAgentTripPlanner.plan_trip quality hard-gate."""

from __future__ import annotations

import sys
import types

import pytest

hello_agents = types.ModuleType("hello_agents")
hello_agents.SimpleAgent = object
hello_agents.HelloAgentsLLM = object
sys.modules.setdefault("hello_agents", hello_agents)

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import DayPlan, TripPlan, TripRequest
from app.services.trip_generation_errors import (
    TripGenerationCancelledError,
    TripPlanQualityRejectedError,
)
from app.services.trip_plan_quality_service import TripPlanQualityService


def _request() -> TripRequest:
    return TripRequest(
        origin_city="上海",
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        travel_days=2,
        travelers=2,
        transportation="公共交通",
        accommodation="经济型酒店",
    )


def _two_day_plan(*, suggestions: str = "正常两日") -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        overall_suggestions=suggestions,
        days=[
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="第1天",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            ),
            DayPlan(
                date="2030-08-03",
                day_index=1,
                description="第2天",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            ),
        ],
    )


class _GraphStub:
    def __init__(self, plan: TripPlan):
        self.plan = plan

    def run(self, _request, _progress_callback=None):
        return self.plan


def test_cancelled_error_carries_reason() -> None:
    error = TripGenerationCancelledError("user_cancelled")
    assert str(error) == "user_cancelled"
    assert error.reason == "user_cancelled"


def test_plan_trip_returns_publishable_plan() -> None:
    request = _request()
    plan = _two_day_plan()
    # Force a clean quality attachment as graph would.
    plan.quality = TripPlanQualityService().evaluate(request, plan)
    # Day-count alone may still fail empty-day; force publishable for gate unit test.
    plan.quality.publishable = True
    plan.quality.review_required = False
    plan.quality.status = "passed"

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.trip_graph = _GraphStub(plan)
    result = planner.plan_trip(request)
    assert result is plan


def test_plan_trip_rejects_extra_day_count_mismatch() -> None:
    request = _request()
    plan = _two_day_plan()
    plan.days.append(
        DayPlan(
            date="2030-08-04",
            day_index=2,
            description="多余第三天",
            transportation="公共交通",
            accommodation="经济型酒店",
            attractions=[],
        )
    )
    plan.quality = TripPlanQualityService().evaluate(request, plan)
    assert plan.quality.publishable is False

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.trip_graph = _GraphStub(plan)
    with pytest.raises(TripPlanQualityRejectedError) as exc:
        planner.plan_trip(request)
    assert "DAY_COUNT_MISMATCH" in str(exc.value)
    assert exc.value.plan is plan


def test_plan_trip_rejects_defensive_crop_marker_even_if_days_match_request() -> None:
    request = _request()
    plan = _two_day_plan(
        suggestions="原始建议 【系统防御】规划输出天数超过请求的2天，已截断。"
    )
    plan.quality = TripPlanQualityService().evaluate(request, plan)
    assert plan.quality.publishable is False
    assert len(plan.days) == 2

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.trip_graph = _GraphStub(plan)
    with pytest.raises(TripPlanQualityRejectedError) as exc:
        planner.plan_trip(request)
    assert "DAY_COUNT_MISMATCH" in str(exc.value)


def test_plan_trip_reruns_quality_when_missing() -> None:
    request = _request()
    plan = _two_day_plan()
    plan.quality = None
    # Extra day so re-run quality fails hard.
    plan.days.append(
        DayPlan(
            date="2030-08-04",
            day_index=2,
            description="多余第三天",
            transportation="公共交通",
            accommodation="经济型酒店",
            attractions=[],
        )
    )

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.trip_graph = _GraphStub(plan)
    with pytest.raises(TripPlanQualityRejectedError):
        planner.plan_trip(request)
    assert plan.quality is not None
    assert plan.quality.publishable is False


def test_allow_unpublishable_returns_failed_plan_for_diagnostics() -> None:
    request = _request()
    plan = _two_day_plan(
        suggestions="【系统防御】规划输出天数超过请求的2天，已截断。"
    )
    plan.quality = TripPlanQualityService().evaluate(request, plan)
    assert plan.quality.publishable is False

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.trip_graph = _GraphStub(plan)
    result = planner.plan_trip(request, allow_unpublishable=True)
    assert result is plan
    assert result.quality is not None
    assert result.quality.publishable is False
