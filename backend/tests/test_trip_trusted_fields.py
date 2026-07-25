from __future__ import annotations

from app.api.routes.trip import _restore_verified_plan_facts
from app.models.schemas import (
    Attraction,
    Budget,
    DayPlan,
    Location,
    TripPlan,
    WeatherInfo,
)


def _plan(mode: str, weather: str, budget_total: int) -> TripPlan:
    return TripPlan(
        city="北京",
        start_date="2030-01-01",
        end_date="2030-01-01",
        generation_mode=mode,
        overall_suggestions="测试",
        weather_info=[
            WeatherInfo(
                date="2030-01-01",
                day_weather=weather,
                night_weather=weather,
                day_temp=20,
                night_temp=10,
                wind_direction="东风",
                wind_power="1-2级",
            )
        ],
        budget=Budget(total_meals=budget_total, total=budget_total),
        days=[
            DayPlan(
                date="2030-01-01",
                day_index=0,
                description="测试",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[
                    Attraction(
                        name="故宫博物院",
                        address="北京市东城区",
                        location=Location(longitude=116.397, latitude=39.918),
                        visit_duration=180,
                        description="测试",
                        category="博物馆",
                        poi_id="amap-1",
                        coordinate_source="amap_poi",
                    )
                ],
            )
        ],
    )


def test_client_cannot_upgrade_fallback_or_forge_weather_and_budget() -> None:
    existing = _plan("map_fallback", "晴", 500)
    edited = _plan("primary", "暴雪", 1)

    _restore_verified_plan_facts(edited, existing)

    assert edited.generation_mode == "map_fallback"
    assert edited.weather_info[0].day_weather == "晴"
    assert edited.budget is not None
    assert edited.budget.total == 500
