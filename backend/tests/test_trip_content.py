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


def _empty_amap_planner() -> MultiAgentTripPlanner:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.amap_service = type(
        "EmptyAmap",
        (),
        {"search_poi_around": lambda *_args, **_kwargs: []},
    )()
    return planner


def _three_attraction_plan() -> TripPlan:
    attractions = [
        Attraction(
            name=name,
            address="北京市东城区测试路1号",
            location=Location(longitude=116.30 + index * 0.01, latitude=39.91),
            visit_duration=90,
            description="可执行地点",
            category=category,
            poi_id=f"poi-{index}",
            coordinate_source="amap_poi",
        )
        for index, (name, category) in enumerate(
            (
                ("测试公园甲", "公园"),
                ("测试博物馆乙", "博物馆"),
                ("测试科技馆丙", "科技馆"),
            )
        )
    ]
    return TripPlan(
        city="北京",
        start_date="2030-01-01",
        end_date="2030-01-01",
        overall_suggestions="按计划出行。",
        days=[
            DayPlan(
                date="2030-01-01",
                day_index=0,
                description="第1天行程",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=attractions,
                meals=[
                    Meal(type="breakfast", name="早餐"),
                    Meal(type="lunch", name="午餐"),
                    Meal(type="dinner", name="晚餐"),
                ],
            )
        ],
    )


def test_parent_trip_prompt_and_finalizer_preserve_people_origin_and_gentle_pacing() -> None:
    """Explicit parent + low-intensity free text: prompt + finalize density cap."""
    planner = _empty_amap_planner()
    request = _request().model_copy(
        update={
            "origin_city": "宝鸡扶风",
            "travelers": 3,
            "intercity_transportation": "自驾",
            "free_text_input": "跟父母去避暑，不想太累",
        }
    )
    plan = _three_attraction_plan()

    prompt = planner._build_planner_query(request, "高德景点", "天气待复核")
    result = planner._finalize_generated_content(request, plan)

    assert "- 出发地: 宝鸡扶风" in prompt
    assert "- 人数: 3人" in prompt
    assert "- 城际交通: 自驾" in prompt
    assert "首日和末日必须为城际往返预留时间" in prompt
    assert "每天最多安排2个主景点" in prompt
    assert len(result.days[0].attractions) == 2
    assert "降低每日主景点密度" in result.overall_suggestions
    assert "父母" in result.overall_suggestions or "老人" in result.overall_suggestions
    # Meals are not main attractions and survive the density cap.
    assert len(result.days[0].meals) == 3


def test_family_preference_applies_gentle_density_without_requiring_theme_park() -> None:
    """Explicit 亲子 preference lowers density; does not force a theme park."""
    planner = _empty_amap_planner()
    request = _request().model_copy(
        update={
            "origin_city": "北京",
            "preferences": ["亲子", "自然风光"],
            "free_text_input": "",
        }
    )
    plan = _three_attraction_plan()

    assert planner._needs_gentle_pacing(request) is True
    result = planner._finalize_generated_content(request, plan)
    prompt = planner._build_planner_query(request, "高德景点", "天气待复核")

    assert len(result.days[0].attractions) == 2
    assert "降低每日主景点密度" in result.overall_suggestions
    # Neutral note — no parent/elder identity inference for pure family preference.
    assert "父母" not in result.overall_suggestions
    assert "老人" not in result.overall_suggestions
    assert "行动不便" not in result.overall_suggestions
    assert "主题乐园" not in prompt or "不要默认安排主题乐园" in prompt
    assert all("主题乐园" not in (item.name or "") for item in result.days[0].attractions)


def test_slow_pace_free_text_caps_main_attractions_at_two() -> None:
    """缓节奏 / 少走路 / 不赶行程 free text is an explicit gentle signal."""
    planner = _empty_amap_planner()
    request = _request().model_copy(
        update={
            "origin_city": "北京",
            "preferences": ["历史文化"],
            "free_text_input": "缓节奏，不赶行程，少走路，每天少安排",
        }
    )
    plan = _three_attraction_plan()

    assert planner._needs_gentle_pacing(request) is True
    result = planner._finalize_generated_content(request, plan)
    assert len(result.days[0].attractions) == 2
    assert "降低每日主景点密度" in result.overall_suggestions
    assert "医疗" not in result.overall_suggestions
    assert "无障碍" not in result.overall_suggestions
    assert "行动不便" not in result.overall_suggestions


def test_default_trip_keeps_up_to_three_main_attractions_without_pacing_note() -> None:
    """Without explicit gentle markers, do not force elder/family pacing."""
    planner = _empty_amap_planner()
    request = _request().model_copy(
        update={
            "origin_city": "北京",
            "preferences": ["历史文化", "自然风光"],
            "free_text_input": "",
        }
    )
    plan = _three_attraction_plan()

    assert planner._needs_gentle_pacing(request) is False
    result = planner._finalize_generated_content(request, plan)
    assert len(result.days[0].attractions) == 3
    assert "降低每日主景点密度" not in result.overall_suggestions


def test_child_and_elder_markers_align_planner_and_quality_relaxed_pace() -> None:
    """Planner finalize markers stay aligned with quality relaxed-pace detection."""
    from app.services.trip_plan_quality_service import TripPlanQualityService

    quality = TripPlanQualityService()
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    cases = [
        (["亲子"], ""),
        ([], "带儿童出行"),
        (["老人同行"], ""),
        ([], "跟爸妈去，慢一点"),
        ([], "缓节奏"),
    ]
    for preferences, free_text in cases:
        request = _request().model_copy(
            update={"preferences": preferences, "free_text_input": free_text}
        )
        assert planner._needs_gentle_pacing(request) is True, (preferences, free_text)
        assert quality._prefers_relaxed_pace(request) is True, (preferences, free_text)


def _aligned_request_plan(
    *,
    preferences: list[str],
    free_text: str = "",
    origin_city: str = "北京",
):
    """Same-date request/plan pair so date blockers do not mask pace issues."""
    request = _request().model_copy(
        update={
            "origin_city": origin_city,
            "city": "北京",
            "start_date": "2030-01-01",
            "end_date": "2030-01-01",
            "travel_days": 1,
            "preferences": preferences,
            "free_text_input": free_text,
        }
    )
    plan = _three_attraction_plan()
    for attraction in plan.days[0].attractions:
        attraction.coordinate_source = "amap_poi"
        attraction.poi_id = attraction.poi_id or f"poi-{attraction.name}"
    return request, plan


def test_relaxed_pace_density_breach_is_advisory_not_blocking() -> None:
    """Explicit gentle request with >2 attractions yields advisory code, not failed plan."""
    from app.services.trip_plan_quality_service import (
        TripPlanQualityService,
        issue_disposition,
    )

    request, plan = _aligned_request_plan(
        preferences=["轻松出游"],
        free_text="缓节奏",
    )
    result = TripPlanQualityService().evaluate(request, plan)
    overload = [issue for issue in result.issues if issue.code == "RELAXED_PACE_OVERLOAD"]
    assert overload, [issue.code for issue in result.issues]
    assert all(issue.severity == "warning" for issue in overload)
    assert all(issue_disposition(issue) == "advisory" for issue in overload)
    # Pace density alone must not force blocking/failed status.
    assert not any(
        issue.code == "RELAXED_PACE_OVERLOAD" and issue_disposition(issue) == "blocking"
        for issue in result.issues
    )
    assert result.publishable is True
    assert result.review_required is True
    assert result.status in {"warning", "passed"}


def test_default_three_attractions_does_not_emit_relaxed_pace_overload() -> None:
    """Ordinary trips with 3 main attractions must not get RELAXED_PACE_OVERLOAD."""
    from app.services.trip_plan_quality_service import TripPlanQualityService

    request, plan = _aligned_request_plan(
        preferences=["历史文化", "自然风光"],
        free_text="",
    )
    result = TripPlanQualityService().evaluate(request, plan)
    assert all(issue.code != "RELAXED_PACE_OVERLOAD" for issue in result.issues)


def test_gentle_finalize_keeps_culture_venues_and_avoids_medical_claims() -> None:
    """Elder/parent pacing trims density without stripping culture or inventing medical claims."""
    planner = _empty_amap_planner()
    request = _request().model_copy(
        update={
            "origin_city": "北京",
            "preferences": ["历史文化"],
            "free_text_input": "父母同行，希望轻松一点",
        }
    )
    plan = _three_attraction_plan()
    result = planner._finalize_generated_content(request, plan)
    names = [item.name for item in result.days[0].attractions]
    categories = [item.category for item in result.days[0].attractions]

    assert len(names) == 2
    # Cap keeps prefix order; culture venues are not mass-deleted by elder keywords.
    assert "测试博物馆乙" in names or "博物馆" in categories
    blob = f"{result.overall_suggestions} {result.days[0].description}"
    for banned in ("无障碍", "医疗", "监护", "残疾", "病", "诊断", "健康保证"):
        assert banned not in blob
    # Hotels/meals are not attractions.
    assert all(item.category != "酒店" for item in result.days[0].attractions)
    assert len(result.days[0].meals) == 3


def test_gentle_cap_keeps_free_text_named_core_attraction_over_prefix_order() -> None:
    """User-named attraction in free_text survives density cap even if listed last."""
    planner = _empty_amap_planner()
    core_name = "测试科技馆丙"
    request = _request().model_copy(
        update={
            "origin_city": "北京",
            "preferences": ["亲子"],
            "free_text_input": f"想去{core_name}，节奏轻松一点",
        }
    )
    plan = _three_attraction_plan()
    # Core is third in list — naive [:2] would drop it.
    assert plan.days[0].attractions[2].name == core_name

    result = planner._finalize_generated_content(request, plan)
    names = [item.name for item in result.days[0].attractions]

    assert core_name in names
    assert len(names) == 2
    assert "降低每日主景点密度" in result.overall_suggestions
    # Dropped non-named filler, not the free_text-named core.
    assert "测试博物馆乙" not in names or "测试公园甲" not in names


def test_multiple_user_named_attractions_over_cap_are_kept_with_overflow_note() -> None:
    """More named attractions than cap: keep named ones and surface tradeoff note."""
    from app.services.trip_plan_quality_service import (
        TripPlanQualityService,
        issue_disposition,
    )

    planner = _empty_amap_planner()
    plan = _three_attraction_plan()
    named = [item.name for item in plan.days[0].attractions]
    request = _request().model_copy(
        update={
            "origin_city": "北京",
            "city": "北京",
            "start_date": "2030-01-01",
            "end_date": "2030-01-01",
            "travel_days": 1,
            "preferences": ["轻松出游"],
            "free_text_input": f"必去：{named[0]}、{named[1]}、{named[2]}，缓节奏",
        }
    )
    plan.start_date = "2030-01-01"
    plan.end_date = "2030-01-01"
    for attraction in plan.days[0].attractions:
        attraction.coordinate_source = "amap_poi"
        attraction.poi_id = attraction.poi_id or f"poi-{attraction.name}"

    result = planner._finalize_generated_content(request, plan)
    kept = [item.name for item in result.days[0].attractions]

    assert set(kept) == set(named)
    assert len(kept) == 3  # overflow allowed for named cores
    assert "优先保留点名景点" in result.overall_suggestions
    assert "取舍" in result.overall_suggestions
    # Day description must only mention retained names.
    for name in kept:
        assert name in (result.days[0].description or "")

    quality = TripPlanQualityService().evaluate(request, result)
    overload = [issue for issue in quality.issues if issue.code == "RELAXED_PACE_OVERLOAD"]
    assert overload
    assert all(issue_disposition(issue) == "advisory" for issue in overload)
    assert quality.publishable is True
    assert quality.review_required is True
    assert quality.status != "failed"


def test_pacing_markers_do_not_scan_attraction_descriptions() -> None:
    """POI description text must not trigger gentle pacing by itself."""
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    request = _request().model_copy(
        update={
            "origin_city": "北京",
            "preferences": ["历史文化"],
            "free_text_input": "",
        }
    )
    plan = _three_attraction_plan()
    plan.days[0].attractions[0].description = "适合亲子与老人休闲，少走路也不赶行程"
    assert planner._needs_gentle_pacing(request) is False


def test_named_attraction_matching_requires_positive_context_and_rejects_generics() -> None:
    """Rule-based named matching: short specific names yes; generics/negation no."""
    from app.models.schemas import Attraction, Location
    from app.services.trip_pacing_contract import is_user_named_attraction

    def att(name: str) -> Attraction:
        return Attraction(
            name=name,
            address="测试市测试区1号",
            location=Location(longitude=116.3, latitude=39.9),
            visit_duration=90,
            description="可执行地点",
            category="景点",
            poi_id=f"poi-{name}",
            coordinate_source="amap_poi",
        )

    cases = [
        ("想去西湖", "西湖", True),
        ("想去西湖", "西湖风景名胜区", True),  # colloquial short form vs grounded official name
        ("必去：甲乙文化馆", "甲乙文化馆", True),
        ("不想去西湖", "西湖", False),
        ("不想去西湖", "西湖风景名胜区", False),
        ("儿童医院附近找个公园", "公园", False),
        ("想安排博物馆类景点", "博物馆", False),
        ("想去博物馆", "陕西历史博物馆", False),  # generic token alone must not match
        ("想去人民公园", "人民公园", True),
    ]
    for free_text, name, expected in cases:
        request = _request().model_copy(update={"free_text_input": free_text})
        assert is_user_named_attraction(request, att(name)) is expected, (free_text, name)


def test_gentle_cap_keeps_colloquial_named_core_after_official_poi_rename() -> None:
    """free_text short form still prioritizes grounded official POI name."""
    planner = _empty_amap_planner()
    request = _request().model_copy(
        update={
            "origin_city": "北京",
            "preferences": ["亲子"],
            "free_text_input": "想去西湖，轻松一点",
        }
    )
    plan = _three_attraction_plan()
    # Simulate post-ground rename: model short name → official AMap name.
    plan.days[0].attractions[2].name = "西湖风景名胜区"
    plan.days[0].attractions[2].coordinate_source = "amap_poi"
    plan.days[0].attractions[2].poi_id = "poi-xihu-official"

    result = planner._finalize_generated_content(request, plan)
    names = [item.name for item in result.days[0].attractions]
    assert "西湖风景名胜区" in names
    assert len(names) == 2


def test_place_name_free_text_does_not_false_trigger_gentle_pacing() -> None:
    """Hospital/street/hotel place names must not alone trigger gentle pacing."""
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    false_triggers = (
        "儿童医院附近",
        "老人街附近",
        "亲子酒店附近",
        "父母路附近",
        "儿童公园怎么去",
        "老人街有什么景点",
        "推荐一些避暑景点",
    )
    for free_text in false_triggers:
        request = _request().model_copy(
            update={"preferences": ["历史文化"], "free_text_input": free_text}
        )
        assert planner._needs_gentle_pacing(request) is False, free_text


def test_summer_retreat_alone_is_not_gentle_but_combined_phrases_are() -> None:
    """Climate-only 避暑 is not gentle; combined parent/slow phrases still are."""
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    only_summer = _request().model_copy(
        update={"preferences": [], "free_text_input": "推荐一些避暑景点"}
    )
    summer_easy = _request().model_copy(
        update={"preferences": [], "free_text_input": "避暑，行程轻松一点"}
    )
    parents_summer = _request().model_copy(
        update={"preferences": [], "free_text_input": "带父母去避暑，不想太累"}
    )
    assert planner._needs_gentle_pacing(only_summer) is False
    assert planner._needs_gentle_pacing(summer_easy) is True
    assert planner._needs_gentle_pacing(parents_summer) is True

    plan = _three_attraction_plan()
    only_result = planner._finalize_generated_content(
        only_summer.model_copy(update={"origin_city": "北京"}), plan.model_copy(deep=True)
    )
    assert "降低每日主景点密度" not in only_result.overall_suggestions
    assert len(only_result.days[0].attractions) == 3


def test_trimmed_attraction_is_removed_from_day_description() -> None:
    """Dropped non-named attractions must not remain in rebuilt day description."""
    planner = _empty_amap_planner()
    request = _request().model_copy(
        update={
            "origin_city": "北京",
            "preferences": ["亲子"],
            "free_text_input": "想去测试科技馆丙，轻松一点",
        }
    )
    plan = _three_attraction_plan()
    result = planner._finalize_generated_content(request, plan)
    names = [item.name for item in result.days[0].attractions]
    description = result.days[0].description or ""
    assert "测试科技馆丙" in names
    for name in names:
        assert name in description
    for attraction in plan.days[0].attractions:
        if attraction.name not in names:
            assert attraction.name not in description


def test_intercity_arrival_day_caps_lighter_than_mid_trip_default() -> None:
    """Arrival day with intercity travel uses a lighter attraction cap."""
    planner = _empty_amap_planner()
    request = _request().model_copy(
        update={
            "origin_city": "上海",
            "city": "北京",
            "start_date": "2030-01-01",
            "end_date": "2030-01-03",
            "travel_days": 3,
            "preferences": ["历史文化"],
            "free_text_input": "",
        }
    )
    days = []
    for index in range(3):
        day_plan = _three_attraction_plan().days[0].model_copy(deep=True)
        day_plan.date = f"2030-01-0{index + 1}"
        day_plan.day_index = index
        days.append(day_plan)
    plan = TripPlan(
        city="北京",
        start_date="2030-01-01",
        end_date="2030-01-03",
        overall_suggestions="按计划出行。",
        days=days,
    )
    result = planner._finalize_generated_content(request, plan)
    # First/last intercity days: min(2, default cap); mid-day may keep 3 without gentle.
    assert len(result.days[0].attractions) <= 2
    assert len(result.days[1].attractions) == 3
    assert len(result.days[2].attractions) <= 2
    assert "抵达" in (result.days[0].description or "") or "入住" in (
        result.days[0].description or ""
    )
