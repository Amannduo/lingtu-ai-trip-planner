from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import (
    Attraction,
    Budget,
    DayPlan,
    Hotel,
    Location,
    Meal,
    POIInfo,
    TripPlan,
    TripRequest,
    WeatherInfo,
)
from app.services.transport_budget_service import TransportBudgetService
from app.services.trip_plan_quality_service import TripPlanQualityService


def _request(
    *,
    start: str = "2030-01-01",
    end: str = "2030-01-01",
    days: int = 1,
    travelers: int = 1,
) -> TripRequest:
    return TripRequest(
        origin_city="北京",
        city="北京",
        start_date=start,
        end_date=end,
        travel_days=days,
        travelers=travelers,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
    )


def _attraction(name: str, longitude: float = 116.397, category: str = "博物馆") -> Attraction:
    return Attraction(
        name=name,
        address="北京市东城区",
        location=Location(longitude=longitude, latitude=39.918),
        visit_duration=120,
        description="可执行地点",
        category=category,
        poi_id=f"poi-{name}",
        coordinate_source="amap_poi",
    )


def _day(value: str, index: int, attractions: list[Attraction]) -> DayPlan:
    return DayPlan(
        date=value,
        day_index=index,
        description="城市漫游",
        transportation="公共交通",
        accommodation="经济型酒店",
        attractions=attractions,
        meals=[
            Meal(type="breakfast", name="早餐"),
            Meal(type="lunch", name="午餐"),
            Meal(type="dinner", name="晚餐"),
        ],
    )


def _plan(request: TripRequest, days: list[DayPlan]) -> TripPlan:
    return TripPlan(
        city=request.city,
        start_date=request.start_date,
        end_date=request.end_date,
        days=days,
        overall_suggestions="按计划出行。",
    )


def _poi(poi_id: str, name: str, poi_type: str, longitude: float) -> POIInfo:
    return POIInfo(
        id=poi_id,
        name=name,
        type=poi_type,
        address="成都市测试地址",
        location=Location(longitude=longitude, latitude=30.67),
        rating=4.5,
    )


def test_single_attraction_meal_anchor_never_indexes_past_end() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    day = _day("2030-01-01", 0, [_attraction("故宫博物院")])

    lunch_anchor = planner._meal_anchor(day, 1)
    dinner_anchor = planner._meal_anchor(day, -1)

    assert lunch_anchor == day.attractions[0].location
    assert dinner_anchor == day.attractions[0].location


def test_attraction_verification_searches_have_a_per_plan_budget() -> None:
    class CountingAmap:
        def __init__(self) -> None:
            self.calls = 0

        def search_poi(self, *_args, **_kwargs):
            self.calls += 1
            return []

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = CountingAmap()
    request = _request(start="2030-01-01", end="2030-01-30", days=30)
    days = [
        _day(
            (date(2030, 1, 1) + timedelta(days=index)).isoformat(),
            index,
            [
                _attraction(
                    f"Unmatched {index}-{item}",
                    116.3 + index * 0.001 + item * 0.00001,
                )
                for item in range(10)
            ],
        )
        for index in range(30)
    ]

    result = planner._ground_trip_plan(request, _plan(request, days), [], [])

    assert planner.amap_service.calls == planner._MAX_ATTRACTION_VERIFICATION_SEARCHES
    assert all(not day.attractions for day in result.days)


def test_meal_poi_searches_have_a_per_plan_request_budget() -> None:
    class CountingAmap:
        def __init__(self) -> None:
            self.calls = 0

        def search_poi_around(self, *_args, **_kwargs):
            self.calls += 1
            return []

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = CountingAmap()
    request = _request(start="2030-01-01", end="2030-01-30", days=30)
    days = [
        _day(
            (date(2030, 1, 1) + timedelta(days=index)).isoformat(),
            index,
            [_attraction(f"POI {index}", 116.3 + index * 0.001)],
        )
        for index in range(30)
    ]

    result = planner._finalize_generated_content(request, _plan(request, days))

    assert planner.amap_service.calls == planner._MAX_MEAL_POI_SEARCHES
    assert len(result.days) == 30
    assert all(len(day.meals) == 3 for day in result.days)
    assert any(not meal.poi_id for day in result.days for meal in day.meals)


def test_weather_normalization_uses_service_data_not_model_weather() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    request = _request()
    plan = _plan(request, [_day(request.start_date, 0, [_attraction("故宫博物院")])])
    plan.weather_info = [
        WeatherInfo(
            date=request.start_date,
            day_weather="模型编造",
            night_weather="模型编造",
            day_temp=99,
            night_temp=99,
            wind_direction="",
            wind_power="",
        )
    ]
    service_weather = [
        WeatherInfo(
            date=request.start_date,
            day_weather="晴",
            night_weather="多云",
            day_temp=28,
            night_temp=18,
            wind_direction="东风",
            wind_power="1-2级",
        )
    ]

    result = planner._normalize_plan_dates_and_weather(
        request,
        plan,
        "天气服务结果",
        service_weather,
    )

    assert len(result.weather_info) == 1
    assert result.weather_info[0].day_weather == "晴"
    assert result.weather_info[0].day_temp == 28


def test_fallback_preserves_service_weather_and_cannot_score_100() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    request = _request()
    poi = _poi("amap-1", "故宫博物院", "风景名胜;博物馆", 116.397)
    plan = planner._create_fallback_plan(request, [poi])
    plan = planner._normalize_plan_dates_and_weather(
        request,
        plan,
        "天气服务结果",
        [
            WeatherInfo(
                date=request.start_date,
                day_weather="晴",
                night_weather="多云",
                day_temp=28,
                night_temp=18,
                wind_direction="东风",
                wind_power="1-2级",
            )
        ],
    )

    quality = TripPlanQualityService().evaluate(request, plan)

    assert [item.date for item in plan.weather_info] == [request.start_date]
    assert plan.generation_mode == "map_fallback"
    assert quality.score <= 70
    assert any(issue.code == "FALLBACK_PLAN" for issue in quality.issues)


def test_missing_weather_inside_sixteen_day_window_is_penalized() -> None:
    travel_date = (date.today() + timedelta(days=10)).isoformat()
    request = _request(start=travel_date, end=travel_date)
    plan = _plan(request, [_day(travel_date, 0, [_attraction("故宫博物院")])])

    quality = TripPlanQualityService().evaluate(request, plan)

    assert any(issue.code == "WEATHER_GAP" for issue in quality.issues)
    assert quality.score < 100


def test_street_only_itinerary_triggers_diversity_warning() -> None:
    start = date(2030, 1, 1)
    values = [(start + timedelta(days=index)).isoformat() for index in range(4)]
    request = _request(start=values[0], end=values[-1], days=4)
    days = [
        _day(value, index, [_attraction(f"测试街区{index}", 116.30 + index * 0.01, "特色街区")])
        for index, value in enumerate(values)
    ]

    quality = TripPlanQualityService().evaluate(request, _plan(request, days))

    assert any(
        issue.code == "ATTRACTION_TYPE_CONCENTRATION"
        for issue in quality.issues
    )


def test_hotel_more_than_eight_kilometers_from_itinerary_warns() -> None:
    request = _request()
    day = _day(request.start_date, 0, [_attraction("故宫博物院", 116.397)])
    day.hotel = Hotel(
        name="远郊测试酒店",
        address="北京市远郊",
        location=Location(longitude=116.52, latitude=39.918),
        poi_id="hotel-far",
    )

    quality = TripPlanQualityService().evaluate(request, _plan(request, [day]))

    assert any(issue.code == "HOTEL_TOO_FAR" for issue in quality.issues)


def test_meals_and_tickets_are_multiplied_by_travelers() -> None:
    request = _request(travelers=2)
    attraction = _attraction("故宫博物院")
    attraction.ticket_price = 50
    day = _day(request.start_date, 0, [attraction])
    day.meals = [
        Meal(type="breakfast", name="早餐", estimated_cost=10),
        Meal(type="lunch", name="午餐", estimated_cost=20),
        Meal(type="dinner", name="晚餐", estimated_cost=30),
    ]
    plan = _plan(request, [day])
    service = TransportBudgetService.__new__(TransportBudgetService)

    assert service._sum_meal_costs(plan, request.travelers) == 120
    assert service._sum_attraction_costs(plan, request.travelers) == 100


def test_poi_diversifier_filters_weak_places_and_caps_streets() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    request = _request(days=7, end="2030-01-07").model_copy(
        update={"city": "成都", "origin_city": "西安", "preferences": ["历史文化", "自然风光"]}
    )
    pois = [
        _poi("bad-car", "五龙山汽车街区", "汽车服务;汽车文化", 104.01),
        _poi("bad-shop", "万象街区（亚洲湾店）", "风景名胜;特色街区", 104.02),
        _poi("museum", "成都博物馆", "风景名胜;博物馆", 104.03),
        _poi("park", "人民公园", "风景名胜;公园", 104.04),
    ] + [
        _poi(f"street-{index}", f"特色步行街{index}", "风景名胜;步行街", 104.05 + index * 0.01)
        for index in range(8)
    ]

    result = planner._diversify_attraction_pois(request, pois)
    names = [poi.name for poi in result]
    street_count = sum("街" in poi.name for poi in result)

    assert "五龙山汽车街区" not in names
    assert "万象街区（亚洲湾店）" not in names
    assert "成都博物馆" in names
    assert "人民公园" in names
    assert street_count <= 4

def test_model_cannot_self_certify_unmatched_poi_coordinates() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = type(
        "EmptyAmap",
        (),
        {"search_poi": lambda *_args, **_kwargs: []},
    )()
    request = _request()
    attraction = _attraction("模型虚构地点", category="模型伪造类别")
    attraction.poi_id = "forged-amap-id"
    attraction.coordinate_source = "amap_poi"
    attraction.description = "SENTINEL模型虚构描述"
    attraction.ticket_price = 777
    plan = _plan(request, [_day(request.start_date, 0, [attraction])])

    unrelated = _poi(
        "unrelated-real-poi",
        "人民公园",
        "风景名胜;公园",
        116.410,
    )
    result = planner._ground_trip_plan(request, plan, [unrelated], [])

    # The invented object is removed; only after the whole day becomes empty
    # may grounding create a brand-new, neutral server-backed attraction.
    filled = result.days[0].attractions[0]
    assert filled is not attraction
    assert filled.name == "人民公园"
    assert filled.poi_id == "unrelated-real-poi"
    assert filled.coordinate_source == "amap_poi"
    assert filled.description == ""
    assert filled.ticket_price == 0
    assert result.generation_mode == "repaired"
    assert "1 个整天空白日期" in result.overall_suggestions
    assert "SENTINEL" not in result.overall_suggestions

def test_amap_misclassified_restaurant_is_not_an_attraction() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    restaurant = _poi(
        "food-1",
        "八潮天燚天妇罗",
        "风景名胜;风景名胜;风景名胜",
        104.06,
    )
    park = _poi("park-1", "交子公园", "风景名胜;公园广场;公园", 104.07)

    assert planner._is_suitable_attraction_poi(restaurant) is False
    assert planner._is_suitable_attraction_poi(park) is True
    assert planner._attraction_poi_category(park) == "nature"


def test_short_public_transport_segment_uses_walking_route() -> None:
    captured = {}

    class RouteAmap:
        @staticmethod
        def plan_route(**kwargs):
            captured.update(kwargs)
            return {
                "route": {
                    "paths": [
                        {
                            "distance": "180",
                            "duration": "150",
                            "steps": [
                                {
                                    "polyline": (
                                        "116.397000,39.918000;"
                                        "116.398000,39.918000"
                                    )
                                }
                            ],
                        }
                    ]
                }
            }

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = RouteAmap()
    request = _request()
    origin = _attraction("起点", 116.397)
    destination = _attraction("终点", 116.398)

    segment = planner._plan_route_segment(
        request,
        origin,
        destination,
        route_type="transit",
        timeout=5,
    )

    assert captured["route_type"] == "walking"
    assert segment is not None
    assert segment.route_type == "walking"
    assert segment.verified is True


def test_model_restaurant_coordinates_require_server_poi_proof() -> None:
    class EmptyAmap:
        @staticmethod
        def search_poi(*_args, **_kwargs):
            return []

        @staticmethod
        def search_poi_around(*_args, **_kwargs):
            return []

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = EmptyAmap()
    request = _request()
    attraction_poi = _poi(
        "amap-attraction",
        "故宫博物院",
        "风景名胜;博物馆",
        116.397,
    )
    day = _day(request.start_date, 0, [_attraction("故宫博物院")])
    day.meals = [
        Meal(
            type=meal_type,
            name=f"SENTINEL伪造{meal_type}餐厅",
            address="模型伪造地址",
            location=Location(longitude=116.397, latitude=39.918),
            poi_id=f"forged-{index}",
            coordinate_source="amap_poi",
        )
        for index, meal_type in enumerate(("breakfast", "lunch", "dinner"))
    ]
    plan = _plan(request, [day])

    grounded = planner._ground_trip_plan(request, plan, [attraction_poi], [])
    assert grounded.days[0].meals == []

    finalized = planner._finalize_generated_content(request, grounded)
    assert all("SENTINEL" not in meal.name for meal in finalized.days[0].meals)
    assert all(not meal.poi_id for meal in finalized.days[0].meals)

def test_intercity_fallback_unit_is_single_person_roundtrip() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    service.flyai_enabled = False
    request = _request(travelers=2).model_copy(
        update={
            "origin_city": "西安",
            "city": "成都",
            "intercity_transportation": "高铁",
        }
    )

    quote = service._estimate_intercity_transport(request)

    assert quote.source == "heuristic_transport"
    assert quote.unit_price == 600
    assert quote.total_price == 1200



def test_model_hotel_is_removed_when_server_has_no_verified_candidate() -> None:
    class EmptyAmap:
        @staticmethod
        def search_poi(*_args, **_kwargs):
            return []

        @staticmethod
        def search_poi_around(*_args, **_kwargs):
            return []

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = EmptyAmap()
    request = _request()
    attraction_poi = _poi(
        "amap-attraction",
        "故宫博物院",
        "风景名胜;博物馆",
        116.397,
    )
    day = _day(request.start_date, 0, [_attraction("故宫博物院")])
    day.hotel = Hotel(
        name="SENTINEL伪造酒店",
        address="模型伪造地址",
        location=Location(longitude=116.397, latitude=39.918),
        poi_id="forged-hotel",
        estimated_cost=1,
    )
    plan = _plan(request, [day])

    grounded = planner._ground_trip_plan(request, plan, [attraction_poi], [])

    assert grounded.days[0].hotel is None


def test_budget_service_failure_clears_model_written_budget() -> None:
    class BrokenBudgetService:
        @staticmethod
        def estimate_budget(*_args, **_kwargs):
            raise TimeoutError("budget provider timeout")

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.budget_service = BrokenBudgetService()
    request = _request()
    plan = _plan(request, [_day(request.start_date, 0, [_attraction("故宫博物院")])])
    plan.budget = Budget(total=1, total_hotels=1, hotel_unit_price=1)

    with pytest.raises(TimeoutError):
        planner._apply_budget_estimate(request, plan)

    assert plan.budget is None


def test_attraction_search_uses_landmarks_instead_of_weak_generic_query() -> None:
    calls: list[str] = []

    class SearchAmap:
        @staticmethod
        def search_poi(keyword, _city):
            calls.append(keyword)
            return [
                _poi(
                    f"poi-{keyword}",
                    f"{keyword}候选",
                    f"风景名胜;{keyword}",
                    104.01 + len(calls) * 0.01,
                )
            ]

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = SearchAmap()
    request = _request().model_copy(update={"city": "成都"})

    result = planner._search_attractions(request)

    assert "名胜古迹" in calls
    assert "景点" not in calls
    assert any("名胜古迹" in poi.name for poi in result)


def test_relaxation_preference_prioritizes_nature_not_theme_parks() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    request = _request().model_copy(update={"preferences": ["休闲"]})
    pois = [
        _poi("museum", "城市博物馆", "风景名胜;博物馆", 116.39),
        _poi("theme", "城市主题乐园", "风景名胜;主题乐园", 116.40),
        _poi("park", "人民公园", "风景名胜;公园", 116.41),
    ]

    result = planner._diversify_attraction_pois(request, pois)

    assert result[0].name == "人民公园"


def test_fallback_caps_museums_and_parks_without_erasing_preferences() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    request = _request(days=7, end="2030-01-07").model_copy(
        update={"preferences": ["历史文化", "自然风光"]}
    )
    museums = [
        _poi(f"museum-{index}", f"测试博物馆{index}", "风景名胜;博物馆", 116.20 + index * 0.01)
        for index in range(6)
    ]
    parks = [
        _poi(f"park-{index}", f"测试公园{index}", "风景名胜;公园", 116.30 + index * 0.01)
        for index in range(6)
    ]
    landmarks = [
        _poi(f"temple-{index}", f"测试古寺{index}", "风景名胜;名胜古迹", 116.40 + index * 0.01)
        for index in range(7)
    ]

    plan = planner._create_fallback_plan(request, museums + parks + landmarks)
    attractions = [item for day in plan.days for item in day.attractions]
    museum_count = sum("博物馆" in f"{item.name} {item.category}" for item in attractions)
    park_count = sum("公园" in f"{item.name} {item.category}" for item in attractions)

    assert museum_count <= 3
    assert park_count <= 4
    assert museum_count > 0  # explicit preference remains represented
    assert park_count > 0
    assert all(day.attractions for day in plan.days)

    quality = TripPlanQualityService().evaluate(request, plan)
    assert not any(issue.code == "TOO_MANY_MUSEUMS" for issue in quality.issues)
    assert not any(issue.code == "TOO_MANY_PARKS" for issue in quality.issues)


def test_map_subsearch_timeouts_do_not_discard_verified_candidate_pool() -> None:
    class TimeoutAmap:
        @staticmethod
        def search_poi(*_args, **_kwargs):
            raise TimeoutError("poi timeout")

        @staticmethod
        def search_poi_around(*_args, **_kwargs):
            raise TimeoutError("around timeout")

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = TimeoutAmap()
    request = _request()
    verified = _poi(
        "verified-1",
        "故宫博物院",
        "风景名胜;博物馆",
        116.397,
    )
    # Similarity is between the acceptance and sub-search thresholds, so
    # the timed-out sub-search must retain this semantically close pool match.
    day = _day(request.start_date, 0, [_attraction("北京故宫博物馆")])
    plan = _plan(request, [day])

    grounded = planner._ground_trip_plan(request, plan, [verified], [])
    finalized = planner._finalize_generated_content(request, grounded)

    assert finalized.days[0].attractions[0].poi_id == "verified-1"
    assert finalized.days[0].attractions[0].coordinate_source == "amap_poi"
    assert len(finalized.days[0].meals) == 3
    assert all(meal.poi_id == "" for meal in finalized.days[0].meals)


def test_economy_hotel_filter_rejects_youth_bed_candidate() -> None:
    class EmptyAmap:
        @staticmethod
        def search_poi_around(*_args, **_kwargs):
            return []

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = EmptyAmap()
    request = _request()
    plan = _plan(request, [_day(request.start_date, 0, [_attraction("故宫博物院")])])
    youth = _poi(
        "youth",
        "逆时光青年旅舍",
        "住宿服务;旅馆招待所;青年旅舍",
        116.398,
    )
    hotel = _poi(
        "hotel",
        "安心经济酒店",
        "住宿服务;宾馆酒店",
        116.399,
    )

    selected = planner._select_central_hotel(request, plan, [youth, hotel])

    assert selected is not None
    assert selected.poi_id == "hotel"


def test_duplicate_model_attraction_is_not_replaced_by_another_pool_poi() -> None:
    class EmptyAmap:
        @staticmethod
        def search_poi(*_args, **_kwargs):
            return []

        @staticmethod
        def search_poi_around(*_args, **_kwargs):
            return []

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = EmptyAmap()
    request = _request()
    museum = _poi(
        "museum-1",
        "故宫博物院",
        "风景名胜;博物馆",
        116.397,
    )
    park = _poi(
        "park-1",
        "人民公园",
        "风景名胜;公园",
        116.410,
    )
    day = _day(
        request.start_date,
        0,
        [_attraction("故宫博物院"), _attraction("故宫博物院")],
    )

    grounded = planner._ground_trip_plan(request, _plan(request, [day]), [museum, park], [])

    assert [item.poi_id for item in grounded.days[0].attractions] == ["museum-1"]
    assert all(item.name != "人民公园" for item in grounded.days[0].attractions)
    assert grounded.generation_mode == "primary"


def test_verified_poi_overrides_model_category_duration_and_day_preferences() -> None:
    class EmptyAmap:
        @staticmethod
        def search_poi(*_args, **_kwargs):
            return []

        @staticmethod
        def search_poi_around(*_args, **_kwargs):
            return []

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = EmptyAmap()
    request = _request()
    museum = _poi(
        "museum-1",
        "故宫博物院",
        "风景名胜;文化场馆;博物馆",
        116.397,
    )
    forged = _attraction("故宫博物院", category="公园")
    forged.visit_duration = 30
    day = _day(request.start_date, 0, [forged])
    day.transportation = "自驾"
    day.accommodation = "豪华酒店"

    grounded = planner._ground_trip_plan(request, _plan(request, [day]), [museum], [])
    finalized = planner._finalize_generated_content(request, grounded)
    attraction = finalized.days[0].attractions[0]

    assert attraction.category == "博物馆"
    assert attraction.visit_duration == 150
    assert "展览" in attraction.description
    assert finalized.days[0].transportation == request.transportation
    assert finalized.days[0].accommodation == request.accommodation


def test_day_description_is_rebuilt_after_invented_place_is_removed() -> None:
    class EmptyAmap:
        @staticmethod
        def search_poi(*_args, **_kwargs):
            return []

        @staticmethod
        def search_poi_around(*_args, **_kwargs):
            return []

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = EmptyAmap()
    request = _request()
    verified = _poi(
        "museum-1",
        "故宫博物院",
        "风景名胜;博物馆",
        116.397,
    )
    day = _day(
        request.start_date,
        0,
        [_attraction("SENTINEL火星基地"), _attraction("故宫博物院")],
    )
    day.description = "上午前往SENTINEL火星基地，下午自由活动。"

    grounded = planner._ground_trip_plan(request, _plan(request, [day]), [verified], [])
    finalized = planner._finalize_generated_content(request, grounded)

    assert [item.name for item in finalized.days[0].attractions] == ["故宫博物院"]
    assert "SENTINEL" not in finalized.days[0].description
    assert "故宫博物院" in finalized.days[0].description


def test_museum_cap_selects_late_high_quality_core_venues_before_restoring_order() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    request = _request(days=7, end="2030-01-07").model_copy(
        update={"city": "成都", "preferences": ["历史文化"]}
    )

    def museum(poi_id: str, name: str, rating: float, longitude: float) -> POIInfo:
        item = _poi(poi_id, name, "风景名胜;文化场馆;博物馆", longitude)
        item.rating = rating
        return item

    candidates = [
        museum("finance", "交子金融博物馆", 4.6, 104.01),
        museum("aviation", "航空博物馆", 4.6, 104.02),
        museum("car", "老爷车博物馆", 4.7, 104.03),
        _poi("temple", "文殊院", "风景名胜;名胜古迹;寺庙", 104.04),
        museum("city", "成都博物馆", 4.9, 104.05),
        museum("dufu", "杜甫草堂博物馆", 4.8, 104.06),
        museum("province", "四川博物院", 4.7, 104.07),
    ]

    result = planner._cap_repetitive_experiences(request, candidates)
    museum_names = [
        poi.name
        for poi in result
        if "museum" in planner._attraction_experience_tags(poi)
    ]

    assert museum_names == ["成都博物馆", "杜甫草堂博物馆", "四川博物院"]
    assert "文殊院" in [poi.name for poi in result]

    aviation_request = request.model_copy(update={"preferences": ["航空"]})
    aviation_result = planner._cap_repetitive_experiences(
        aviation_request, candidates
    )
    aviation_museums = [
        poi.name
        for poi in aviation_result
        if "museum" in planner._attraction_experience_tags(poi)
    ]

    # An explicit topical preference may promote a lower-rated specialist
    # venue, while the remaining slots still use provider quality.
    assert aviation_museums == ["航空博物馆", "成都博物馆", "杜甫草堂博物馆"]


def test_empty_last_day_fill_prefers_nearby_verified_candidate() -> None:
    class EmptyAmap:
        @staticmethod
        def search_poi(*_args, **_kwargs):
            return []

        @staticmethod
        def search_poi_around(*_args, **_kwargs):
            return []

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = EmptyAmap()
    request = _request(days=2, end="2030-01-02")
    center = _poi(
        "center",
        "故宫博物院",
        "风景名胜;博物馆",
        116.397,
    )
    near = _poi(
        "near",
        "景山公园",
        "风景名胜;公园",
        116.407,
    )
    near.rating = 4.3
    far = _poi(
        "far",
        "远郊国家博物馆",
        "风景名胜;国家级;博物馆",
        118.000,
    )
    far.rating = 5.0
    days = [
        _day("2030-01-01", 0, [_attraction("故宫博物院")]),
        _day("2030-01-02", 1, [_attraction("SENTINEL火星基地")]),
    ]

    grounded = planner._ground_trip_plan(
        request,
        _plan(request, days),
        [center, near, far],
        [],
    )

    assert [item.poi_id for item in grounded.days[0].attractions] == ["center"]
    assert [item.poi_id for item in grounded.days[1].attractions] == ["near"]
    assert grounded.generation_mode == "repaired"
    assert "1 个整天空白日期" in grounded.overall_suggestions
