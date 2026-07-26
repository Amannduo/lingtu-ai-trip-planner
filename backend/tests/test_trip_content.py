from __future__ import annotations

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import (
    Attraction,
    DayPlan,
    Hotel,
    Location,
    Meal,
    POIInfo,
    TripPlan,
    TripRequest,
)


def _poi(
    poi_id: str,
    name: str,
    poi_type: str,
    longitude: float,
    *,
    rating: float = 4.5,
) -> POIInfo:
    return POIInfo(
        id=poi_id,
        name=name,
        type=poi_type,
        address=f"北京市东城区{name}街1号",
        location=Location(longitude=longitude, latitude=39.91),
        rating=rating,
        district="东城区",
    )


class _AmapStub:
    def search_poi_around(self, keywords, center, **_kwargs):
        if "早餐" in keywords:
            return [
                _poi("invalid", "晨光公园", "风景名胜;公园", 116.31, rating=5.0),
                _poi("breakfast", "京味早点铺", "餐饮服务;快餐厅", 116.32),
            ]
        return [
            _poi("lunch", "京华家常菜", "餐饮服务;中餐厅", 116.42, rating=4.7),
            _poi("dinner", "胡同小馆", "餐饮服务;中餐厅", 116.43, rating=4.6),
        ]


def _request() -> TripRequest:
    return TripRequest(
        city="北京",
        start_date="2026-07-16",
        end_date="2026-07-16",
        travel_days=1,
        travelers=2,
        transportation="公共交通",
        accommodation="舒适型酒店",
        preferences=["历史文化", "自然风光", "美食"],
    )


def _plan() -> TripPlan:
    return TripPlan(
        city="北京",
        start_date="2026-07-16",
        end_date="2026-07-16",
        overall_suggestions="提前预约。",
        days=[
            DayPlan(
                date="2026-07-16",
                day_index=0,
                description="第1天行程",
                transportation="公共交通",
                accommodation="舒适型酒店",
                hotel=Hotel(
                    name="测试酒店",
                    address="北京市东城区",
                    location=Location(longitude=116.30, latitude=39.91),
                ),
                attractions=[
                    Attraction(
                        name="首都博物馆",
                        address="北京市西城区复兴门外大街16号",
                        location=Location(longitude=116.34, latitude=39.91),
                        visit_duration=120,
                        description="这是北京的著名景点",
                        category="博物馆",
                    ),
                    Attraction(
                        name="北海公园",
                        address="北京市西城区文津街1号",
                        location=Location(longitude=116.39, latitude=39.92),
                        visit_duration=120,
                        description="景点详细描述",
                        category="公园",
                    ),
                ],
                meals=[
                    Meal(type="breakfast", name="第1天早餐", description="当地特色早餐"),
                    Meal(type="lunch", name="第1天午餐", description="午餐推荐"),
                    Meal(type="dinner", name="第1天晚餐", description="晚餐推荐"),
                ],
            )
        ],
    )


def test_finalizer_replaces_fixed_durations_generic_copy_and_placeholder_meals() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = _AmapStub()
    assert planner._suggest_visit_duration_minutes(
        Attraction(
            name="中山公园",
            address="北京市东城区中华路4号",
            location=Location(longitude=116.40, latitude=39.91),
            visit_duration=120,
            description="城市公园",
            category="公园",
        )
    ) == 120

    uniform_plan = _plan()
    for attraction in uniform_plan.days[0].attractions:
        attraction.visit_duration = 90
    uniform_result = planner._finalize_generated_content(_request(), uniform_plan)
    assert [item.visit_duration for item in uniform_result.days[0].attractions] == [150, 120]

    result = planner._finalize_generated_content(_request(), _plan())
    day = result.days[0]

    assert [item.visit_duration for item in day.attractions] == [150, 120]
    assert all("著名景点" not in item.description for item in day.attractions)
    assert "展览、馆藏" in day.attractions[0].description
    assert "散步、拍照" in day.attractions[1].description
    assert day.description == (
        "第1天围绕首都博物馆、北海公园展开，"
        "整体节奏以顺路游览和减少折返为主。"
    )
    assert [meal.name for meal in day.meals] == [
        "京味早点铺",
        "京华家常菜",
        "胡同小馆",
    ]
    assert all(meal.address and meal.location for meal in day.meals)
    assert all("核对营业时间" in (meal.description or "") for meal in day.meals)


def test_meal_fallback_is_explicit_when_amap_has_no_reliable_restaurant() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = type(
        "EmptyAmap",
        (),
        {"search_poi_around": lambda *_args, **_kwargs: []},
    )()

    result = planner._finalize_generated_content(_request(), _plan())

    assert len(result.days[0].meals) == 3
    assert all("附近" in meal.name for meal in result.days[0].meals)
    assert all(
        "暂未取得可靠的具体商家数据" in (meal.description or "")
        for meal in result.days[0].meals
    )

def test_parent_trip_prompt_and_finalizer_preserve_people_origin_and_gentle_pacing() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = _AmapStub()
    request = _request().model_copy(
        update={
            "origin_city": "宝鸡扶风",
            "travelers": 3,
            "intercity_transportation": "自驾",
            "free_text_input": "跟父母去避暑，不想太累",
        }
    )
    plan = _plan()
    plan.days[0].attractions.append(
        Attraction(
            name="景山公园",
            address="北京市西城区景山西街44号",
            location=Location(longitude=116.39, latitude=39.92),
            visit_duration=90,
            description="城市公园",
            category="公园",
        )
    )

    prompt = planner._build_planner_query(request, "高德景点", "天气待复核")
    result = planner._finalize_generated_content(request, plan)

    assert "- 出发地: 宝鸡扶风" in prompt
    assert "- 人数: 3人" in prompt
    assert "- 城际交通: 自驾" in prompt
    assert "首日和末日必须为城际往返预留时间" in prompt
    assert "每天最多安排2个主景点" in prompt
    assert len(result.days[0].attractions) == 2
    assert "每天最多保留2个主景点" in result.overall_suggestions
