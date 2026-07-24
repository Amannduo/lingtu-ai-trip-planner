"""Day-count quality and planner crop defense (belongs with Commit 3 planning)."""

from __future__ import annotations

import sys
import types

import pytest

hello_agents = types.ModuleType("hello_agents")
hello_agents.SimpleAgent = object
hello_agents.HelloAgentsLLM = object
sys.modules.setdefault("hello_agents", hello_agents)

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import (
    Attraction,
    DayPlan,
    Location,
    TripPlan,
    TripRequest,
)
from app.services.trip_plan_quality_service import TripPlanQualityService


def test_two_day_plan_rejects_extra_day_in_quality() -> None:
    request = TripRequest(
        origin_city="上海",
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        travel_days=2,
        travelers=2,
        transportation="公共交通",
        accommodation="经济型酒店",
        free_text_input="【时段】周末Sat-Sun·2天\n【抵达建议】建议周五下午抵达（可选）",
    )
    plan = TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        overall_suggestions="测试",
        days=[
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="第1天",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[
                    Attraction(
                        name="西湖",
                        address="杭州",
                        location=Location(longitude=120.1, latitude=30.2),
                        visit_duration=120,
                        description="湖",
                        poi_id="p1",
                        coordinate_source="amap_poi",
                    )
                ],
            ),
            DayPlan(
                date="2030-08-03",
                day_index=1,
                description="第2天",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[
                    Attraction(
                        name="灵隐寺",
                        address="杭州",
                        location=Location(longitude=120.1, latitude=30.24),
                        visit_duration=120,
                        description="寺",
                        poi_id="p2",
                        coordinate_source="amap_poi",
                    )
                ],
            ),
            DayPlan(
                date="2030-08-01",
                day_index=2,
                description="Day 0 周五完整游玩",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            ),
        ],
    )
    result = TripPlanQualityService().evaluate(request, plan)
    codes = {issue.code for issue in result.issues if issue.severity == "error"}
    assert "DAY_COUNT_MISMATCH" in codes


def test_crop_defense_marks_quality_and_clears_budget() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    request = TripRequest(
        origin_city="上海",
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        travel_days=2,
        travelers=2,
        transportation="公共交通",
        accommodation="经济型酒店",
    )
    plan = TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        overall_suggestions="原始建议",
        budget=None,
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
            DayPlan(
                date="2030-08-04",
                day_index=2,
                description="多余第三天",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            ),
        ],
    )
    normalized = planner._normalize_plan_dates_and_weather(request, plan)
    assert len(normalized.days) == 2
    assert "【系统防御】" in (normalized.overall_suggestions or "")
    assert "截断" in (normalized.overall_suggestions or "")
    quality = TripPlanQualityService().evaluate(request, normalized)
    codes = {issue.code for issue in quality.issues if issue.severity == "error"}
    assert "DAY_COUNT_MISMATCH" in codes
    assert quality.publishable is False

    # Public entry must reject even after length was clipped back to 2 days.
    class _GraphStub:
        def run(self, _request, _progress=None):
            normalized.quality = quality
            return normalized

    planner.trip_graph = _GraphStub()
    from app.services.trip_generation_errors import TripPlanQualityRejectedError

    with pytest.raises(TripPlanQualityRejectedError) as exc:
        planner.plan_trip(request)
    assert "DAY_COUNT_MISMATCH" in str(exc.value)
