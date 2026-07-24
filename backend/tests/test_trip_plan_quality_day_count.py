"""Pure quality-gate day-count checks (no planner/graph/jobs)."""

from __future__ import annotations

from app.models.schemas import (
    DayPlan,
    TripPlan,
    TripRequest,
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


def test_quality_flags_extra_day_as_day_count_mismatch() -> None:
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
    result = TripPlanQualityService().evaluate(_request(), plan)
    codes = {issue.code for issue in result.issues if issue.severity == "error"}
    assert "DAY_COUNT_MISMATCH" in codes
    assert result.publishable is False


def test_quality_flags_defensive_crop_marker() -> None:
    plan = TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        overall_suggestions="原始建议 【系统防御】规划输出天数超过请求的2天，已截断。",
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
    )
    result = TripPlanQualityService().evaluate(_request(), plan)
    codes = {issue.code for issue in result.issues if issue.severity == "error"}
    assert "DAY_COUNT_MISMATCH" in codes
    assert result.publishable is False


def test_quality_passes_matching_two_day_span() -> None:
    plan = TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        overall_suggestions="正常两日",
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
    result = TripPlanQualityService().evaluate(_request(), plan)
    day_errors = [
        i for i in result.issues
        if i.severity == "error" and i.code == "DAY_COUNT_MISMATCH"
    ]
    assert day_errors == []
