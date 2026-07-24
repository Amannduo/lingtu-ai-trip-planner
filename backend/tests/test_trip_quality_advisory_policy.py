"""Tests for advisory quality policy, budget relaxation, and preference-aware checks."""

from __future__ import annotations

from app.models.schemas import (
    Attraction,
    DayPlan,
    Hotel,
    Location,
    TripPlan,
    TripRequest,
)
from app.services.trip_plan_quality_service import (
    BLOCKING_ISSUE_CODES,
    get_trip_plan_quality_service,
    issue_disposition,
)


def _location() -> Location:
    return Location(longitude=120.15, latitude=30.28)


def _base_request(**kwargs) -> TripRequest:
    data = {
        "origin_city": "上海",
        "city": "杭州",
        "start_date": "2030-08-02",
        "end_date": "2030-08-03",
        "travel_days": 2,
        "travelers": 2,
        "transportation": "公共交通",
        "accommodation": "经济型酒店",
        "preferences": [],
        "free_text_input": "",
    }
    data.update(kwargs)
    return TripRequest(**data)


def _base_plan(**kwargs) -> TripPlan:
    data = {
        "city": "杭州",
        "start_date": "2030-08-02",
        "end_date": "2030-08-03",
        "overall_suggestions": "整体建议",
        "days": [
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="第1天",
                transportation="公共交通",
                accommodation="经济型酒店",
                hotel=Hotel(
                    name="杭州西湖宜必思酒店",
                    address="杭州市延安路",
                    location=_location(),
                    price_range="200-300",
                    rating="4.5",
                    distance="1km",
                    type="经济型",
                    poi_id="B001",
                ),
                attractions=[
                    Attraction(
                        name="西湖",
                        address="杭州市西湖区",
                        location=_location(),
                        visit_duration=120,
                        description="西湖风景名胜区",
                        category="公园",
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
                        address="杭州市灵隐路",
                        location=_location(),
                        visit_duration=120,
                        description="灵隐景区",
                        category="古迹",
                    )
                ],
            ),
        ],
    }
    data.update(kwargs)
    return TripPlan(**data)


def test_issue_disposition_classification():
    assert issue_disposition("DAY_COUNT_MISMATCH") == "blocking"
    assert issue_disposition("CITY_MISMATCH") == "blocking"
    assert issue_disposition("BUDGET_MISSING") == "advisory"
    assert issue_disposition("TOO_MANY_MUSEUMS") == "advisory"
    assert issue_disposition("WEB_AUDIT_FAILED") == "advisory"


def test_single_day_same_city_zero_budget_is_publishable():
    service = get_trip_plan_quality_service()
    req = _base_request(
        origin_city="杭州",
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-02",
        travel_days=1,
        budget=0,
        free_text_input="免费同城公园漫步",
    )
    plan = _base_plan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-02",
        days=[
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="一日游",
                transportation="步行",
                accommodation="无需住宿",
                attractions=[
                    Attraction(
                        name="西湖公园",
                        address="杭州市",
                        location=_location(),
                        visit_duration=120,
                        description="免费公园漫步",
                    )
                ],
            )
        ],
    )

    result = service.evaluate(req, plan)
    assert result.publishable is True
    assert result.status in {"passed", "warning"}
    assert not any(issue.code in BLOCKING_ISSUE_CODES for issue in result.issues)


def test_single_day_intercity_zero_budget_has_advisory_warning():
    service = get_trip_plan_quality_service()
    req = _base_request(
        origin_city="西安",
        city="成都",
        start_date="2030-08-02",
        end_date="2030-08-02",
        travel_days=1,
        budget=0,
        intercity_transportation="高铁",
    )
    plan = _base_plan(
        city="成都",
        start_date="2030-08-02",
        end_date="2030-08-02",
        days=[
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="一日跨城高铁游",
                transportation="高铁",
                accommodation="无需住宿",
                attractions=[
                    Attraction(
                        name="锦里",
                        address="成都市",
                        location=_location(),
                        visit_duration=120,
                        description="打卡游览",
                    )
                ],
            )
        ],
    )

    result = service.evaluate(req, plan)
    assert result.publishable is True
    assert result.review_required is True
    assert any(issue.code in {"BUDGET_IMPLAUSIBLY_LOW", "BUDGET_MISSING"} for issue in result.issues)


def test_same_city_one_day_walk_has_no_transport_warning():
    service = get_trip_plan_quality_service()
    req = _base_request(
        origin_city="杭州",
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-02",
        travel_days=1,
        transportation="步行",
    )
    plan = _base_plan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-02",
        days=[
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="同城步行",
                transportation="步行",
                accommodation="无需住宿",
                attractions=[
                    Attraction(
                        name="西湖公园",
                        address="杭州市",
                        location=_location(),
                        visit_duration=120,
                        description="免费公园漫步",
                    )
                ],
            )
        ],
    )

    result = service.evaluate(req, plan)
    assert result.publishable is True
    assert not any(issue.code == "TRANSPORT_REFERENCE_MISMATCH" for issue in result.issues)


def test_intercity_natural_language_transport_passes():
    service = get_trip_plan_quality_service()
    req = _base_request(
        origin_city="西安",
        city="成都",
        start_date="2030-08-02",
        end_date="2030-08-02",
        travel_days=1,
        intercity_transportation="高铁",
    )
    plan = _base_plan(
        city="成都",
        start_date="2030-08-02",
        end_date="2030-08-02",
        days=[
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="早上乘高铁从西安前往成都打卡",
                transportation="高铁",
                accommodation="无需住宿",
                attractions=[
                    Attraction(
                        name="锦里",
                        address="成都市",
                        location=_location(),
                        visit_duration=120,
                        description="打卡游览",
                    )
                ],
            )
        ],
    )

    result = service.evaluate(req, plan)
    assert result.publishable is True
    assert not any(issue.code == "TRANSPORT_REFERENCE_MISMATCH" for issue in result.issues)


def test_museum_enthusiast_preference_relaxes_museum_cap():
    service = get_trip_plan_quality_service()
    req = _base_request(
        preferences=["历史文化", "博物馆研学"],
        free_text_input="想多看几个博物馆",
    )

    attractions = [
        Attraction(
            name=f"博物馆{i}",
            address="地址",
            location=_location(),
            visit_duration=60,
            description="博物馆描述",
            category="博物馆",
        )
        for i in range(5)
    ]
    plan = _base_plan()
    plan.days[0].attractions = attractions[:3]
    plan.days[1].attractions = attractions[3:]

    result = service.evaluate(req, plan)
    assert result.publishable is True
    assert not any(issue.code == "TOO_MANY_MUSEUMS" for issue in result.issues)


def test_usable_map_fallback_is_reviewable_not_blocked():
    service = get_trip_plan_quality_service()
    req = _base_request()
    plan = _base_plan(generation_mode="map_fallback")

    result = service.evaluate(req, plan)
    assert result.publishable is True
    assert result.review_required is True
    assert result.status == "warning"
