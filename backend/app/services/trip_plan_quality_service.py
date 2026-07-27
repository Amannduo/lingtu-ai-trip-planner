"""Deterministic business quality gate for generated travel plans."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import math
import re
from typing import Iterable

from ..models.schemas import (
    TripPlan,
    TripPlanQualityIssue,
    TripPlanQualityResult,
    TripRequest,
)
from .destination_feasibility_service import (
    get_destination_feasibility_service,
    poi_destination_status,
)
from .trip_pacing_contract import prefers_gentle_pacing


# Structural hard blockers (shared with PR trust-hardening intent).
# Budget/hotel/transport gaps remain severity-driven: advisory when warning,
# blocking when severity=error (see issue_disposition). That preserves
# reviewable delivery for soft budget issues while keeping real errors hard.
BLOCKING_ISSUE_CODES = frozenset({
    "CITY_MISMATCH",
    "SHORT_TRIP_DESTINATION_UNREACHABLE",
    "PLAN_DATE_RANGE_MISMATCH",
    "INVALID_DATE_RANGE",
    "PAST_TRIP_DATE",
    "DAY_COUNT_MISMATCH",
    "DAY_DATE_MISMATCH",
    "EMPTY_DAY",
    "DAY_SCHEDULE_IMPOSSIBLE",
})


def issue_disposition(issue: TripPlanQualityIssue | str) -> str:
    """Classify an issue into 'blocking', 'advisory', or 'info'."""
    code = getattr(issue, "code", issue)
    severity = getattr(issue, "severity", "warning")
    if code in BLOCKING_ISSUE_CODES or str(severity).strip().lower() == "error":
        return "blocking"
    if str(severity).strip().lower() == "info":
        return "info"
    return "advisory"


class TripPlanQualityService:
    """Validate facts and cross-field constraints before a plan is persisted."""

    FORECAST_WINDOW_DAYS = 16
    # Issue codes that always block automatic persistence/delivery,
    # regardless of the numeric score.
    BLOCKING_CODES = frozenset({
        "CITY_MISMATCH",
        "SHORT_TRIP_DESTINATION_UNREACHABLE",
        "PLAN_DATE_RANGE_MISMATCH",
        "INVALID_DATE_RANGE",
        "PAST_TRIP_DATE",
        "DAY_COUNT_MISMATCH",
        "DAY_DATE_MISMATCH",
        "EMPTY_DAY",
        "DAY_SCHEDULE_IMPOSSIBLE",
        "BUDGET_MISSING",
        "HOTEL_GAP",
        "UNVERIFIED_HOTEL",
        "HOTEL_REFERENCE_MISMATCH",
        "HOTEL_PLAN_BUDGET_PRICE_MISMATCH",
        "TRANSPORT_MODE_MISMATCH",
        "TRANSPORT_REFERENCE_MISMATCH",
        "POI_DESTINATION_MISMATCH",
        "INVALID_COORDINATE",
    })
    ISSUE_PENALTIES = {
        "FALLBACK_PLAN": 30,
        "MODEL_OUTPUT_REPAIRED": 8,
        "PAST_TRIP_DATE": 35,
        "DAY_UNDERFILLED": 8,
        "DAY_SCHEDULE_OVERLOAD": 12,
        "DAY_SCHEDULE_IMPOSSIBLE": 35,
        "WEATHER_NOT_YET_AVAILABLE": 2,
        "HOTEL_REFERENCE_MISMATCH": 6,
        "HOTEL_PLAN_BUDGET_PRICE_MISMATCH": 6,
        "TRANSPORT_REFERENCE_MISMATCH": 10,
        "WEB_AUDIT_FORMAT_ONLY": 4,
        "CITY_MISMATCH": 35,
        "SHORT_TRIP_DESTINATION_UNREACHABLE": 35,
        "PLAN_DATE_RANGE_MISMATCH": 30,
        "INVALID_DATE_RANGE": 35,
        "DAY_COUNT_MISMATCH": 30,
        "DAY_DATE_MISMATCH": 25,
        "EMPTY_DAY": 25,
        "INVALID_COORDINATE": 20,
        "POI_DESTINATION_MISMATCH": 35,
        "POI_DESTINATION_UNVERIFIED": 6,
        "NON_TOURISM_POI": 15,
        "UNVERIFIED_POI": 8,
        "UNVERIFIED_MEAL": 8,
        "UNVERIFIED_ROUTE": 6,
        "HOTEL_GAP": 15,
        "UNVERIFIED_HOTEL": 12,
        "FACT_COVERAGE_INCOMPLETE": 4,
        "ATTRACTION_TYPE_CONCENTRATION": 14,
        "TOO_MANY_MUSEUMS": 12,
        "TOO_MANY_PARKS": 10,
        "HOTEL_TOO_FAR": 8,
        "WEATHER_GAP": 12,
        "BUDGET_MISSING": 12,
        "BUDGET_SUM_MISMATCH": 10,
        "BUDGET_NEGATIVE_COMPONENT": 25,
        "BUDGET_MEAL_MULTIPLIER": 12,
        "BUDGET_TICKET_MULTIPLIER": 8,
        "TICKET_PRICE_UNAVAILABLE": 4,
        "BUDGET_HOTEL_MULTIPLIER": 12,
        "BUDGET_HOTEL_NIGHTS_MISMATCH": 10,
        "BUDGET_HOTEL_ROOMS_MISMATCH": 8,
        "BUDGET_TRANSPORT_BREAKDOWN_MISMATCH": 10,
        "BUDGET_TRANSPORT_MULTIPLIER": 10,
        "BUDGET_MEALS_MISSING": 10,
        "BUDGET_IMPLAUSIBLY_LOW": 12,
        "HOTEL_PRICE_IMPLAUSIBLY_LOW": 12,
        "HOTEL_PRICE_UNVERIFIED": 4,
        "HOTEL_TYPE_MISMATCH": 12,
        "TRANSPORT_MODE_MISMATCH": 12,
        "TRANSPORT_MODE_UNVERIFIED": 8,
        "WEB_AUDIT_MISSING": 12,
        "WEB_AUDIT_NO_REFERENCES": 8,
        "WEB_AUDIT_FAILED": 25,
        "WEB_AUDIT_WARNING": 8,
        "SEMANTIC_ORIGIN_MISMATCH": 20,
        "SEMANTIC_DESTINATION_MISMATCH": 25,
        "SEMANTIC_TRAVELERS_MISMATCH": 12,
        "SEMANTIC_BUDGET_MISMATCH": 10,
        "SEMANTIC_PACE_MISMATCH": 10,
        "RELAXED_PACE_OVERLOAD": 10,
        "SEMANTIC_PARTY_UNCONFIRMED": 4,
        "SEMANTIC_CONTRACT_CONFLICT": 6,
        "SEMANTIC_PENDING_FIELDS": 4,
    }
    CHECKED_ITEMS = [
        "规划生成模式与降级状态",
        "行程日期连续性",
        "每日景点与餐饮完整性及来源",
        "POI坐标可信度与类型适用性",
        "景点类型多样性",
        "相邻景点路线可行性",
        "住宿位置合理性",
        "预算分项、人数倍率与合理区间",
        "天气日期覆盖",
        "联网审核风险",
        "出发地到目的地的短途可达性",
        "语义契约（出发地/人数/预算/节奏/冲突）",
    ]

    def evaluate(self, request: TripRequest, plan: TripPlan) -> TripPlanQualityResult:
        issues: list[TripPlanQualityIssue] = []
        verified_facts = 0
        fact_targets = 0

        def add(
            code: str,
            severity: str,
            path: str,
            message: str,
            suggestion: str = "",
        ) -> None:
            issues.append(
                TripPlanQualityIssue(
                    code=code,
                    severity=severity,
                    path=path,
                    message=message,
                    suggestion=suggestion,
                )
            )

        if plan.generation_mode == "map_fallback":
            add(
                "FALLBACK_PLAN",
                "warning",
                "generation_mode",
                "当前为地图校验备选方案，不是主规划模型的完整结果。",
                "建议重新生成主规划；如直接使用，请逐日确认景点取舍和交通节奏。",
            )
        elif plan.generation_mode == "repaired":
            add(
                "MODEL_OUTPUT_REPAIRED",
                "warning",
                "generation_mode",
                "主规划经过结构校正或地图可信补全后才进入事实校验。",
                "核心日期和服务端补全地点已复核，但建议再次确认个性化取舍是否符合预期。",
            )

        feasibility_svc = get_destination_feasibility_service()
        req_city_norm = feasibility_svc.normalize_city(request.city)
        plan_city_norm = feasibility_svc.normalize_city(plan.city)
        if req_city_norm and plan_city_norm and req_city_norm != plan_city_norm:
            add(
                "CITY_MISMATCH",
                "error",
                "city",
                f"生成目的地“{plan.city}”与用户选择的“{request.city}”不一致。",
                "重新按用户选择的目的地生成。",
            )

        feasibility = get_destination_feasibility_service().assess(
            request.origin_city,
            request.city,
            request.travel_days,
            explicit_destination=request.destination_source != "recommendation",
        )
        if feasibility.severity in {"warning", "error"}:
            add(
                (
                    "SHORT_TRIP_DESTINATION_UNREACHABLE"
                    if feasibility.severity == "error"
                    else "SHORT_TRIP_DESTINATION_RISK"
                ),
                feasibility.severity,
                "city",
                feasibility.reason,
                feasibility.transport_note,
            )

        if plan.start_date != request.start_date or plan.end_date != request.end_date:
            add(
                "PLAN_DATE_RANGE_MISMATCH",
                "error",
                "start_date",
                (
                    f"计划日期范围为{plan.start_date}至{plan.end_date}，"
                    f"用户确认的是{request.start_date}至{request.end_date}。"
                ),
                "按用户确认的起止日期重新生成。",
            )

        self._evaluate_semantic_contract(request, plan, add)

        expected_dates = self._date_range(request.start_date, request.end_date)
        forecast_dates = self._forecast_check_dates(expected_dates)
        past_dates = [
            value
            for value in expected_dates
            if date.fromisoformat(value) < date.today()
        ]
        if past_dates:
            add(
                "PAST_TRIP_DATE",
                "error",
                "start_date",
                "出行日期已经过去，不能作为待生成的新行程。",
                "请选择今天或之后的出行日期。",
            )
        if not expected_dates:
            add("INVALID_DATE_RANGE", "error", "start_date", "行程日期格式或范围无效。")
        else:
            if len(plan.days) != len(expected_dates):
                add(
                    "DAY_COUNT_MISMATCH",
                    "error",
                    "days",
                    f"应有{len(expected_dates)}天行程，实际生成{len(plan.days)}天。",
                    "补齐或删除不匹配的日期。",
                )
            if len(plan.days) != request.travel_days:
                add(
                    "DAY_COUNT_MISMATCH",
                    "error",
                    "days",
                    (
                        f"计划天数 {len(plan.days)} 与请求 travel_days="
                        f"{request.travel_days} 不一致。"
                    ),
                    "按请求天数重新生成，禁止静默增减行程日。",
                )
            # Two-day requests must not invent a formal Friday day in the plan.
            if request.travel_days == 2 and request.departure_mode != "evening_before":
                for day in plan.days:
                    desc = f"{day.description or ''} {day.date or ''}"
                    if re.search(r"周五完整|Day\s*0|第0天", desc, re.IGNORECASE):
                        add(
                            "DAY_COUNT_MISMATCH",
                            "error",
                            "days",
                            "两日行程中出现了额外的周五/Day0正式安排。",
                            "周五提前抵达仅为建议，不得生成第三天。",
                        )
                        break

        if "【系统防御】" in (plan.overall_suggestions or "") and "截断" in (
            plan.overall_suggestions or ""
        ):
            add(
                "DAY_COUNT_MISMATCH",
                "error",
                "days",
                "规划输出曾超过请求天数，系统做了防御性截断，结果可能不完整。",
                "请重新生成，确保模型直接输出正确天数，而不是依赖截断。",
            )
            for index, expected in enumerate(expected_dates):
                if index >= len(plan.days):
                    break
                day = plan.days[index]
                if day.date != expected:
                    add(
                        "DAY_DATE_MISMATCH",
                        "error",
                        f"days[{index}].date",
                        f"第{index + 1}天日期应为{expected}，实际为{day.date}。",
                        "按开始日期重新对齐每日日期。",
                    )

        seen_pois: set[str] = set()
        relaxed_pace = self._prefers_relaxed_pace(request)
        destination_service = get_destination_feasibility_service()
        cross_city = bool(
            request.origin_city
            and destination_service.normalize_city(request.origin_city)
            != destination_service.normalize_city(request.city)
        )
        weather_dates = {
            item.date[:10]
            for item in plan.weather_info
            if self._usable_weather(item)
        }
        for day_index, day in enumerate(plan.days):
            attractions = day.attractions or []
            is_edge_day = day_index in {0, max(0, len(plan.days) - 1)}
            minimum_attractions = (
                1 if relaxed_pace or is_edge_day else 2
            )
            # Edge days with cross-city travel have less available time.
            # Reserve 240 min for intercity, 120 for meals/rest → ~360 min
            # consumed before any sightseeing.  120 min of actual visiting
            # is reasonable for a travel day.
            if cross_city and is_edge_day:
                minimum_visit_minutes = 90
            elif relaxed_pace and is_edge_day:
                minimum_visit_minutes = 120
            elif relaxed_pace or is_edge_day:
                minimum_visit_minutes = 180
            else:
                minimum_visit_minutes = 210
            if not attractions:
                add(
                    "EMPTY_DAY",
                    "error",
                    f"days[{day_index}].attractions",
                    f"第{day_index + 1}天没有可执行景点。",
                    "至少安排一个经过地图校验的景点。",
                )
            elif relaxed_pace and len(attractions) > 2:
                # Explicit gentle/family/elder request: density breach is advisory
                # only — never force every trip to ≤2 attractions as blocking.
                add(
                    "RELAXED_PACE_OVERLOAD",
                    "warning",
                    f"days[{day_index}].attractions",
                    (
                        f"第{day_index + 1}天安排了{len(attractions)}个主景点，"
                        "与明确的缓节奏/亲子/老人同行偏好不一致。"
                    ),
                    "将当日主景点控制在2个以内，并预留休息与灵活调整时间。",
                )
            elif len(attractions) > 4:
                add(
                    "DAY_OVERLOADED",
                    "warning",
                    f"days[{day_index}].attractions",
                    f"第{day_index + 1}天安排了{len(attractions)}个景点，节奏可能过紧。",
                    "减少景点或增加旅行天数。",
                )

            total_visit_minutes = 0
            fact_targets += max(len(attractions), minimum_attractions)
            for attraction_index, attraction in enumerate(attractions):
                path = f"days[{day_index}].attractions[{attraction_index}]"
                total_visit_minutes += max(0, attraction.visit_duration)
                poi_key = attraction.poi_id or attraction.name
                if poi_key in seen_pois:
                    add(
                        "DUPLICATE_POI",
                        "warning",
                        path,
                        f"景点“{attraction.name}”在行程中重复出现。",
                        "保留一次并替换为同区域的其他景点。",
                    )
                seen_pois.add(poi_key)

                location = attraction.location
                if not self._valid_china_location(location.longitude, location.latitude):
                    add(
                        "INVALID_COORDINATE",
                        "error",
                        f"{path}.location",
                        f"景点“{attraction.name}”坐标不在可信范围内。",
                        "重新使用高德POI匹配。",
                    )
                elif (
                    attraction.coordinate_source == "amap_poi"
                    and bool((attraction.poi_id or "").strip())
                ):
                    verified_facts += 1
                    dest_status = self._attraction_matches_destination(
                        request, attraction,
                    )
                    if dest_status == "mismatched":
                        add(
                            "POI_DESTINATION_MISMATCH",
                            "error",
                            path,
                            (
                                f"景点「{attraction.name}」位于"
                                f"「{attraction.address or '未知'}」"
                                f"，与目的地「{request.city}」不一致。"
                            ),
                            "仅使用目的地城市的高德POI替换。",
                        )
                    elif dest_status == "unknown":
                        add(
                            "POI_DESTINATION_UNVERIFIED",
                            "warning",
                            path,
                            (
                                f"景点「{attraction.name}」的地址"
                                f"「{attraction.address or '无'}」未包含"
                                f"「{request.city}」的行政区信息，"
                                "无法确认是否属于目的地。"
                            ),
                            "出发前通过地图确认实际位置。",
                        )
                else:
                    add(
                        "UNVERIFIED_POI",
                        "warning",
                        path,
                        f"景点“{attraction.name}”未匹配到高德POI坐标。",
                        "出发前在地图中再次确认。",
                    )

            if attractions and total_visit_minutes < minimum_visit_minutes:
                add(
                    "DAY_UNDERFILLED",
                    "warning",
                    f"days[{day_index}]",
                    (
                        f"第{day_index + 1}天主要游览时长约"
                        f"{total_visit_minutes}分钟，低于当日节奏的"
                        f"可执行下限{minimum_visit_minutes}分钟。"
                    ),
                    "增加一个顺路主景点，或如实标注长时间游览、休息和自由活动。",
                )

            if total_visit_minutes > 600:
                add(
                    "VISIT_TIME_OVERLOAD",
                    "warning",
                    f"days[{day_index}]",
                    f"第{day_index + 1}天仅景点游览就需要约{round(total_visit_minutes / 60, 1)}小时。",
                    "减少一个景点或降低单点停留时间。",
                )

            expected_routes = max(0, len(attractions) - 1)
            fact_targets += expected_routes
            routes = day.routes or []
            if len(routes) < expected_routes:
                add(
                    "ROUTE_GAP",
                    "warning",
                    f"days[{day_index}].routes",
                    f"第{day_index + 1}天有{expected_routes - len(routes)}段景点衔接未取得可靠路线。",
                    "打开地图后再次确认交通方式和耗时。",
                )
            for route_index, route in enumerate(routes):
                route_path = f"days[{day_index}].routes[{route_index}]"
                endpoint_matches = (
                    route_index < expected_routes
                    and route.from_name == attractions[route_index].name
                    and route.to_name == attractions[route_index + 1].name
                )
                if not endpoint_matches:
                    add(
                        "ROUTE_ENDPOINT_MISMATCH",
                        "warning",
                        route_path,
                        "路线端点与当前景点游览顺序不一致。",
                        "按相邻景点顺序重新规划路线。",
                    )
                elif (
                    route.verified
                    and route.source == "amap_route"
                    and len(route.path or []) >= 2
                ):
                    verified_facts += 1
                else:
                    add(
                        "UNVERIFIED_ROUTE",
                        "warning",
                        route_path,
                        f"“{route.from_name} → {route.to_name}”只有路线摘要，尚未取得完整折线。",
                        "导航时以高德实时路线为准。",
                    )
                if route.duration > 10800:
                    add(
                        "ROUTE_TOO_LONG",
                        "warning",
                        route_path,
                        f"“{route.from_name} → {route.to_name}”预计耗时超过3小时。",
                        "调整景点分组或更换交通方式。",
                    )

            route_minutes = sum(
                max(0, int(route.duration or 0)) / 60
                for route in routes[:expected_routes]
            )
            intercity_reserve = 240 if cross_city and is_edge_day else 0
            meal_and_rest_minutes = 150 if relaxed_pace else 120
            scheduled_minutes = (
                total_visit_minutes
                + route_minutes
                + intercity_reserve
                + meal_and_rest_minutes
            )
            overload_limit = 480 if relaxed_pace else 600
            impossible_limit = 660 if relaxed_pace else 840
            if scheduled_minutes > impossible_limit:
                add(
                    "DAY_SCHEDULE_IMPOSSIBLE",
                    "error",
                    f"days[{day_index}]",
                    (
                        f"第{day_index + 1}天景点、路线、用餐休息"
                        f"和城际预留合计约{round(scheduled_minutes / 60, 1)}"
                        "小时，已无法在正常作息内执行。"
                    ),
                    "删减景点、缩短路线或拆分到其他日期。",
                )
            elif scheduled_minutes > overload_limit:
                add(
                    "DAY_SCHEDULE_OVERLOAD",
                    "warning",
                    f"days[{day_index}]",
                    (
                        f"第{day_index + 1}天预计活动约"
                        f"{round(scheduled_minutes / 60, 1)}小时，"
                        "对当前出行节奏偏紧。"
                    ),
                    "减少折返，并为用餐、卫生间和临时休息留出缓冲。",
                )

            meals = day.meals or []
            required_meal_types = {"breakfast", "lunch", "dinner"}
            fact_targets += len(required_meal_types)
            meals_by_type = {
                meal.type: meal
                for meal in meals
                if meal.type in required_meal_types
            }
            missing_meals = required_meal_types - set(meals_by_type)
            if missing_meals:
                add(
                    "MEAL_GAP",
                    "warning",
                    f"days[{day_index}].meals",
                    f"第{day_index + 1}天缺少{self._meal_names(missing_meals)}安排。",
                    "补充附近餐饮或明确留作自由选择。",
                )

            unverified_meals: list[str] = []
            for meal_type in sorted(required_meal_types):
                meal = meals_by_type.get(meal_type)
                if meal is None:
                    continue
                if (
                    meal.address
                    and meal.location is not None
                    and meal.poi_id
                    and meal.coordinate_source == "amap_poi"
                    and self._valid_china_location(
                        meal.location.longitude, meal.location.latitude
                    )
                ):
                    verified_facts += 1
                else:
                    unverified_meals.append(meal.name)
            if unverified_meals:
                add(
                    "UNVERIFIED_MEAL",
                    "warning",
                    f"days[{day_index}].meals",
                    "具体餐厅尚未通过地图POI校验：" + "、".join(unverified_meals[:3]),
                    "重新按当天景点周边检索餐饮，并核对营业时间。",
                )

        all_attractions = [
            attraction
            for day in plan.days
            for attraction in (day.attractions or [])
        ]
        if all_attractions and not any(
            int(attraction.ticket_price or 0) > 0
            for attraction in all_attractions
        ):
            add(
                "TICKET_PRICE_UNAVAILABLE",
                "warning",
                "days.attractions.ticket_price",
                "全部景点暂按0元计入预算，但当前没有取得可核验票价；这不代表景点已确认免费。",
                "出发前通过景区官方渠道核对免费政策、预约要求和实际票价。",
            )
        weak_attractions = [
            attraction.name
            for attraction in all_attractions
            if self._looks_like_non_tourism_poi(attraction.name, attraction.category or "")
        ]
        if weak_attractions:
            preview = "、".join(weak_attractions[:3])
            add(
                "NON_TOURISM_POI",
                "warning",
                "days",
                f"行程中包含疑似住宅、门店或汽车服务类地点：{preview}。",
                "从景点候选中移除弱旅游POI，并使用文博、自然或正式景区替换。",
            )

        if len(all_attractions) >= 4:
            category_counts: dict[str, int] = {}
            for attraction in all_attractions:
                category = self._attraction_category(
                    attraction.name, attraction.category or ""
                )
                category_counts[category] = category_counts.get(category, 0) + 1
            dominant_category, dominant_count = max(
                category_counts.items(), key=lambda item: item[1]
            )
            dominant_ratio = dominant_count / len(all_attractions)
            street_ratio = category_counts.get("street", 0) / len(all_attractions)
            if dominant_ratio >= 0.65 or street_ratio > 0.40:
                labels = {
                    "street": "商业街区",
                    "culture": "历史文化",
                    "nature": "自然公园",
                    "leisure": "休闲娱乐",
                    "other": "同类或未明确分类地点",
                }
                dominant_label = labels.get(dominant_category, dominant_category)
                # When the user explicitly prefers the dominant category,
                # concentration is intentional — don't penalise.
                user_prefers_dominant = any(
                    marker in " ".join(request.preferences or [])
                    for marker in self._category_preference_markers(
                        dominant_category
                    )
                )
                if not user_prefers_dominant:
                    add(
                        "ATTRACTION_TYPE_CONCENTRATION",
                        "warning",
                        "days",
                        (
                            f"景点类型过于集中：{dominant_label}"
                            f"占{round(dominant_ratio * 100)}%。"
                        ),
                        "增加文博、自然、公园或地标类景点，避免连续多天重复相同体验。",
                    )

        user_prefs = [p.casefold() for p in (request.preferences or [])]
        free_text = (request.free_text_input or "").casefold()

        has_museum_pref = any(
            kw in p or kw in free_text
            for p in user_prefs + [free_text]
            for kw in ("历史", "文化", "博物馆", "研学", "展览", "艺术")
        )
        has_park_pref = any(
            kw in p or kw in free_text
            for p in user_prefs + [free_text]
            for kw in ("自然", "风光", "公园", "绿道", "徒步", "户外", "休闲")
        )

        museum_count = sum(
            any(
                marker in f"{attraction.name} {attraction.category or ''}"
                for marker in (
                    "博物馆", "美术馆", "艺术馆", "纪念馆", "科技馆", "展览馆"
                )
            )
            for attraction in all_attractions
        )
        museum_limit = 6 if has_museum_pref else 3
        if museum_count > museum_limit:
            add(
                "TOO_MANY_MUSEUMS",
                "warning",
                "days",
                f"行程安排了{museum_count}个博物馆或展馆，体验可能重复。",
                "在不同日期错开参观，或适当补充特色历史街区和自然景观。",
            )

        park_count = sum(
            any(
                marker in f"{attraction.name} {attraction.category or ''}"
                for marker in ("公园", "绿道", "湿地", "植物园")
            )
            for attraction in all_attractions
        )
        park_limit = 7 if has_park_pref else 4
        if park_count > park_limit:
            add(
                "TOO_MANY_PARKS",
                "warning",
                "days",
                f"行程安排了{park_count}个公园或绿道，连续体验可能相似。",
                "保留差异明显的自然休闲点，并补充文化或城市地标。",
            )

        attraction_locations = [
            attraction.location
            for attraction in all_attractions
            if self._valid_china_location(
                attraction.location.longitude, attraction.location.latitude
            )
        ]
        expected_hotel_nights = self._expected_hotel_nights(request)
        hotel_required = self._requires_hotel(request, expected_hotel_nights)
        if hotel_required:
            fact_targets += expected_hotel_nights
            overnight_indexes = list(
                range(min(expected_hotel_nights, len(plan.days)))
            )
            missing_hotel_days = [
                index
                for index in overnight_indexes
                if plan.days[index].hotel is None
            ]
            if len(overnight_indexes) < expected_hotel_nights:
                missing_hotel_days.extend(
                    range(len(overnight_indexes), expected_hotel_nights)
                )
            if missing_hotel_days:
                labels = "、".join(f"第{index + 1}天" for index in missing_hotel_days[:5])
                add(
                    "HOTEL_GAP",
                    "warning",
                    "days.hotel",
                    f"跨夜行程缺少可信住宿安排：{labels}。",
                    "为每个住宿夜晚补充经过地图POI校验的酒店；若不需要住宿，请明确选择当天往返或露营。",
                )

            overnight_hotel_entries = [
                (index, plan.days[index].hotel)
                for index in overnight_indexes
                if plan.days[index].hotel is not None
            ]
            overnight_hotels = [
                hotel for _, hotel in overnight_hotel_entries
            ]
            untrusted_hotel_entries = [
                (index, hotel)
                for index, hotel in overnight_hotel_entries
                if not self._trusted_hotel(hotel)
            ]
            verified_facts += (
                len(overnight_hotel_entries) - len(untrusted_hotel_entries)
            )
            if (
                not self._accepts_hostel(request.accommodation)
                and any(
                    marker in hotel.name
                    for hotel in overnight_hotels
                    for marker in (
                        "青年旅舍",
                        "青年旅社",
                        "青旅",
                        "床位",
                        "钟点房",
                        "小时房",
                        "日租房",
                    )
                )
            ):
                add(
                    "HOTEL_TYPE_MISMATCH",
                    "warning",
                    "days.hotel",
                    "住宿推荐与用户选择的酒店档次不符，疑似床位或短租房。",
                    "重新筛选与住宿偏好匹配的整间客房。",
                )
            if untrusted_hotel_entries:
                names = "、".join(
                    f"第{index + 1}天{hotel.name}"
                    for index, hotel in untrusted_hotel_entries[:5]
                )
                add(
                    "UNVERIFIED_HOTEL",
                    "warning",
                    "days.hotel",
                    f"部分住宿夜尚未通过地图POI校验：{names}。",
                    "为每个住宿夜保留服务端POI编号和有效坐标，不能用一晚的验证覆盖全程。",
                )


        seen_hotels: set[str] = set()
        for day_index, day in enumerate(plan.days):
            hotel = day.hotel
            if hotel is None or hotel.location is None or not attraction_locations:
                continue
            hotel_key = hotel.poi_id or hotel.name
            if hotel_key in seen_hotels:
                continue
            seen_hotels.add(hotel_key)
            if not self._valid_china_location(
                hotel.location.longitude, hotel.location.latitude
            ):
                add(
                    "UNVERIFIED_HOTEL",
                    "warning",
                    f"days[{day_index}].hotel",
                    f"住宿“{hotel.name}”的坐标不可信。",
                    "重新使用地图酒店POI校准。",
                )
                continue
            average_distance = sum(
                self._distance_km(hotel.location, location)
                for location in attraction_locations
            ) / len(attraction_locations)
            if average_distance > 8:
                add(
                    "HOTEL_TOO_FAR",
                    "warning",
                    f"days[{day_index}].hotel",
                    (
                        f"住宿“{hotel.name}”距全部行程景点平均约"
                        f"{average_distance:.1f}公里，往返成本偏高。"
                    ),
                    "优先选择核心景点几何中心附近或地铁换乘更便利的住宿。",
                )

        not_yet_available_weather = [
            value
            for value in expected_dates
            if value not in forecast_dates
            and date.fromisoformat(value) >= date.today()
        ]
        if not_yet_available_weather:
            add(
                "WEATHER_NOT_YET_AVAILABLE",
                "warning",
                "weather_info",
                (
                    "部分日期尚未进入16天逐日预报窗口："
                    + "、".join(not_yet_available_weather[:7])
                ),
                "这不是天气服务失败，但出发就绪度不得记为满分；请在出发前3至7天刷新。",
            )

        fact_targets += len(forecast_dates)
        if forecast_dates:
            missing_weather = [
                value for value in forecast_dates if value not in weather_dates
            ]
            if missing_weather:
                add(
                    "WEATHER_GAP",
                    "warning",
                    "weather_info",
                    "当前可预报窗口内仍缺少有效逐日天气：" + "、".join(missing_weather),
                    "重新获取天气；如仍缺失，请在出发前再次刷新并准备室内替代方案。",
                )
        verified_facts += len(weather_dates.intersection(forecast_dates))

        if plan.budget is None:
            add(
                "BUDGET_MISSING",
                "warning",
                "budget",
                "行程缺少结构化预算，无法验证住宿、交通和人数倍率。",
                "在保存或发送前重新计算预算分项与总计。",
            )
        else:
            budget = plan.budget
            component_values = {
                "total_attractions": budget.total_attractions,
                "total_hotels": budget.total_hotels,
                "total_meals": budget.total_meals,
                "total_transportation": budget.total_transportation,
                "total": budget.total,
                "hotel_nights": budget.hotel_nights,
                "hotel_rooms": budget.hotel_rooms,
                "hotel_unit_price": budget.hotel_unit_price,
                "intercity_transportation": budget.intercity_transportation,
                "local_transportation": budget.local_transportation,
                "transport_unit_price": budget.transport_unit_price,
            }
            negative_fields = [
                name for name, value in component_values.items() if value < 0
            ]
            if negative_fields:
                add(
                    "BUDGET_NEGATIVE_COMPONENT",
                    "error",
                    "budget",
                    "预算包含负数分项：" + "、".join(negative_fields),
                    "拒绝该预算并由服务端重新计算全部分项。",
                )

            calculated = (
                budget.total_attractions
                + budget.total_hotels
                + budget.total_meals
                + budget.total_transportation
            )
            if calculated != budget.total:
                add(
                    "BUDGET_SUM_MISMATCH",
                    "warning",
                    "budget.total",
                    f"预算分项合计¥{calculated}与总计¥{budget.total}不一致。",
                    "重新计算预算汇总。",
                )
            if (
                request.budget is not None
                and budget.total > request.budget * 1.1
            ):
                add(
                    "BUDGET_EXCEEDED",
                    "warning",
                    "budget.total",
                    f"预计总费用¥{budget.total}超过用户预算¥{request.budget}。",
                    "优先降低酒店、城际交通或高价门票支出。",
                )

            if hotel_required:
                expected_rooms = max(1, math.ceil(request.travelers / 2))
                if budget.hotel_nights != expected_hotel_nights:
                    add(
                        "BUDGET_HOTEL_NIGHTS_MISMATCH",
                        "warning",
                        "budget.hotel_nights",
                        (
                            f"预算按{budget.hotel_nights}晚计算住宿，"
                            f"但日期范围需要{expected_hotel_nights}晚。"
                        ),
                        "按起止日期重新计算住宿晚数。",
                    )
                if budget.hotel_rooms != expected_rooms:
                    add(
                        "BUDGET_HOTEL_ROOMS_MISMATCH",
                        "warning",
                        "budget.hotel_rooms",
                        (
                            f"预算按{budget.hotel_rooms}间房计算，"
                            f"{request.travelers}人保守需要{expected_rooms}间。"
                        ),
                        "按每间最多2人重新计算房间数；特殊房型请明确说明。",
                    )

                hotel_floor = self._hotel_unit_price_floor(request.accommodation)
                if budget.hotel_unit_price < hotel_floor:
                    add(
                        "HOTEL_PRICE_IMPLAUSIBLY_LOW",
                        "warning",
                        "budget.hotel_unit_price",
                        (
                            f"住宿单晚仅¥{budget.hotel_unit_price}，低于"
                            f"{request.accommodation}的保守校验下限¥{hotel_floor}。"
                        ),
                        "核对是否误选青年旅舍床位价、钟点房或脱敏价格。",
                    )

                expected_hotel_total = (
                    budget.hotel_unit_price
                    * expected_hotel_nights
                    * expected_rooms
                )
                if budget.total_hotels != expected_hotel_total:
                    add(
                        "BUDGET_HOTEL_MULTIPLIER",
                        "warning",
                        "budget.total_hotels",
                        (
                            f"住宿分项¥{budget.total_hotels}与单晚价×晚数×房间数"
                            f"的结果¥{expected_hotel_total}不一致。"
                        ),
                        "按单晚价格、住宿晚数和房间数重新计算。",
                    )

                hotel_reference = budget.hotel_reference or ""
                if (
                    "酒店兜底估算" in (budget.budget_source or "")
                    or "参考单晚" in hotel_reference
                ):
                    add(
                        "HOTEL_PRICE_UNVERIFIED",
                        "warning",
                        "budget.hotel_reference",
                        "住宿价格来自档次兜底估算，不是可核验的实时酒店报价。",
                        "预订前通过酒店官方渠道或可信平台复核实际房价。",
                    )
                if (
                    not self._accepts_hostel(request.accommodation)
                    and any(
                        marker in hotel_reference
                        for marker in (
                            "青年旅舍", "青年旅社", "青旅", "床位",
                            "钟点房", "小时房", "日租房",
                        )
                    )
                ):
                    add(
                        "HOTEL_TYPE_MISMATCH",
                        "warning",
                        "budget.hotel_reference",
                        "住宿报价与用户选择的酒店档次不符，疑似床位价或短租房。",
                        "重新筛选与住宿偏好匹配的整间客房。",
                    )

            planned_hotels = [
                plan.days[index].hotel
                for index in range(
                    min(expected_hotel_nights, len(plan.days))
                )
                if plan.days[index].hotel is not None
            ]
            planned_hotel_names = {
                self._normalized_label(hotel.name)
                for hotel in planned_hotels
                if hotel.name
            }
            generic_hotel_reference = (
                "酒店兜底估算" in (budget.budget_source or "")
                or "参考单晚" in (budget.hotel_reference or "")
            )
            if (
                hotel_required
                and planned_hotel_names
                and budget.hotel_reference
                and not generic_hotel_reference
                and not all(
                    name and name in self._normalized_label(budget.hotel_reference)
                    for name in planned_hotel_names
                )
            ):
                add(
                    "HOTEL_REFERENCE_MISMATCH",
                    "warning",
                    "budget.hotel_reference",
                    "住宿报价引用的酒店与行程中实际选定的酒店不一致。",
                    "仅使用实际行程酒店的可核验报价；无法获取时标记为档次估算。",
                )
            planned_hotel_prices = [
                int(hotel.estimated_cost or 0)
                for hotel in planned_hotels
            ]
            if (
                hotel_required
                and len(planned_hotel_prices) == expected_hotel_nights
                and all(price > 0 for price in planned_hotel_prices)
                and budget.hotel_unit_price > 0
            ):
                selected_price = max(
                    1,
                    round(sum(planned_hotel_prices) / len(planned_hotel_prices)),
                )
                if budget.hotel_unit_price != selected_price:
                    add(
                        "HOTEL_PLAN_BUDGET_PRICE_MISMATCH",
                        "warning",
                        "budget.hotel_unit_price",
                        (
                            f"行程酒店逐晚参考均价为¥{selected_price}，"
                            f"但预算按¥{budget.hotel_unit_price}计算。"
                        ),
                        "按每个住宿夜实际选定酒店的单晚价加权计算。",
                    )

            transport_breakdown = (
                budget.intercity_transportation + budget.local_transportation
            )
            if budget.total_transportation != transport_breakdown:
                add(
                    "BUDGET_TRANSPORT_BREAKDOWN_MISMATCH",
                    "warning",
                    "budget.total_transportation",
                    (
                        f"交通总计¥{budget.total_transportation}与城际加市内交通"
                        f"¥{transport_breakdown}不一致。"
                    ),
                    "重新汇总城际交通与市内交通。",
                )

            transport_reference = budget.transport_reference or ""
            feasibility = get_destination_feasibility_service()
            different_city = bool(
                request.origin_city
                and feasibility.normalize_city(request.origin_city)
                != feasibility.normalize_city(request.city)
            )
            self_drive_or_local = any(
                kw in (request.intercity_transportation or "")
                for kw in ("自驾", "步行", "公共交通", "城市漫步", "无")
            ) or request.travel_days <= 1
            fallback_transport = (
                "城际交通兜底估算" in (budget.budget_source or "")
                or "兜底估算" in transport_reference
                or "非实时班次" in transport_reference
            )
            if (
                different_city
                and not self_drive_or_local
                and transport_reference
                and not fallback_transport
                and not self._transport_reference_matches(
                    request,
                    transport_reference,
                )
            ):
                add(
                    "TRANSPORT_REFERENCE_MISMATCH",
                    "warning",
                    "budget.transport_reference",
                    "城际交通引用未同时匹配出发日期、返程日期和往返城市方向。",
                    "只能保留与本次请求日期、站点和方向一致的车次或航班。",
                )

            if different_city and fallback_transport:
                add(
                    "TRANSPORT_MODE_UNVERIFIED",
                    "warning",
                    "budget.transport_reference",
                    "城际交通价格来自兜底估算，尚未取得可核验的实时班次。",
                    "在官方交通渠道确认往返班次和票价后再锁定预算。",
                )
            elif self._requested_high_speed(request.intercity_transportation or ""):
                train_prefixes = re.findall(
                    r"(?<![A-Z0-9])([A-Z])\s*\d{1,5}",
                    transport_reference.upper(),
                )
                slow_train_words = ("普快", "普速", "快速", "特快", "直达")
                if (
                    any(prefix in {"K", "T", "Z", "L", "Y", "S"} for prefix in train_prefixes)
                    or any(word in transport_reference for word in slow_train_words)
                ):
                    add(
                        "TRANSPORT_MODE_MISMATCH",
                        "warning",
                        "budget.transport_reference",
                        "用户选择高铁/动车，但预算引用了普速列车。",
                        "重新筛选G、D或C字头车次并计算往返费用。",
                    )
                elif sum(
                    prefix in {"G", "D", "C"} for prefix in train_prefixes
                ) < 2:
                    add(
                        "TRANSPORT_MODE_UNVERIFIED",
                        "warning",
                        "budget.transport_reference",
                        "高铁/动车预算没有可核验的G、D或C字头往返车次。",
                        "取得实际往返车次和票价后再确认预算。",
                    )

            expected_meals = sum(
                max(0, int(meal.estimated_cost or 0))
                for day in plan.days
                for meal in (day.meals or [])
            ) * request.travelers
            if budget.total_meals <= 0:
                add(
                    "BUDGET_MEALS_MISSING",
                    "warning",
                    "budget.total_meals",
                    "多日旅行的餐饮预算为0，预算明显不完整。",
                    "按每日餐饮单人参考价乘以出行人数计算。",
                )
            elif (
                expected_meals > 0
                and budget.total_meals != expected_meals
            ):
                add(
                    "BUDGET_MEAL_MULTIPLIER",
                    "warning",
                    "budget.total_meals",
                    (
                        f"餐饮分项¥{budget.total_meals}未正确反映"
                        f"{request.travelers}人的单人参考餐费¥{expected_meals}。"
                    ),
                    "按每日三餐单人参考价乘以出行人数重新计算。",
                )

            expected_tickets = sum(
                max(0, int(attraction.ticket_price or 0))
                for attraction in all_attractions
            ) * request.travelers
            if (
                expected_tickets > 0
                and budget.total_attractions != expected_tickets
            ):
                add(
                    "BUDGET_TICKET_MULTIPLIER",
                    "warning",
                    "budget.total_attractions",
                    (
                        f"门票分项¥{budget.total_attractions}未正确反映"
                        f"{request.travelers}人的单人票价合计¥{expected_tickets}。"
                    ),
                    "按单人门票价格乘以出行人数重新计算。",
                )

            if different_city and "自驾" not in (request.intercity_transportation or ""):
                expected_intercity = budget.transport_unit_price * request.travelers
                if (
                    budget.intercity_transportation <= 0
                    or budget.intercity_transportation != expected_intercity
                ):
                    add(
                        "BUDGET_TRANSPORT_MULTIPLIER",
                        "warning",
                        "budget.intercity_transportation",
                        (
                            f"城际交通分项¥{budget.intercity_transportation}未正确反映"
                            f"{request.travelers}人的单人往返价¥{expected_intercity}。"
                        ),
                        "按单人往返价格乘以出行人数重新计算。",
                    )

            minimum_reasonable = self._minimum_reasonable_budget(request)
            if budget.total < int(minimum_reasonable * 0.85):
                add(
                    "BUDGET_IMPLAUSIBLY_LOW",
                    "warning",
                    "budget.total",
                    (
                        f"当前总预算估算¥{budget.total}明显低于"
                        f"{request.travelers}人{request.travel_days}天的保守下限"
                        f"¥{minimum_reasonable}。"
                    ),
                    "复核住宿晚数、往返交通、餐饮人数倍率和市内交通。",
                )

        if plan.agent_audit is None:
            add(
                "WEB_AUDIT_MISSING",
                "warning",
                "agent_audit",
                "行程缺少联网审核结果，动态信息尚未复核。",
                "完成联网审核或明确标记本地降级后再确认方案。",
            )
        else:
            if plan.agent_audit.status == "failed":
                add(
                    "WEB_AUDIT_FAILED",
                    "warning",
                    "agent_audit",
                    "动态数据未实时联网复核。",
                    "查看下方“审核检查”中的具体问题，出发前人工确认二次信息。",
                )
            elif plan.agent_audit.status != "passed" or plan.agent_audit.issues:
                issue_count = max(1, len(plan.agent_audit.issues))
                add(
                    "WEB_AUDIT_WARNING",
                    "warning",
                    "agent_audit",
                    f"联网审核有{issue_count}项内容需要复核。",
                    "具体原因与处理建议见下方“审核检查”，此处不重复展开。",
                )
            if (
                plan.agent_audit.status == "passed"
                and not plan.web_references
            ):
                add(
                    "WEB_AUDIT_NO_REFERENCES",
                    "warning",
                    "web_references",
                    "联网审核标记为通过，但没有可核验引用链接。",
                    "保留实际搜索引用；否则将审核状态降级为需要复核。",
                )

        if (
            plan.agent_audit is not None
            and plan.agent_audit.status == "passed"
            and plan.agent_audit.audit_level != "semantic_verified"
        ):
            add(
                "WEB_AUDIT_FORMAT_ONLY",
                "warning",
                "agent_audit.audit_level",
                "当前联网审核只验证输出结构、引用编号和链接一致性，不等于景点、酒店、车次和政策已完成语义事实验证。",
                "保留“格式审核”标识；只有服务端逐项语义核对后才能标记为事实已验证。",
            )

        coverage = (verified_facts / fact_targets) if fact_targets else 0.0
        if fact_targets and verified_facts < fact_targets:
            add(
                "FACT_COVERAGE_INCOMPLETE",
                "warning",
                "quality.verified_facts",
                (
                    f"地图、路线、餐饮、住宿和天气事实仅验证"
                    f"{verified_facts}/{fact_targets}项。"
                ),
                "补齐未验证事实后再将方案评为满分。",
            )

        error_count = sum(item.severity == "error" for item in issues)
        warning_count = sum(item.severity == "warning" for item in issues)
        score = max(
            0,
            100 - sum(self._issue_penalty(issue) for issue in issues),
        )
        constraint_score = self._dimension_score(
            issues,
            {
                "CITY_MISMATCH",
                "SHORT_TRIP_DESTINATION_UNREACHABLE",
                "PLAN_DATE_RANGE_MISMATCH",
                "INVALID_DATE_RANGE",
                "PAST_TRIP_DATE",
                "DAY_COUNT_MISMATCH",
                "DAY_DATE_MISMATCH",
                "EMPTY_DAY",
                "INVALID_COORDINATE",
                "POI_DESTINATION_MISMATCH",
                "BUDGET_NEGATIVE_COMPONENT",
                "BUDGET_SUM_MISMATCH",
            },
        )
        executability_score = self._dimension_score(
            issues,
            {
                "DAY_UNDERFILLED",
                "DAY_OVERLOADED",
                "RELAXED_PACE_OVERLOAD",
                "DAY_SCHEDULE_OVERLOAD",
                "DAY_SCHEDULE_IMPOSSIBLE",
                "VISIT_TIME_OVERLOAD",
                "ROUTE_GAP",
                "ROUTE_ENDPOINT_MISMATCH",
                "ROUTE_TOO_LONG",
                "HOTEL_GAP",
                "UNVERIFIED_HOTEL",
                "HOTEL_TOO_FAR",
                "ATTRACTION_TYPE_CONCENTRATION",
                "TOO_MANY_MUSEUMS",
                "TOO_MANY_PARKS",
            },
        )
        evidence_score = round(coverage * 100) if fact_targets else 0
        readiness_score = self._dimension_score(
            issues,
            {
                "WEATHER_GAP",
                "WEATHER_NOT_YET_AVAILABLE",
                "BUDGET_MISSING",
                "TICKET_PRICE_UNAVAILABLE",
                "HOTEL_PRICE_UNVERIFIED",
                "HOTEL_REFERENCE_MISMATCH",
                "HOTEL_PLAN_BUDGET_PRICE_MISMATCH",
                "TRANSPORT_MODE_UNVERIFIED",
                "TRANSPORT_MODE_MISMATCH",
                "TRANSPORT_REFERENCE_MISMATCH",
                "WEB_AUDIT_MISSING",
                "WEB_AUDIT_NO_REFERENCES",
                "WEB_AUDIT_FAILED",
                "WEB_AUDIT_WARNING",
                "WEB_AUDIT_FORMAT_ONLY",
            },
        )
        weighted_score = round(
            constraint_score * 0.25
            + executability_score * 0.30
            + evidence_score * 0.25
            + readiness_score * 0.20
        )
        score = min(score, weighted_score)
        if error_count:
            score = min(score, 59)
        if fact_targets and coverage < 1:
            score = min(score, 96)
        if fact_targets and coverage < 0.75:
            score = min(score, 85)
        if fact_targets and coverage < 0.5:
            score = min(score, 70)
        if plan.generation_mode == "map_fallback":
            score = min(score, 70)
        elif plan.generation_mode == "repaired":
            score = min(score, 92)
        has_blocking = (
            len(plan.days) == 0
            or any(issue_disposition(issue) == "blocking" for issue in issues)
        )
        has_advisory = any(
            issue_disposition(issue) == "advisory" for issue in issues
        )
        if has_blocking:
            publishable = False
            review_required = True
            status = "failed"
        elif (
            has_advisory
            or score < 100
            or plan.generation_mode in {"repaired", "map_fallback"}
        ):
            # Info-only issues (e.g. SEMANTIC_PENDING_FIELDS) do not force review.
            publishable = True
            review_required = True
            status = "warning"
        else:
            publishable = True
            review_required = False
            status = "passed"

        return TripPlanQualityResult(
            status=status,
            score=score,
            constraint_score=constraint_score,
            executability_score=executability_score,
            evidence_score=evidence_score,
            readiness_score=readiness_score,
            publishable=publishable,
            review_required=review_required,
            checked_items=list(self.CHECKED_ITEMS),
            issues=issues,
            verified_facts=verified_facts,
            generated_at=datetime.now().isoformat(timespec="seconds"),
        )
        # Unified gate triple (publishable + quality_status): single source.
        refresh_quality_gate(result, generation_mode=plan.generation_mode)
        return result

    def _normalized_label(self, value: str) -> str:
        return re.sub(r"[\W_]+", "", value or "").casefold()

    def _transport_reference_matches(
        self,
        request: TripRequest,
        reference: str,
    ) -> bool:
        feasibility = get_destination_feasibility_service()

        def location_aliases(value: str) -> set[str]:
            """Return a set of normalized forms for a location name.

            Includes the raw normalized label, the feasibility-service
            city normalization, and the shared location-name normalization
            that strips province prefixes and station suffixes.  This
            allows ``山西太原`` to match ``太原站`` and ``石家庄`` to
            match ``石家庄站``.
            """
            candidates: set[str] = {
                self._normalized_label(value),
                self._normalized_label(feasibility.normalize_city(value)),
                self._normalized_label(
                    feasibility.normalize_location_for_matching(value)
                ),
            }
            candidates.discard("")
            return candidates

        origin_aliases = location_aliases(request.origin_city or "")
        destination_aliases = location_aliases(request.city or "")
        legs = [
            re.sub(r"\s+", "", item)
            for item in re.split(r"[;；]", reference or "")
            if item.strip()
        ]
        if not origin_aliases or not destination_aliases or len(legs) < 2:
            return False

        def leg_matches(
            leg: str,
            starts: set[str],
            ends: set[str],
            expected_date: str,
        ) -> bool:
            has_direction = any(
                f"{start}{separator}{end}" in leg.casefold()
                for start in starts
                for end in ends
                for separator in ("->", "→", "至", "-")
            )
            detail_parts = re.split(r"[:：]", leg, maxsplit=1)
            if not has_direction or expected_date not in leg or len(detail_parts) != 2:
                return False
            detail_norm = self._normalized_label(detail_parts[1])
            return (
                expected_date in detail_parts[1]
                and any(value in detail_norm for value in starts)
                and any(value in detail_norm for value in ends)
            )

        outbound_ok = any(
            leg_matches(leg, origin_aliases, destination_aliases, request.start_date)
            for leg in legs
        )
        inbound_ok = any(
            leg_matches(leg, destination_aliases, origin_aliases, request.end_date)
            for leg in legs
        )
        return outbound_ok and inbound_ok

    def _dimension_score(
        self,
        issues: list[TripPlanQualityIssue],
        codes: set[str],
    ) -> int:
        return max(
            0,
            100
            - sum(
                self._issue_penalty(issue)
                for issue in issues
                if issue.code in codes
            ),
        )

    def _evaluate_semantic_contract(
        self,
        request: TripRequest,
        plan: TripPlan,
        add,
    ) -> None:
        """Validate request/plan identity fields against the semantic contract."""
        from .destination_feasibility_service import get_destination_feasibility_service
        from .semantic_contract_service import (
            attach_contract_to_trip_request,
            user_acknowledged_contract_risks,
        )

        contract = request.semantic_contract
        if contract is None:
            # Rebuild server-side so free_text provenance is never skipped.
            try:
                contract = attach_contract_to_trip_request(request).semantic_contract
            except Exception:
                return
        if contract is None:
            return

        feasibility = get_destination_feasibility_service()
        risks_acked = user_acknowledged_contract_risks(request)

        if contract.conflicts:
            add(
                "SEMANTIC_CONTRACT_CONFLICT",
                "warning",
                "semantic_contract.conflicts",
                f"语义契约存在 {len(contract.conflicts)} 条冲突记录，已按高优先级来源保留取值。",
                "请在生成前确认出发地、人数、预算等关键字段。",
            )

        pending_critical = [
            name
            for name in contract.pending_fields
            if name
            in {
                "origin_city",
                "travelers",
                "budget",
                "start_date",
                "end_date",
                "travel_party",
            }
        ]
        if pending_critical:
            # Single acknowledgment source: structured boolean first, with
            # legacy free-text marker compatibility handled inside it.
            acknowledged = risks_acked
            add(
                "SEMANTIC_PENDING_FIELDS",
                "info",
                "semantic_contract.pending_fields",
                (
                    f"仍有待确认字段：{'、'.join(pending_critical[:6])}。"
                    + ("用户已在提交时确认按当前表单继续。" if acknowledged else "")
                ),
                "确认后可减少行程与真实需求偏差。"
                if not acknowledged
                else "已记录用户确认，仍建议行程中复核关键约束。",
            )

        # After secondary confirm, form TripRequest fields are the execution source of
        # truth; free-text-promoted contract values may intentionally diverge.
        if not risks_acked:
            if (
                contract.origin_city.is_known()
                and not contract.origin_city.pending_confirmation
                and request.origin_city
            ):
                # "山西太原" (utterance) and "太原" (form) are the same origin.
                left = feasibility.normalize_location_for_matching(
                    str(contract.origin_city.value)
                )
                right = feasibility.normalize_location_for_matching(
                    request.origin_city
                )
                if left and right and left != right:
                    add(
                        "SEMANTIC_ORIGIN_MISMATCH",
                        "error",
                        "origin_city",
                        (
                            f"请求出发地“{request.origin_city}”与语义契约"
                            f"“{contract.origin_city.value}”不一致。"
                        ),
                        "以用户确认的出发地重新生成。",
                    )

            if (
                contract.destination_city.is_known()
                and contract.destination_city.source
                in {"user_explicit", "form_confirmed"}
                and not contract.destination_city.pending_confirmation
            ):
                left = feasibility.normalize_location_for_matching(
                    str(contract.destination_city.value)
                )
                right = feasibility.normalize_location_for_matching(request.city)
                plan_city = feasibility.normalize_location_for_matching(plan.city)
                if left and right and left != right:
                    add(
                        "SEMANTIC_DESTINATION_MISMATCH",
                        "error",
                        "city",
                        (
                            f"请求目的地“{request.city}”与契约目的地"
                            f"“{contract.destination_city.value}”不一致。"
                        ),
                        "按契约/表单目的地重新生成。",
                    )
                if left and plan_city and left != plan_city:
                    add(
                        "SEMANTIC_DESTINATION_MISMATCH",
                        "error",
                        "city",
                        (
                            f"生成目的地“{plan.city}”与契约目的地"
                            f"“{contract.destination_city.value}”不一致。"
                        ),
                        "按用户确认目的地重新生成。",
                    )

            if (
                contract.travelers.is_known()
                and not contract.travelers.pending_confirmation
                and contract.travelers.source
                in {"user_explicit", "form_confirmed", "rule_inferred"}
            ):
                try:
                    expected = int(contract.travelers.value)
                except (TypeError, ValueError):
                    expected = None
                if expected is not None and expected != request.travelers:
                    add(
                        "SEMANTIC_TRAVELERS_MISMATCH",
                        "warning",
                        "travelers",
                        (
                            f"请求人数 {request.travelers} 与契约人数 {expected} 不一致"
                            f"（来源 {contract.travelers.source}）。"
                        ),
                        "确认出行人数后重新生成。",
                    )

            if (
                contract.budget.is_known()
                and not contract.budget.pending_confirmation
                and request.budget is not None
            ):
                try:
                    expected_budget = int(contract.budget.value)
                except (TypeError, ValueError):
                    expected_budget = None
                if expected_budget is not None and expected_budget != request.budget:
                    add(
                        "SEMANTIC_BUDGET_MISMATCH",
                        "warning",
                        "budget",
                        (
                            f"请求预算 {request.budget} 与契约预算 {expected_budget} 不一致。"
                        ),
                        "确认总预算上限后重新生成。",
                    )

        if (
            contract.pace.is_known()
            and str(contract.pace.value) in {"轻松", "舒缓"}
            and not contract.pace.pending_confirmation
        ):
            overloaded = [
                day
                for day in plan.days
                if len(day.attractions or []) > 2
            ]
            if overloaded:
                add(
                    "SEMANTIC_PACE_MISMATCH",
                    "warning",
                    "pace",
                    (
                        f"契约节奏为“{contract.pace.value}”，但有 "
                        f"{len(overloaded)} 天安排超过 2 个主景点。"
                    ),
                    "减少每日主景点或改为更松弛的节奏偏好。",
                )

        if (
            contract.travel_party.is_known()
            and contract.travel_party.pending_confirmation
        ):
            add(
                "SEMANTIC_PARTY_UNCONFIRMED",
                "info",
                "travel_party",
                f"同行关系待确认：{contract.travel_party.value}。",
                "确认同行关系与人数后可提高行程匹配度。",
            )

        self._evaluate_exclusions(contract, plan, feasibility, add, risks_acked)

    def _evaluate_exclusions(
        self, contract, plan, feasibility, add, risks_acked: bool
    ) -> None:
        """What the user ruled out must not come back in the generated plan.

        After an explicit secondary confirmation the user owns the decision, so
        the destination check degrades to a warning instead of blocking.
        """
        excluded_cities = (
            [str(item) for item in contract.excluded_destinations.value]
            if contract.excluded_destinations.is_known()
            and isinstance(contract.excluded_destinations.value, list)
            else []
        )
        destination = feasibility.normalize_location_for_matching(plan.city)
        if excluded_cities and destination:
            for city in excluded_cities:
                if feasibility.normalize_location_for_matching(city) == destination:
                    add(
                        "SEMANTIC_EXCLUDED_DESTINATION",
                        "warning" if risks_acked else "error",
                        "city",
                        f"用户明确排除了“{city}”，但行程目的地仍是该城市。",
                        "改用其他目的地重新生成，或请用户确认取消该排除。",
                    )

        excluded_themes = (
            [str(item) for item in contract.excluded_themes.value]
            if contract.excluded_themes.is_known()
            and isinstance(contract.excluded_themes.value, list)
            else []
        )
        if not excluded_themes:
            return
        for theme in excluded_themes:
            hits = [
                attraction.name
                for day in plan.days
                for attraction in (day.attractions or [])
                if theme in f"{attraction.name} {attraction.category or ''}"
            ]
            if hits:
                add(
                    "SEMANTIC_EXCLUDED_THEME",
                    "warning",
                    "days[].attractions",
                    (
                        f"用户明确排除了“{theme}”，但行程仍包含 "
                        f"{'、'.join(hits[:3])}。"
                    ),
                    "移除或替换这些安排后再确认行程。",
                )

    def _prefers_relaxed_pace(self, request: TripRequest) -> bool:
        contract = request.semantic_contract
        if (
            contract is not None
            and contract.pace.is_known()
            and str(contract.pace.value) in {"轻松", "舒缓"}
        ):
            return True
        # Shared free-text / preference markers with planner finalize.
        return prefers_gentle_pacing(request)

    def _date_range(self, start: str, end: str) -> list[str]:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            return []
        if end_date < start_date:
            return []
        return [
            (start_date + timedelta(days=index)).isoformat()
            for index in range((end_date - start_date).days + 1)
        ]

    def _forecast_check_dates(self, dates: Iterable[str]) -> list[str]:
        today = date.today()
        horizon = today + timedelta(days=max(0, self.FORECAST_WINDOW_DAYS - 1))
        result: list[str] = []
        for value in dates:
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                continue
            if today <= parsed <= horizon:
                result.append(value)
        return result

    def _within_forecast_window(self, dates: Iterable[str]) -> bool:
        return bool(self._forecast_check_dates(dates))

    def _attraction_category(self, name: str, category: str) -> str:
        text = f"{name or ''} {category or ''}"
        if any(
            marker in text
            for marker in (
                "公园", "园林", "植物园", "湿地", "绿道", "山", "湖", "瀑布", "峡谷"
            )
        ):
            return "nature"
        if any(
            marker in text
            for marker in (
                "街区", "步行街", "商业街", "老街", "古街", "水街", "商圈", "购物", "广场"
            )
        ):
            return "street"
        if any(
            marker in text
            for marker in (
                "博物馆", "美术馆", "艺术馆", "纪念馆", "科技馆", "文化馆",
                "古镇", "古城", "遗址", "寺", "庙", "祠", "塔", "城墙",
            )
        ):
            return "culture"
        if any(
            marker in text
            for marker in ("动物园", "海洋馆", "游乐园", "主题乐园", "度假区")
        ):
            return "leisure"
        return "other"

    def _attraction_matches_destination(
        self, request: TripRequest, attraction,
    ) -> str:
        """Return ``"matched"``, ``"mismatched"``, or ``"unknown"``.

        Reads structured verification metadata from the Attraction,
        falling back to address text parsing when no structured fields
        are available.
        """
        verification = getattr(attraction, "verification", None)
        cityname = getattr(verification, "cityname", "") or ""
        citycode = getattr(verification, "citycode", "") or ""
        adname = getattr(verification, "adname", "") or ""
        adcode = getattr(verification, "adcode", "") or ""
        return poi_destination_status(
            destination_city=request.city,
            cityname=cityname,
            citycode=citycode,
            adname=adname,
            adcode=adcode,
            address=getattr(attraction, "address", "") or "",
            name=getattr(attraction, "name", "") or "",
        )

    @staticmethod
    def _category_preference_markers(category: str) -> list[str]:
        """Return user preference keywords that align with *category*.

        When the user has stated a preference matching the dominant
        attraction category, concentration is intentional and should
        not be penalised.
        """
        mapping: dict[str, list[str]] = {
            "culture": ["历史文化", "文化", "历史", "古迹", "博物馆", "古镇", "人文"],
            "nature": ["自然风光", "自然", "山水", "户外", "风景"],
            "leisure": ["休闲", "娱乐", "亲子", "度假"],
            "street": ["逛街", "购物", "城市"],
        }
        return mapping.get(category, [])

    def _looks_like_non_tourism_poi(self, name: str, category: str) -> bool:
        text = f"{name or ''} {category or ''}"
        rejected = (
            "商务住宅", "住宅区", "小区", "公寓", "写字楼", "公司企业",
            "产业园", "工业园", "售楼部", "营销中心", "汽车服务", "汽车销售",
            "汽车维修", "汽车街区", "汽车城", "家居城", "建材城", "餐饮服务",
            "停车场", "售票处", "卫生间", "出入口", "儿童滑梯",
        )
        if any(marker in text for marker in rejected):
            return True
        if any(
            marker in (name or "")
            for marker in ("不对外开放", "暂停开放", "施工中")
        ):
            return True
        food_markers = (
            "餐厅", "餐馆", "饭店", "酒家", "火锅", "烧烤", "烤肉", "咖啡",
            "茶馆", "茶楼", "天妇罗", "料理", "食府", "小吃", "面馆", "甜品",
            "蛋糕", "酒吧", "bistro",
        )
        if (
            any(marker in (name or "").lower() for marker in food_markers)
            and "博物馆" not in (name or "")
        ):
            return True
        return bool(
            re.search(r"(?:分店|门店|旗舰店|体验店)$", name or "")
            or re.search(r"[（(][^）)]*店[）)]$", name or "")
        )

    def _distance_km(self, origin, destination) -> float:
        lon1, lat1, lon2, lat2 = map(
            math.radians,
            [
                origin.longitude,
                origin.latitude,
                destination.longitude,
                destination.latitude,
            ],
        )
        delta_lon = lon2 - lon1
        delta_lat = lat2 - lat1
        value = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
        return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(value)))

    def _usable_weather(self, item) -> bool:
        value = (item.date or "")[:10]
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        descriptions = [
            str(item.day_weather or "").strip(),
            str(item.night_weather or "").strip(),
        ]
        invalid_descriptions = {"", "未知", "暂无", "无", "--", "null", "none"}
        if not any(
            description.casefold() not in invalid_descriptions
            for description in descriptions
        ):
            return False
        try:
            day_temp = float(item.day_temp)
            night_temp = float(item.night_temp)
        except (TypeError, ValueError):
            return False
        return -60 <= day_temp <= 60 and -60 <= night_temp <= 60

    def _expected_hotel_nights(self, request: TripRequest) -> int:
        try:
            start = date.fromisoformat(request.start_date)
            end = date.fromisoformat(request.end_date)
        except ValueError:
            return max(0, request.travel_days - 1)
        return max(0, (end - start).days)

    def _requires_hotel(self, request: TripRequest, nights: int) -> bool:
        if nights <= 0:
            return False
        accommodation = request.accommodation or ""
        no_hotel_markers = (
            "不住宿", "无需住宿", "当天往返", "露营", "帐篷", "房车",
            "住亲友", "亲友家", "自有住房", "住宿已安排",
        )
        return not any(marker in accommodation for marker in no_hotel_markers)

    def _trusted_hotel(self, hotel) -> bool:
        return bool(
            hotel
            and (hotel.poi_id or "").strip()
            and hotel.location is not None
            and self._valid_china_location(
                hotel.location.longitude, hotel.location.latitude
            )
        )

    def _accepts_hostel(self, accommodation: str) -> bool:
        return any(
            marker in (accommodation or "")
            for marker in ("青年旅舍", "青年旅社", "青旅", "床位")
        )

    def _requested_high_speed(self, mode: str) -> bool:
        return any(marker in (mode or "") for marker in ("高铁", "动车"))

    def _hotel_unit_price_floor(self, accommodation: str) -> int:
        if self._accepts_hostel(accommodation):
            return 60
        if "豪华" in accommodation:
            return 400
        if "舒适" in accommodation or "亲子" in accommodation:
            return 180
        if "民宿" in accommodation or "经济" in accommodation:
            return 100
        return 120

    def _minimum_reasonable_budget(self, request: TripRequest) -> int:
        travelers = max(1, request.travelers)
        days = max(1, request.travel_days)

        feasibility = get_destination_feasibility_service()
        is_same_city = bool(
            request.origin_city
            and feasibility.normalize_city(request.origin_city)
            == feasibility.normalize_city(request.city)
        )
        free_text = (request.free_text_input or "").casefold()
        is_free_trip = any(
            kw in free_text
            for kw in ("免费", "自带", "城市漫步", "漫步", "徒步", "校园", "公园", "短途")
        )
        if days <= 1 or is_same_city or is_free_trip:
            return 0

        meal_floor = 90 * travelers * days
        local_transport_floor = 15 * travelers * days

        try:
            start = date.fromisoformat(request.start_date)
            end = date.fromisoformat(request.end_date)
            nights = max(0, (end - start).days)
        except ValueError:
            nights = max(0, days - 1)
        rooms = max(1, math.ceil(travelers / 2))
        hotel_floor = (
            150 * nights * rooms
            if self._requires_hotel(request, nights)
            else 0
        )

        intercity_floor = 0
        if request.origin_city and not is_same_city:
            intercity_floor = 200 * travelers

        return (
            meal_floor
            + local_transport_floor
            + hotel_floor
            + intercity_floor
        )

    def _issue_penalty(self, issue: TripPlanQualityIssue) -> int:
        if issue.severity == "info":
            return 0
        if issue.code in self.ISSUE_PENALTIES:
            return self.ISSUE_PENALTIES[issue.code]
        return 25 if issue.severity == "error" else 6

    def _valid_china_location(self, longitude: float, latitude: float) -> bool:
        return 73.0 <= longitude <= 136.0 and 3.0 <= latitude <= 54.0

    def _meal_names(self, meal_types: set[str]) -> str:
        labels = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
        return "、".join(labels.get(value, value) for value in sorted(meal_types))


def _has_error_issues(quality: TripPlanQualityResult | None) -> bool:
    return any(
        str(getattr(issue, "severity", "") or "").strip().lower() == "error"
        for issue in (getattr(quality, "issues", None) or [])
    )


def refresh_quality_gate(
    quality: TripPlanQualityResult,
    *,
    generation_mode: str = "primary",
    force_review: bool = False,
) -> TripPlanQualityResult:
    """Recompute ``publishable`` + ``review_required`` after a mutation.

    ``evaluate()`` decides the pair once at construction; the pipeline then
    mutates ``score`` and ``issues`` (partial enrichment, repair rounds), which
    used to leave the pair describing a plan that no longer exists. Routing the
    same rule through one recomputable function keeps them coherent.

    ``force_review`` demotes a plan to "deliverable but review it" without
    inventing an error issue — partial enrichment is a confidence problem, not
    a correctness one, so it must not make the plan undeliverable.
    """
    has_blocking = any(
        issue_disposition(issue) == "blocking" for issue in quality.issues
    )
    has_advisory = any(
        issue_disposition(issue) == "advisory" for issue in quality.issues
    )
    if has_blocking:
        quality.publishable = False
        quality.review_required = True
        quality.status = "failed"
        quality.quality_status = "blocked"
    elif (
        has_advisory
        or force_review
        or quality.score < 100
        or generation_mode in {"repaired", "map_fallback"}
    ):
        quality.publishable = True
        quality.review_required = True
        quality.status = "warning"
        quality.quality_status = "needs_review"
    else:
        quality.publishable = True
        quality.review_required = False
        quality.status = "passed"
        quality.quality_status = "publishable"
    return quality


def resolve_plan_quality_status(plan: TripPlan) -> str:
    """Derive ``blocked | needs_review | publishable`` for routing and display.

    Not a stored field: ``publishable`` + ``review_required`` are the authority
    (see MERGE_REVIEW.md scheme 1). This is the one place that flattens them,
    so callers cannot invent a fourth derivation.
    """
    quality = getattr(plan, "quality", None)
    if quality is None:
        return "blocked"
    if not bool(getattr(quality, "publishable", False)):
        return "blocked"
    if bool(getattr(quality, "review_required", False)):
        return "needs_review"
    return "publishable"


_trip_plan_quality_service: TripPlanQualityService | None = None


def get_trip_plan_quality_service() -> TripPlanQualityService:
    global _trip_plan_quality_service
    if _trip_plan_quality_service is None:
        _trip_plan_quality_service = TripPlanQualityService()
    return _trip_plan_quality_service