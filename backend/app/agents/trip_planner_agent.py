"""多智能体旅行规划系统"""

import json
import logging
import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from typing import Dict, Any, Callable, List, Optional
from hello_agents import SimpleAgent
from ..services.llm_service import get_llm
from ..services.semantic_contract_service import decided_constraint_text
from ..services.transport_budget_service import get_transport_budget_service
from ..services.amap_service import get_amap_service
from ..models.schemas import (
    TripRequest, TripPlan, DayPlan, Attraction, Meal, WeatherInfo,
    Location, Hotel, RouteSegment, POIInfo, AgentAuditResult
)
from ..config import get_settings
from .web_travel_guide_agent import get_web_travel_guide_agent
from .graph.trip_planning_graph import TripPlanningAgentGraph


logger = logging.getLogger(__name__)

# ============ Agent提示词 ============

REPAIR_AGENT_PROMPT = """你是旅行计划JSON结构修复专家。
只修复输入中已有旅行计划的JSON语法、字段类型、日期和天数结构，不新增未经提供的事实。
必须保持用户确认的目的地、起止日期和出行天数；每天保留已有景点信息，不得编造新的地点、坐标、票价或来源。
只输出一个可解析的JSON对象，不输出Markdown代码块、解释、EOF或其他文字。
"""

PLANNER_AGENT_PROMPT = """你是行程规划专家。你的任务是根据景点信息和天气信息,生成详细的旅行计划。

请严格按照以下JSON格式返回旅行计划:
```json
{
  "city": "城市名称",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "description": "第1天行程概述",
      "transportation": "交通方式",
      "accommodation": "住宿类型",
      "hotel": {
        "name": "酒店名称",
        "address": "酒店地址",
        "location": {"longitude": 116.397128, "latitude": 39.916527},
        "price_range": "300-500元",
        "rating": "4.5",
        "distance": "距离景点2公里",
        "type": "经济型酒店",
        "estimated_cost": 400
      },
      "attractions": [
        {
          "name": "景点名称",
          "address": "详细地址",
          "location": {"longitude": 116.397128, "latitude": 39.916527},
          "visit_duration": 150,
          "description": "景点详细描述",
          "category": "景点类别",
          "ticket_price": 60
        }
      ],
      "meals": [
        {"type": "breakfast", "name": "早餐推荐", "description": "早餐描述", "estimated_cost": 30},
        {"type": "lunch", "name": "午餐推荐", "description": "午餐描述", "estimated_cost": 50},
        {"type": "dinner", "name": "晚餐推荐", "description": "晚餐描述", "estimated_cost": 80}
      ]
    }
  ],
  "weather_info": [
    {
      "date": "YYYY-MM-DD",
      "day_weather": "晴",
      "night_weather": "多云",
      "day_temp": 25,
      "night_temp": 15,
      "wind_direction": "南风",
      "wind_power": "1-3级"
    }
  ],
  "overall_suggestions": "总体建议",
  "budget": {
    "total_attractions": 180,
    "total_hotels": 1200,
    "total_meals": 480,
    "total_transportation": 200,
    "total": 2060
  }
}
```

**重要提示:**
1. weather_info数组只能使用天气查询结果中明确覆盖旅行日期的数据；如果查询结果不覆盖旅行日期,weather_info必须返回空数组,不要把其他日期天气改写成旅行日期
2. 温度必须是纯数字(不要带°C等单位)
3. 每天安排2-3个景点
4. 考虑景点之间的距离和游览时间
5. 每天必须包含早中晚三餐
6. 提供实用的旅行建议
7. **必须包含预算信息**:
   - 景点门票价格(ticket_price)
   - 餐饮预估费用(estimated_cost)
   - 酒店预估费用(estimated_cost)
   - 预算汇总(budget)包含各项总费用
8. 景点游览时间必须按景点体量和类型分别估算，不要把所有景点统一写成120分钟
9. 景点描述应简要说明可看的内容和推荐原因，不要使用“这是某地的著名景点”等空泛句式
10. 三餐名称应尽量具体，不能只写“早餐推荐”“午餐推荐”“晚餐推荐”等占位内容
"""


class MultiAgentTripPlanner:
    """多智能体旅行规划系统"""

    _MAX_ATTRACTION_VERIFICATION_SEARCHES = 24
    _MAX_MEAL_POI_SEARCHES = 24

    def __init__(self):
        """Initialize the performance-aware multi-agent coordinator."""
        logger.info("[planner] initializing trip-planning coordinator")
        try:
            self.settings = get_settings()
            self.llm = get_llm()
            self.budget_service = get_transport_budget_service()
            self.web_guide_agent = get_web_travel_guide_agent()
            self.amap_service = get_amap_service()
            self.amap_tool = None
            self.trip_graph = TripPlanningAgentGraph(self)
            framework = "langgraph" if self.trip_graph.graph_available else "sequential"
            logger.info(f"[planner] coordinator ready: framework={framework}")
            logger.info(
                "[planner] web guide provider: "
                f"{self.web_guide_agent.status()['provider']}"
            )
        except Exception as exc:
            logger.info(f"[planner] initialization failed: {type(exc).__name__}")
            raise

    def plan_trip(
        self,
        request: TripRequest,
        progress_callback: Optional[Callable[..., None]] = None,
        *,
        allow_unpublishable: bool = False,
    ) -> TripPlan:
        """Generate a trip through the conditional multi-agent graph.

        Public entry hard-gate: plans that fail quality (publishable=false)
        raise TripPlanQualityRejectedError unless allow_unpublishable=True
        (tests/diagnostics only). Graph internals may still return failed
        plans for inspection; callers of plan_trip must not treat them as
        normal success by default.
        """
        try:
            # The HTTP entry already attached the server-built contract;
            # this fallback covers direct/internal callers only, keeping
            # the at-most-one-extraction invariant per request.
            if getattr(request, "semantic_contract", None) is None:
                from ..services.semantic_contract_service import (
                    attach_contract_to_trip_request,
                )

                request = attach_contract_to_trip_request(request)
        except Exception:
            # Contract attachment must never block trip generation.
            pass
        graph = getattr(self, "trip_graph", None)
        if graph is None:
            graph = TripPlanningAgentGraph(self)
            self.trip_graph = graph
        plan = graph.run(request, progress_callback)
        return self._enforce_public_quality_gate(
            request,
            plan,
            allow_unpublishable=allow_unpublishable,
        )

    def _enforce_public_quality_gate(
        self,
        request: TripRequest,
        plan: TripPlan,
        *,
        allow_unpublishable: bool = False,
    ) -> TripPlan:
        """Read the unified quality_status from the quality service.

        - ``blocked`` → raise TripPlanQualityRejectedError
        - ``needs_review`` → return plan as-is (not an error)
        - ``publishable`` → return plan as-is
        """
        from ..services.trip_generation_errors import TripPlanQualityRejectedError
        from ..services.trip_plan_quality_service import (
            get_trip_plan_quality_service,
            resolve_plan_quality_status,
        )

        if plan.quality is None:
            plan.quality = get_trip_plan_quality_service().evaluate(request, plan)

        if allow_unpublishable:
            return plan

        # Same unified resolver as the HTTP layer — one gate, one decision.
        if resolve_plan_quality_status(plan) == "blocked":
            raise TripPlanQualityRejectedError(quality=plan.quality, plan=plan)
        return plan

    def _run_primary_planner(
        self,
        request: TripRequest,
        attractions: str,
        weather: str,
        hotels: str,
    ) -> str:
        """Run the only planning-model call on the normal execution path."""
        planner_query = self._build_planner_query(
            request, attractions, weather, hotels
        )
        request_planner = SimpleAgent(
            name="行程规划专家",
            llm=self.llm,
            system_prompt=PLANNER_AGENT_PROMPT,
        )
        return request_planner.run(planner_query)

    def _repair_planner_response(
        self,
        request: TripRequest,
        planner_response: str,
    ) -> str:
        """Conditionally repair an invalid primary response at most once."""
        source = (planner_response or "").strip()
        if not source:
            raise ValueError("empty planner response cannot be repaired")
        repair_query = f"""请修复下面的旅行计划JSON，使其严格满足：
- 目的地：{request.city}
- 开始日期：{request.start_date}
- 结束日期：{request.end_date}
- days必须恰好包含{request.travel_days}天，日期连续且day_index从0开始
- 必须保留city、start_date、end_date、days、weather_info、overall_suggestions
- 每天保留attractions和meals数组；缺失的非事实文本字段可使用空字符串
- 不新增输入中不存在的景点、酒店、坐标、票价或联网事实

待修复内容：
{source[:50000]}
"""
        repair_agent = SimpleAgent(
            name="行程结构修复专家",
            llm=self.llm,
            system_prompt=REPAIR_AGENT_PROMPT,
        )
        return repair_agent.run(repair_query)

    def _search_attractions(self, request: TripRequest) -> List[POIInfo]:
        preference_queries = {
            "历史文化": "博物馆",
            "自然风光": "风景区",
            "艺术": "美术馆",
            "休闲": "公园",
            "购物": "特色街区",
        }
        queries = [
            preference_queries[preference]
            for preference in request.preferences
            if preference in preference_queries
        ]
        if not any(
            preference in request.preferences
            for preference in ("历史文化", "艺术")
        ):
            queries.append("博物馆")
        if not any(
            preference in request.preferences
            for preference in ("自然风光", "休闲")
        ):
            queries.append("公园")
        queries.append("名胜古迹")
        queries = list(dict.fromkeys(queries))

        # Category coverage is fetched concurrently. This adds no model calls
        # and keeps wall-clock discovery latency near the slowest map request.
        groups: List[List[POIInfo]] = []
        with ThreadPoolExecutor(max_workers=min(4, len(queries))) as executor:
            futures = [
                executor.submit(self.amap_service.search_poi, query, request.city)
                for query in queries
            ]
            for future in futures:
                try:
                    groups.append(future.result())
                except Exception as exc:
                    logger.info(
                        "[planner] attraction category search failed: "
                        f"{type(exc).__name__}"
                    )
                    groups.append([])

        interleaved: List[POIInfo] = []
        for index in range(max((len(group) for group in groups), default=0)):
            for group in groups:
                if index < len(group):
                    interleaved.append(group[index])
        merged = self._merge_pois(interleaved)
        return self._diversify_attraction_pois(request, merged)

    def _diversify_attraction_pois(
        self,
        request: TripRequest,
        pois: List[POIInfo],
    ) -> List[POIInfo]:
        """Filter weak POIs and interleave attraction categories locally."""
        buckets: Dict[str, List[POIInfo]] = {
            "culture": [],
            "nature": [],
            "leisure": [],
            "street": [],
            "other": [],
        }
        seen_names: set[str] = set()
        for poi in pois:
            if not self._is_suitable_attraction_poi(poi):
                continue
            normalized_name = self._normalize_poi_name(poi.name)
            if not normalized_name or normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            buckets[self._attraction_poi_category(poi)].append(poi)

        preference_order: List[str] = []
        preference_categories = {
            "历史文化": "culture",
            "艺术": "culture",
            "自然风光": "nature",
            "休闲": "nature",
            "购物": "street",
        }
        for preference in request.preferences:
            category = preference_categories.get(preference)
            if category and category not in preference_order:
                preference_order.append(category)
        category_order = preference_order + [
            category
            for category in ("culture", "nature", "leisure", "street", "other")
            if category not in preference_order
        ]

        # The deterministic fallback keeps at most two main attractions per
        # day, so this cap holds commercial streets to roughly one third.
        street_limit = max(1, (request.travel_days * 2) // 3)
        result: List[POIInfo] = []
        street_count = 0
        while True:
            added = False
            for category in category_order:
                if category == "street" and street_count >= street_limit:
                    continue
                bucket = buckets[category]
                if not bucket:
                    continue
                result.append(bucket.pop(0))
                if category == "street":
                    street_count += 1
                added = True
            if not added:
                break
        return self._cap_repetitive_experiences(request, result)

    def _cap_repetitive_experiences(
        self,
        request: TripRequest,
        pois: List[POIInfo],
    ) -> List[POIInfo]:
        """Select the strongest repeated experiences, then retain route order."""
        limits = {"museum": 3, "park": 4}
        selected_by_tag: Dict[str, set[int]] = {}
        for tag, limit in limits.items():
            candidates = [
                index
                for index, poi in enumerate(pois)
                if tag in self._attraction_experience_tags(poi)
            ]
            ranked = sorted(
                candidates,
                key=lambda index: (
                    self._experience_quality_score(request, pois[index], tag),
                    -index,
                ),
                reverse=True,
            )
            selected_by_tag[tag] = set(ranked[:limit])

        # Selection quality is evaluated globally within each repeated type,
        # while output order remains the category-interleaved route order.
        result: List[POIInfo] = []
        for index, poi in enumerate(pois):
            tags = self._attraction_experience_tags(poi)
            if tags and any(
                index not in selected_by_tag[tag]
                for tag in tags
                if tag in selected_by_tag
            ):
                continue
            result.append(poi)
        return result

    def _experience_quality_score(
        self,
        request: TripRequest,
        poi: POIInfo,
        tag: str,
    ) -> float:
        """Rank capped POIs using provider quality and generic public signals."""
        rating = max(0.0, min(5.0, float(poi.rating or 0)))
        name = (poi.name or "").strip()
        poi_type = (poi.type or "").strip()
        text = f"{name} {poi_type}"
        score = rating * 10.0

        normalized_city = self._normalize_poi_name(request.city)
        normalized_name = self._normalize_poi_name(name)
        if normalized_city and normalized_city in normalized_name:
            score += 0.8
        if any(
            marker in text
            for marker in (
                "国家一级", "国家级", "全国重点", "5A级", "AAAAA", "世界遗产"
            )
        ):
            score += 0.9
        if tag == "museum":
            if "博物院" in name:
                score += 0.7
            if re.search(r"(?:国家|省|市|自治区|自治州).{0,6}(?:博物馆|博物院)$", name):
                score += 0.5
        elif tag == "park" and any(
            marker in name for marker in ("国家公园", "国家森林公园", "国家湿地公园")
        ):
            score += 0.7

        score += self._poi_preference_quality_bonus(request, text)
        return score

    def _poi_preference_quality_bonus(
        self,
        request: TripRequest,
        poi_text: str,
    ) -> float:
        """Let explicit topical preferences influence otherwise factual rank."""
        normalized_poi = self._normalize_poi_name(poi_text)
        explicit_values = [
            self._normalize_poi_name(value)
            for value in request.preferences
            if value
        ]
        direct_match = any(
            len(value) >= 2 and value in normalized_poi
            for value in explicit_values
        )
        intent_text = f"{' '.join(request.preferences)} {request.free_text_input or ''}"
        topical_markers = (
            "航空", "航天", "汽车", "铁路", "科技", "自然", "历史", "考古",
            "艺术", "美术", "文学", "诗歌", "民俗", "军事", "地质", "摄影",
            "熊猫", "植物", "湿地", "亲子", "儿童",
        )
        topical_match = any(
            marker in intent_text and marker in poi_text
            for marker in topical_markers
        )
        return 2.5 if direct_match or topical_match else 0.0

    def _attraction_experience_tags(self, poi: POIInfo) -> set[str]:
        text = f"{poi.name or ''} {poi.type or ''}"
        tags: set[str] = set()
        if any(
            marker in text
            for marker in (
                "博物馆", "美术馆", "艺术馆", "纪念馆", "科技馆", "展览馆"
            )
        ):
            tags.add("museum")
        if any(
            marker in text
            for marker in ("公园", "绿道", "湿地", "植物园")
        ):
            tags.add("park")
        return tags

    def _is_suitable_attraction_poi(self, poi: POIInfo) -> bool:
        name = (poi.name or "").strip()
        poi_type = (poi.type or "").strip()
        text = f"{name} {poi_type}"
        strong_markers = (
            "风景名胜", "旅游景点", "景区", "名胜古迹", "博物馆", "美术馆",
            "艺术馆", "纪念馆", "科技馆", "文化馆", "公园", "园林", "植物园",
            "动物园", "海洋馆", "游乐园", "主题乐园", "古镇", "古城", "遗址",
            "寺", "庙", "祠", "塔", "城墙", "湿地", "步行街", "老街", "街区",
            "山", "湖", "瀑布", "峡谷", "广场", "景点",
        )
        rejected_types = (
            "商务住宅", "住宅区", "公司企业", "产业园区", "汽车服务", "汽车销售",
            "汽车维修", "摩托车服务", "住宿服务", "餐饮服务", "医疗保健服务",
            "金融保险服务", "生活服务", "地名地址信息", "室内设施",
        )
        if any(marker in poi_type for marker in rejected_types):
            return False
        rejected_names = (
            "小区", "公寓", "写字楼", "产业园", "工业园", "售楼部", "营销中心",
            "汽车街区", "汽车城", "家居城", "建材城", "停车场", "售票处",
            "卫生间", "出入口", "入口", "出口", "儿童滑梯", "不对外开放",
            "暂停开放", "施工中",
        )
        if any(marker in name for marker in rejected_names):
            return False
        food_name_markers = (
            "餐厅", "餐馆", "饭店", "酒家", "火锅", "烧烤", "烤肉", "咖啡",
            "茶馆", "茶楼", "天妇罗", "料理", "食府", "小吃", "面馆", "甜品",
            "蛋糕", "酒吧", "bistro",
        )
        if (
            any(marker in name.lower() for marker in food_name_markers)
            and "博物馆" not in name
        ):
            return False
        looks_like_branch = bool(
            re.search(r"(?:分店|门店|旗舰店|体验店)$", name)
            or re.search(r"[（(][^）)]*店[）)]$", name)
        )
        if looks_like_branch:
            return False
        return any(marker in text for marker in strong_markers)

    def _attraction_poi_category(self, poi: POIInfo) -> str:
        text = f"{poi.name or ''} {poi.type or ''}"
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

    def _search_hotels(
        self,
        request: TripRequest,
        attraction_pois: List[POIInfo],
    ) -> List[POIInfo]:
        city_hotels = self.amap_service.search_poi("酒店", request.city)
        center = self._poi_centroid(attraction_pois[:12])
        if center is None:
            return city_hotels
        nearby = self.amap_service.search_poi_around(
            "酒店",
            center,
            radius=12000,
            city=request.city,
        )
        return self._merge_pois(nearby, city_hotels)

    def _merge_pois(self, *groups: List[POIInfo]) -> List[POIInfo]:
        merged: List[POIInfo] = []
        seen: set[str] = set()
        for group in groups:
            for poi in group:
                key = poi.id or f"{poi.name}:{poi.location.longitude}:{poi.location.latitude}"
                if key in seen:
                    continue
                seen.add(key)
                merged.append(poi)
        return merged

    def _poi_centroid(self, pois: List[POIInfo]) -> Optional[Location]:
        if not pois:
            return None
        return Location(
            longitude=sum(poi.location.longitude for poi in pois) / len(pois),
            latitude=sum(poi.location.latitude for poi in pois) / len(pois),
        )

    def _search_weather_text(self, request: TripRequest) -> str:
        weather = self.amap_service.get_weather(request.city, request.start_date, request.end_date)
        return self._format_weather_for_prompt(request, weather)

    def _format_weather_for_prompt(self, request: TripRequest, weather: List[WeatherInfo]) -> str:
        if not weather:
            return f"高德天气查询结果: 未查询到{request.city}天气。"
        lines = [f"高德天气查询结果: {request.city}"]
        for item in weather[:8]:
            lines.append(
                f"{item.date}: 白天{item.day_weather}, 夜间{item.night_weather}, "
                f"温度{item.day_temp}/{item.night_temp}℃, {item.wind_direction}{item.wind_power}"
            )
        return "\n".join(lines)

    def _format_pois_for_prompt(self, label: str, pois: List[Any], limit: int = 10) -> str:
        if not pois:
            return f"{label}: 未查询到结果。"
        lines = [label]
        for index, poi in enumerate(pois[:limit], 1):
            location = ""
            if getattr(poi, "location", None):
                location = f" | 坐标 {poi.location.longitude},{poi.location.latitude}"
            address = getattr(poi, "address", "") or "地址未提供"
            poi_type = getattr(poi, "type", "") or "类型未提供"
            lines.append(f"{index}. {poi.name} | {poi_type} | {address}{location}")
        return "\n".join(lines)

    def _ground_trip_plan(
        self,
        request: TripRequest,
        trip_plan: TripPlan,
        attraction_pois: List[POIInfo],
        hotel_pois: List[POIInfo],
    ) -> TripPlan:
        """Replace model-generated locations with verified AMap POI records."""
        source_pool = [
            poi for poi in attraction_pois
            if self._is_suitable_attraction_poi(poi)
        ]
        used_ids: set[str] = set()
        search_cache: Dict[str, List[POIInfo]] = {}
        attraction_searches_remaining = self._MAX_ATTRACTION_VERIFICATION_SEARCHES

        for day in trip_plan.days:
            # Hotel and restaurant proof is server-owned. The model can forge
            # POI IDs and coordinates, so discard those entities wholesale;
            # verified hotel candidates and nearby restaurant searches below
            # are the only paths that may add them back.
            day.transportation = request.transportation
            day.accommodation = request.accommodation
            day.hotel = None
            day.meals = []
            verified_attractions: List[Attraction] = []
            for attraction in day.attractions:
                # Verification fields are server-owned. A model cannot certify
                # its own coordinates or POI identifier.
                attraction.poi_id = ""
                attraction.coordinate_source = ""
                matched, score = self._best_poi_match(attraction.name, source_pool)
                if score < 0.72:
                    if attraction.name not in search_cache:
                        if attraction_searches_remaining <= 0:
                            search_cache[attraction.name] = []
                        else:
                            attraction_searches_remaining -= 1
                            try:
                                search_cache[attraction.name] = [
                                    poi
                                    for poi in self.amap_service.search_poi(
                                        attraction.name,
                                        request.city,
                                    )
                                    if self._is_suitable_attraction_poi(poi)
                                ]
                            except Exception as exc:
                                logger.info(
                                    "[planner] attraction verification search failed: "
                                    f"{type(exc).__name__}"
                                )
                                search_cache[attraction.name] = []
                    searched, searched_score = self._best_poi_match(
                        attraction.name,
                        search_cache[attraction.name],
                    )
                    if searched is not None and searched_score > score:
                        matched, score = searched, searched_score

                if (
                    matched is None
                    or score < 0.45
                    or matched.id in used_ids
                ):
                    # A real but unrelated POI is not a valid correction for a
                    # model-invented place. Keep only a sufficiently similar,
                    # unused match; deterministic fallback POIs match exactly.
                    continue
                used_ids.add(matched.id)
                self._apply_verified_poi(attraction, matched)
                verified_attractions.append(attraction)
            # Never expose model-provided coordinates for an unmatched place.
            day.attractions = verified_attractions

        filled_days = self._fill_empty_days_from_source_pool(
            request,
            trip_plan,
            source_pool,
            used_ids,
        )
        if filled_days:
            if trip_plan.generation_mode != "map_fallback":
                trip_plan.generation_mode = "repaired"
            note = (
                f"地图校准移除了未匹配地点，并为 {filled_days} 个整天空白日期"
                "各补充了1个未使用的高德认证景点；补充项是全新可信对象，"
                "不是对模型虚构地点的同名或随机替换。"
            )
            if note not in trip_plan.overall_suggestions:
                trip_plan.overall_suggestions = (
                    f"{trip_plan.overall_suggestions.rstrip()} {note}"
                ).strip()

        selected_hotel = self._select_central_hotel(
            request,
            trip_plan,
            hotel_pois,
        )
        if selected_hotel is not None:
            for day in trip_plan.days:
                day.hotel = selected_hotel.model_copy(deep=True)
        return trip_plan

    def _fill_empty_days_from_source_pool(
        self,
        request: TripRequest,
        trip_plan: TripPlan,
        source_pool: List[POIInfo],
        used_ids: set[str],
    ) -> int:
        """Add one newly constructed trusted POI only to fully empty days."""
        verified_locations = [
            attraction.location
            for day in trip_plan.days
            for attraction in day.attractions
            if attraction.coordinate_source == "amap_poi" and attraction.poi_id
        ]
        centroid = None
        if verified_locations:
            centroid = Location(
                longitude=sum(item.longitude for item in verified_locations) / len(verified_locations),
                latitude=sum(item.latitude for item in verified_locations) / len(verified_locations),
            )

        candidates = [
            poi
            for poi in source_pool
            if poi.id and poi.id not in used_ids
        ]
        filled = 0
        last_index = len(trip_plan.days) - 1
        for day in trip_plan.days:
            if day.attractions or not candidates:
                continue
            edge_day = day.day_index in {0, last_index}
            selected = max(
                candidates,
                key=lambda poi: self._empty_day_fill_score(
                    request, poi, centroid, edge_day
                ),
            )
            attraction = Attraction(
                name=selected.name,
                address=selected.address,
                location=selected.location.model_copy(deep=True),
                visit_duration=0,
                description="",
                category="景点",
                ticket_price=0,
            )
            self._apply_verified_poi(attraction, selected)
            day.attractions = [attraction]
            used_ids.add(selected.id)
            candidates.remove(selected)
            filled += 1
        return filled

    def _empty_day_fill_score(
        self,
        request: TripRequest,
        poi: POIInfo,
        centroid: Optional[Location],
        edge_day: bool,
    ) -> float:
        tags = self._attraction_experience_tags(poi)
        tag = sorted(tags)[0] if tags else "other"
        score = self._experience_quality_score(request, poi, tag)
        if centroid is not None:
            distance = min(200.0, self._distance_km(centroid, poi.location))
            score -= distance * (0.8 if edge_day else 0.2)
        return score

    def _apply_verified_poi(self, attraction: Attraction, poi: POIInfo) -> None:
        attraction.name = poi.name
        attraction.address = poi.address
        attraction.location = poi.location.model_copy(deep=True)
        attraction.poi_id = poi.id
        attraction.rating = poi.rating
        safe_photos = [
            url for url in poi.photos
            if isinstance(url, str)
            and len(url) <= 2048
            and url.lower().startswith(("https://", "http://"))
        ][:10]
        attraction.photos = safe_photos
        attraction.image_url = safe_photos[0] if safe_photos else None
        categories = [
            item.strip() for item in (poi.type or "").split(";") if item.strip()
        ]
        # Category is also a server-owned fact: it drives diversity checks,
        # visit duration and generated descriptions.
        attraction.category = categories[-1] if categories else (attraction.category or "景点")
        attraction.coordinate_source = "amap_poi"
        # Provider-supplied admin data for destination verification.
        from ..models.schemas import VerificationMeta
        attraction.verification = VerificationMeta(
            cityname=poi.cityname,
            citycode=poi.citycode,
            adname=poi.district,   # district column IS the AMap adname
            adcode=poi.adcode,
        )
        # Model-written descriptions, duration and ticket prices are not facts
        # supplied by the POI provider. Recompute neutral content downstream.
        attraction.description = ""
        attraction.visit_duration = 0
        attraction.ticket_price = 0

    def _finalize_generated_content(self, request: TripRequest, trip_plan: TripPlan) -> TripPlan:
        gentle_pacing = self._needs_gentle_pacing(request)
        evening_before = self._is_evening_before_departure(request)
        intercity = bool(
            request.origin_city
            and request.origin_city.strip()
            and request.origin_city.strip() != request.city.strip()
        )
        meal_search_budget = {
            "remaining": min(
                self._MAX_MEAL_POI_SEARCHES,
                max(9, len(trip_plan.days) * 3),
            )
        }
        if gentle_pacing:
            pacing_note = (
                "已按父母/老人同行或轻松出游需求降低行程密度，"
                "每天最多保留2个主景点，并为休息和临时调整预留时间。"
            )
            if pacing_note not in trip_plan.overall_suggestions:
                trip_plan.overall_suggestions = (
                    f"{trip_plan.overall_suggestions.rstrip()} {pacing_note}"
                ).strip()

        # Last-resort defense only: if the model still returns extra days after
        # parse/normalize, trim and surface a quality-visible marker. Prefer
        # prompt + normalize_plan_dates to prevent this path.
        if len(trip_plan.days) > request.travel_days:
            trip_plan.days = trip_plan.days[: request.travel_days]
            crop_note = (
                f"【系统防御】规划输出天数超过请求的{request.travel_days}天，"
                "已截断多余日期；请重新生成以获得完整一致的预算与摘要。"
            )
            if crop_note not in (trip_plan.overall_suggestions or ""):
                trip_plan.overall_suggestions = (
                    f"{(trip_plan.overall_suggestions or '').rstrip()} {crop_note}"
                ).strip()
            # Drop budget so stale multi-day totals cannot survive a crop.
            trip_plan.budget = None

        for index, day in enumerate(trip_plan.days):
            day.transportation = request.transportation
            day.accommodation = request.accommodation
            is_first = index == 0
            is_last = index == len(trip_plan.days) - 1
            attraction_cap = 2 if gentle_pacing else 3
            if intercity and is_first:
                # First day reserves intercity time. Friday-evening arrivals stay lightest.
                if evening_before and request.travel_days >= 3:
                    attraction_cap = 1
                else:
                    attraction_cap = min(2, attraction_cap)
            if intercity and is_last:
                attraction_cap = min(attraction_cap, 2)
            if len(day.attractions) > attraction_cap:
                day.attractions = day.attractions[:attraction_cap]
            if gentle_pacing and len(day.attractions) > 2:
                day.attractions = day.attractions[:2]
            durations = [
                max(0, int(attraction.visit_duration or 0))
                for attraction in day.attractions
            ]
            use_suggested_durations = len(durations) > 1 and len(set(durations)) == 1
            for attraction in day.attractions:
                attraction.visit_duration = self._normalized_visit_duration(
                    attraction,
                    force_suggested=use_suggested_durations,
                )
                if self._needs_generated_attraction_description(attraction, request.city):
                    attraction.description = self._build_attraction_description(request, attraction)

            # Always rebuild the day summary from final verified POIs. This
            # prevents removed model places from surviving in narrative text.
            names = [item.name for item in day.attractions[:3] if item.name]
            if names:
                joined_names = "、".join(names)
                day.description = (
                    f"第{day.day_index + 1}天围绕{joined_names}展开"
                    "，整体节奏以顺路游览和减少折返为主。"
                )
            else:
                day.description = (
                    f"第{day.day_index + 1}天尚无通过地图校验的景点，"
                    "请重新生成后再使用。"
                )
            if is_first and intercity:
                arrival_note = (
                    "周五下午或傍晚抵达后先办理入住并休息，傍晚仅安排酒店周边轻量活动。"
                    if evening_before and request.travel_days >= 3
                    else "上午或中午抵达后先办理入住并休息，根据实际到达时间安排一个轻量活动。"
                )
                if arrival_note not in (day.description or ""):
                    day.description = f"{day.description.rstrip()} {arrival_note}".strip()

            day.meals = self._finalize_day_meals(
                request,
                day,
                meal_search_budget,
            )
        return trip_plan

    def _is_evening_before_departure(self, request: TripRequest) -> bool:
        if request.departure_mode == "evening_before":
            return True
        # Never the advisory 【抵达建议】 line: on a default weekend card it says
        # "建议周五下午或傍晚出发" while the user chose the Saturday start.
        text = decided_constraint_text(request.free_text_input)
        if "evening_before" in text or "周五—周日" in text or "周五提前" in text:
            return True
        if re.search(r"周五.{0,6}(?:下午|傍晚|晚上)", text):
            return True
        return False

    def _needs_gentle_pacing(self, request: TripRequest) -> bool:
        # Structured channel first (S4a): the server-built contract's pace
        # binding is the authoritative decided value.
        contract = getattr(request, "semantic_contract", None)
        pace = getattr(contract, "pace", None) if contract is not None else None
        if pace is not None and pace.is_known():
            return str(pace.value or "") == "轻松"
        # Machine-block / preference keyword channel — kept as the compat
        # fallback until the token handoff (S4b/S4c) fully replaces it.
        text = (
            f"{decided_constraint_text(request.free_text_input)} "
            f"{' '.join(request.preferences)}"
        )
        return any(
            keyword in text
            for keyword in (
                "父母", "爸妈", "老人", "长辈", "不想太累",
                "轻松", "慢一点", "休闲", "避暑",
            )
        )

    def _normalized_visit_duration(self, attraction: Attraction, force_suggested: bool = False) -> int:
        current = max(0, int(attraction.visit_duration or 0))
        suggested = self._suggest_visit_duration_minutes(attraction)
        if force_suggested or current <= 0 or current == 120:
            return suggested
        return max(30, min(480, current))

    def _suggest_visit_duration_minutes(self, attraction: Attraction) -> int:
        text = f"{attraction.name or ''} {attraction.category or ''}"
        name = (attraction.name or "").strip()
        category = (attraction.category or "").strip()
        if any(keyword in text for keyword in ("迪士尼", "欢乐谷", "主题乐园", "游乐园", "度假区")):
            return 360
        if (
            any(keyword in text for keyword in ("长城", "大型景区", "风景区", "名胜区", "动物园", "海洋馆"))
            or category in {"山", "山岳", "山峰"}
            or name.endswith(("山", "峰"))
        ):
            return 210
        if any(keyword in text for keyword in ("博物馆", "美术馆", "艺术馆", "展览馆", "科技馆", "纪念馆")):
            return 150
        if any(keyword in text for keyword in ("古镇", "古城", "园林", "公园", "湖", "湿地", "海滩", "沙滩", "植物园")):
            return 120
        if any(keyword in text for keyword in ("步行街", "老街", "街区", "商圈", "夜市", "集市")):
            return 90
        if any(keyword in text for keyword in ("寺", "庙", "祠", "塔", "宫", "府", "城墙", "遗址", "教堂")):
            return 75
        if any(keyword in text for keyword in ("商场", "观景台", "广场")):
            return 75
        return 90

    def _needs_generated_attraction_description(self, attraction: Attraction, city: str) -> bool:
        text = (attraction.description or "").strip()
        if not text or len(text) < 10:
            return True
        placeholders = (
            "景点详细描述",
            "著名景点",
            "景点介绍",
            "值得一游",
            f"这是{city}的著名景点",
        )
        return any(token in text for token in placeholders)

    def _build_attraction_description(self, request: TripRequest, attraction: Attraction) -> str:
        category_text = f"{attraction.name or ''} {attraction.category or ''}"
        area = self._short_place_label(attraction.address or request.city)
        preference_note = self._preference_note(request.preferences, category_text)

        if any(keyword in category_text for keyword in ("博物馆", "美术馆", "艺术馆", "展览馆", "科技馆", "纪念馆")):
            experience = "以展览、馆藏和室内参观为主，适合系统了解相关文化内容"
        elif any(keyword in category_text for keyword in ("公园", "园林", "湖", "湿地", "海滩", "沙滩", "植物园")):
            experience = "以户外景观和步行游览为主，适合散步、拍照并调节当天节奏"
        elif any(keyword in category_text for keyword in ("古镇", "古城", "老街", "步行街", "街区", "城墙")):
            experience = "适合边走边看，体验当地街区肌理和城市风貌"
        elif any(keyword in category_text for keyword in ("寺", "庙", "祠", "塔", "宫", "府", "遗址", "教堂")):
            experience = "以历史建筑和人文景观为主，适合了解当地历史脉络"
        elif any(keyword in category_text for keyword in ("主题乐园", "游乐园", "动物园", "海洋馆")):
            experience = "项目和游览区域较多，适合安排较完整的半日体验"
        else:
            category = (attraction.category or "城市游览").strip()
            experience = f"以{category}体验为主，适合作为当天线路中的一处停留点"

        pieces = [f"{attraction.name}位于{area}", experience]
        if preference_note:
            pieces.append(preference_note)
        return "，".join(pieces) + "。"

    def _preference_note(self, preferences: List[str], text: str) -> str:
        if not preferences:
            return "整体游览压力较小，适合与周边景点顺路组合"
        if "历史文化" in preferences and any(keyword in text for keyword in ("博物馆", "古镇", "古城", "遗址", "寺", "庙", "祠", "塔", "宫", "府", "城墙")):
            return "与本次历史文化偏好匹配度较高"
        if "艺术" in preferences and any(keyword in text for keyword in ("美术馆", "艺术馆", "展览馆")):
            return "适合作为艺术向行程的重点停留点"
        if "自然风光" in preferences and any(keyword in text for keyword in ("公园", "园林", "湖", "湿地", "海滩", "山")):
            return "适合安排自然风光和轻松漫步体验"
        if "休闲" in preferences and any(keyword in text for keyword in ("公园", "街区", "步行街", "商圈", "湖")):
            return "适合作为轻松游览和补给休息的一段"
        if "购物" in preferences and any(keyword in text for keyword in ("商场", "商圈", "步行街", "街区")):
            return "便于串联逛街、补给和城市漫步"
        return f"与本次{'、'.join(preferences[:2])}偏好基本匹配"

    def _needs_generated_day_description(self, description: str) -> bool:
        text = (description or "").strip()
        if not text:
            return True
        return bool(re.fullmatch(r"第\d+天行程", text))

    def _finalize_day_meals(
        self,
        request: TripRequest,
        day: DayPlan,
        search_budget: Optional[Dict[str, int]] = None,
    ) -> List[Meal]:
        if not self._needs_generated_meals(day.meals):
            return [self._normalize_existing_meal(meal) for meal in day.meals]
        return self._recommend_day_meals(request, day, search_budget)

    def _needs_generated_meals(self, meals: List[Meal]) -> bool:
        required_types = {"breakfast", "lunch", "dinner"}
        by_type = {meal.type: meal for meal in meals if meal.type in required_types}
        if set(by_type) != required_types:
            return True

        for meal in by_type.values():
            name = (meal.name or "").strip()
            description = (meal.description or "").strip()
            if re.fullmatch(r"第\d+天(早餐|午餐|晚餐)", name):
                return True
            if name in {"早餐推荐", "午餐推荐", "晚餐推荐"}:
                return True
            if description in {"当地特色早餐", "午餐推荐", "晚餐推荐", "早餐描述", "午餐描述", "晚餐描述"}:
                return True

        # Verification fields are server-owned. Model-written addresses and
        # coordinates never make a restaurant trusted by themselves.
        return not all(
            meal.address
            and meal.location
            and meal.poi_id
            and meal.coordinate_source == "amap_poi"
            for meal in by_type.values()
        )

    def _normalize_existing_meal(self, meal: Meal) -> Meal:
        normalized = meal.model_copy(deep=True) if hasattr(meal, 'model_copy') else meal.copy(deep=True)
        normalized.estimated_cost = max(0, int(normalized.estimated_cost or 0))
        if not normalized.description:
            normalized.description = f"适合作为{self._meal_label(normalized.type)}安排"
        return normalized

    def _recommend_day_meals(
        self,
        request: TripRequest,
        day: DayPlan,
        search_budget: Optional[Dict[str, int]] = None,
    ) -> List[Meal]:
        breakfast_center = self._meal_anchor(day, 0, prefer_hotel=True)
        lunch_center = self._meal_anchor(day, 1)
        dinner_center = self._meal_anchor(day, -1)
        used_ids: set[str] = set()

        return [
            self._pick_meal_candidate(
                request, day, "breakfast", breakfast_center, used_ids, search_budget
            ),
            self._pick_meal_candidate(
                request, day, "lunch", lunch_center, used_ids, search_budget
            ),
            self._pick_meal_candidate(
                request, day, "dinner", dinner_center, used_ids, search_budget
            ),
        ]

    def _meal_anchor(self, day: DayPlan, attraction_index: int, prefer_hotel: bool = False) -> Location:
        if prefer_hotel and day.hotel and day.hotel.location:
            return day.hotel.location.model_copy(deep=True)
        attractions = day.attractions or []
        if attractions:
            if attraction_index < 0:
                resolved_index = len(attractions) - 1
            else:
                resolved_index = min(attraction_index, len(attractions) - 1)
            target = attractions[resolved_index]
            return target.location.model_copy(deep=True)
        if day.hotel and day.hotel.location:
            return day.hotel.location.model_copy(deep=True)
        return Location(longitude=116.397128, latitude=39.916527)

    def _pick_meal_candidate(
        self,
        request: TripRequest,
        day: DayPlan,
        meal_type: str,
        center: Location,
        used_ids: set[str],
        search_budget: Optional[Dict[str, int]] = None,
    ) -> Meal:
        primary_keyword = {
            "breakfast": "早餐",
            "lunch": "餐厅",
            "dinner": "餐厅",
        }.get(meal_type, "餐厅")
        fallback_keywords = {
            "breakfast": [primary_keyword, "早餐店", "小吃"],
            "lunch": [primary_keyword, "中餐厅", "特色菜"],
            "dinner": [primary_keyword, "本地菜", "美食"],
        }.get(meal_type, [primary_keyword, "美食"])

        candidate: Optional[POIInfo] = None
        for keyword in fallback_keywords:
            if search_budget is not None:
                remaining = max(0, int(search_budget.get("remaining", 0)))
                if remaining <= 0:
                    break
                search_budget["remaining"] = remaining - 1
            try:
                candidates = self.amap_service.search_poi_around(
                    keyword,
                    center,
                    radius=3000 if meal_type == "breakfast" else 5000,
                    city=request.city,
                )
            except Exception as exc:
                logger.info(
                    f"[planner] meal POI search failed: {type(exc).__name__}"
                )
                continue
            ranked = sorted(
                enumerate(candidates),
                key=lambda item: self._meal_candidate_score(item[1], meal_type, item[0]),
                reverse=True,
            )
            for _, poi in ranked:
                candidate_key = poi.id or self._normalize_poi_name(poi.name)
                if candidate_key in used_ids or not self._is_food_poi(poi):
                    continue
                candidate = poi
                used_ids.add(candidate_key)
                break
            if candidate is not None:
                break

        if candidate is None:
            reference_name = self._meal_reference_name(day, meal_type)
            return Meal(
                type=meal_type,
                name=f"{reference_name}附近{self._meal_label(meal_type)}",
                description=(
                    "暂未取得可靠的具体商家数据；"
                    f"建议在{reference_name}附近选择高德评分较高、步行距离合适的"
                    f"{self._meal_label(meal_type)}店，并在出发前核对营业时间"
                ),
                estimated_cost=self._estimated_meal_cost(meal_type),
            )

        reference_name = self._meal_reference_name(day, meal_type)
        district = candidate.district or self._short_place_label(candidate.address or request.city)
        details = [f"位于{district}，方便衔接{reference_name}"]
        if candidate.rating:
            details.append(f"高德参考评分{candidate.rating:.1f}")
        details.append("建议出发前核对营业时间和排队情况")
        return Meal(
            type=meal_type,
            name=candidate.name,
            address=candidate.address,
            location=candidate.location.model_copy(deep=True),
            description="；".join(details) + "。",
            estimated_cost=self._estimated_meal_cost(meal_type),
            poi_id=candidate.id,
            coordinate_source="amap_poi",
        )

    def _is_food_poi(self, poi: POIInfo) -> bool:
        text = f"{poi.name or ''} {poi.type or ''}"
        food_markers = (
            "餐饮服务", "餐厅", "餐馆", "饭店", "酒家", "小吃", "快餐",
            "早餐", "面馆", "粉店", "粥店", "火锅", "烧烤", "咖啡", "甜品",
        )
        return any(marker in text for marker in food_markers)

    def _meal_candidate_score(self, poi: POIInfo, meal_type: str, index: int) -> float:
        text = f"{poi.name or ''} {poi.type or ''}"
        meal_markers = {
            "breakfast": ("早餐", "包子", "粥", "面", "粉", "小吃", "快餐"),
            "lunch": ("中餐", "餐厅", "餐馆", "饭店", "本地菜", "特色菜"),
            "dinner": ("中餐", "餐厅", "餐馆", "饭店", "本地菜", "火锅", "烧烤"),
        }.get(meal_type, ("餐厅", "餐馆"))
        type_score = 2.0 if "餐饮服务" in text else 0.0
        meal_score = 1.5 if any(marker in text for marker in meal_markers) else 0.0
        rating_score = max(0.0, float(poi.rating or 0)) * 0.35
        proximity_score = max(0.0, 1.5 - index * 0.08)
        return type_score + meal_score + rating_score + proximity_score

    def _meal_reference_name(self, day: DayPlan, meal_type: str) -> str:
        attractions = day.attractions or []
        if not attractions:
            return "当天行程"
        if meal_type == "breakfast":
            return attractions[0].name
        if meal_type == "lunch":
            return attractions[min(1, len(attractions) - 1)].name
        return attractions[-1].name

    def _meal_label(self, meal_type: str) -> str:
        return {
            "breakfast": "早餐",
            "lunch": "午餐",
            "dinner": "晚餐",
            "snack": "加餐",
        }.get(meal_type, meal_type)

    def _estimated_meal_cost(self, meal_type: str) -> int:
        return {
            "breakfast": 28,
            "lunch": 58,
            "dinner": 88,
            "snack": 25,
        }.get(meal_type, 50)

    def _short_place_label(self, value: str) -> str:
        text = (value or "").strip()
        if not text:
            return "目的地区域"
        if len(text) <= 18:
            return text
        for separator in ("区", "县", "市", "路", "街"):
            index = text.find(separator)
            if 0 < index < 12:
                return text[: index + 1]
        return text[:18]

    def _best_poi_match(
        self,
        name: str,
        candidates: List[POIInfo],
    ) -> tuple[Optional[POIInfo], float]:
        best: Optional[POIInfo] = None
        best_score = 0.0
        target = self._normalize_poi_name(name)
        for candidate in candidates:
            candidate_name = self._normalize_poi_name(candidate.name)
            if not target or not candidate_name:
                continue
            if target == candidate_name:
                score = 1.0
            elif target in candidate_name or candidate_name in target:
                score = 0.88
            else:
                score = SequenceMatcher(None, target, candidate_name).ratio()
            if score > best_score:
                best, best_score = candidate, score
        return best, best_score

    def _normalize_poi_name(self, value: str) -> str:
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", value or "").lower()

    def _select_central_hotel(
        self,
        request: TripRequest,
        trip_plan: TripPlan,
        hotel_pois: List[POIInfo],
    ) -> Optional[Hotel]:
        attractions = [
            attraction
            for day in trip_plan.days
            for attraction in day.attractions
            if attraction.coordinate_source == "amap_poi"
        ]
        if not attractions:
            return None
        center = Location(
            longitude=sum(item.location.longitude for item in attractions) / len(attractions),
            latitude=sum(item.location.latitude for item in attractions) / len(attractions),
        )
        try:
            nearby = self.amap_service.search_poi_around(
                "酒店",
                center,
                radius=12000,
                city=request.city,
            )
        except Exception as exc:
            logger.info(
                f"[planner] nearby hotel search failed: {type(exc).__name__}"
            )
            nearby = []
        candidates = [
            poi
            for poi in self._merge_pois(nearby, hotel_pois)
            if self._is_suitable_hotel_poi(request, poi)
        ]
        ranked: List[tuple[float, float, POIInfo]] = []
        for poi in candidates:
            distances = [
                self._distance_km(poi.location, attraction.location)
                for attraction in attractions
            ]
            if not distances:
                continue
            average = sum(distances) / len(distances)
            maximum = max(distances)
            rating = poi.rating or 3.8
            preference_penalty = self._hotel_preference_penalty(
                request.accommodation,
                poi,
                rating,
            )
            score = average + maximum * 0.2 - rating * 0.8 + preference_penalty
            ranked.append((score, average, poi))
        if not ranked:
            return None

        _, average_distance, selected = min(ranked, key=lambda item: item[0])
        unit_price = {
            "经济型酒店": 180,
            "舒适型酒店": 320,
            "豪华酒店": 680,
            "民宿": 260,
        }.get(request.accommodation, 300)
        return Hotel(
            name=selected.name,
            address=selected.address,
            location=selected.location.model_copy(deep=True),
            price_range=f"参考 {unit_price - 60}-{unit_price + 100} 元/晚",
            rating=f"{selected.rating:.1f}" if selected.rating else "暂无",
            distance=f"距行程景点平均约 {average_distance:.1f} 公里",
            type=request.accommodation,
            estimated_cost=unit_price,
            poi_id=selected.id,
            selection_reason=(
                "基于全部行程景点的平均距离、最远距离、高德评分和住宿偏好综合排序；"
                "优先选择行程中心附近、往返更均衡的住宿。"
            ),
        )

    def _is_suitable_hotel_poi(
        self,
        request: TripRequest,
        poi: POIInfo,
    ) -> bool:
        text = f"{poi.name or ''} {poi.type or ''}"
        lodging_markers = (
            "住宿服务", "酒店", "宾馆", "旅馆", "旅舍", "客栈", "民宿", "度假村"
        )
        if not any(marker in text for marker in lodging_markers):
            return False
        if request.accommodation == "经济型酒店" and any(
            marker in text
            for marker in ("青年旅舍", "青年社区", "青旅", "床位")
        ):
            return False
        return True

    def _hotel_preference_penalty(
        self,
        accommodation: str,
        poi: POIInfo,
        rating: float,
    ) -> float:
        text = f"{poi.name} {poi.type}"
        if accommodation == "豪华酒店" and rating < 4.2:
            return 4.0
        if accommodation == "民宿" and not any(word in text for word in ("民宿", "客栈", "公寓")):
            return 2.5
        if accommodation == "经济型酒店" and any(word in text for word in ("度假", "豪华", "国际")):
            return 1.5
        return 0.0

    def _distance_km(self, origin: Location, destination: Location) -> float:
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

    def _build_planner_query(self, request: TripRequest, attractions: str, weather: str, hotels: str = "") -> str:
        """构建行程规划查询"""
        query = f"""请根据以下信息生成{request.city}的{request.travel_days}天旅行计划:

**基本信息:**
- 出发地: {request.origin_city or '未填写'}
- 目的地: {request.city}
- 日期: {request.start_date} 至 {request.end_date}
- 天数: {request.travel_days}天
- 人数: {request.travelers}人
- 总预算: {f'{request.budget}元' if request.budget else '未设置'}
- 城际交通: {request.intercity_transportation or '自动选择'}
- 市内交通: {request.transportation}
- 住宿: {request.accommodation}
- 偏好: {', '.join(request.preferences) if request.preferences else '无'}

**景点信息:**
{attractions}

**天气信息:**
{weather}

**酒店信息:**
{hotels}

**要求:**
1. 每天安排2-3个景点，只能从上方“高德景点搜索结果”中选择，不得编造景点名称或坐标，整个行程不得重复使用同一景点
2. 每天必须包含早中晚三餐
3. 每天推荐一个具体的酒店(从酒店信息中选择)
4. 考虑景点之间的距离和交通方式
5. 返回完整的JSON格式数据
6. 景点的经纬度坐标要真实准确
7. 如果用户设置了总预算,酒店、餐饮、交通和门票估算应尽量控制在该预算内,并在budget.total中体现
8. weather_info只能填写天气来源中与{request.start_date}至{request.end_date}日期完全匹配的数据；如果天气来源日期不匹配,weather_info返回空数组,并在overall_suggestions提示出发前复核天气
9. 如果出发地与目的地不同，首日和末日必须为城际往返预留时间，不得按两个完整游玩日排满
10. 如果额外要求含父母、老人、轻松、休闲或避暑，每天最多安排2个主景点，避免高强度徒步、连续爬坡和频繁换乘
11. 景点类型必须多样化，商业街区或步行街不超过全部景点的三分之一，且不得选择住宅、汽车服务、产业园或带门店后缀的弱旅游POI
12. 博物馆、美术馆和展馆合计最多3个，公园和绿道合计最多4个；优先选择知名景区、城市地标和与用户偏好强相关的地点，避免用小型附属设施凑数
13. days 数组长度必须恰好为 {request.travel_days}，禁止多生成或少生成天数
"""
        if request.departure_mode == "evening_before" and request.travel_days >= 3:
            query += (
                "\n14. 用户已确认周五下午/傍晚提前出发：第1天仅安排城际抵达、入住与酒店周边轻量活动，"
                "不得排满主要景点；第2天再安排主体游览。"
            )
        elif request.travel_days <= 2:
            query += (
                "\n14. 本次为两日行程：不得额外生成周五 Day0 或第三天；"
                "若额外要求中仅有“建议周五下午抵达”，那只是建议，不要增加行程天数。"
            )
        if request.free_text_input:
            query += f"\n**额外要求:** {request.free_text_input}"
        if request.early_arrival_hint and request.departure_mode != "evening_before":
            query += (
                f"\n**抵达建议（非正式行程日）:** {request.early_arrival_hint}"
            )

        return query

    def _normalize_plan_dates_and_weather(
        self,
        request: TripRequest,
        trip_plan: TripPlan,
        _source_weather_text: str = "",
        source_weather: Optional[List[WeatherInfo]] = None
    ) -> TripPlan:
        """Keep generated dates and weather aligned with the user's requested trip dates."""
        trip_plan.city = request.city
        trip_plan.start_date = request.start_date
        trip_plan.end_date = request.end_date

        if len(trip_plan.days) > request.travel_days:
            # Defense only — prefer failing generation constraints over silent crop.
            # Keep truncation + marker so quality can flag the defensive path.
            trip_plan.days = trip_plan.days[: request.travel_days]
            crop_note = (
                f"【系统防御】规划输出天数超过请求的{request.travel_days}天，已截断。"
            )
            if crop_note not in (trip_plan.overall_suggestions or ""):
                trip_plan.overall_suggestions = (
                    f"{(trip_plan.overall_suggestions or '').rstrip()} {crop_note}"
                ).strip()
            trip_plan.budget = None
        elif len(trip_plan.days) < request.travel_days:
            raise ValueError(
                f"planner returned {len(trip_plan.days)} days for a "
                f"{request.travel_days}-day request"
            )

        trip_dates = self._request_date_list(request)
        for index, day in enumerate(trip_plan.days):
            day.day_index = index
            if index < len(trip_dates):
                day.date = trip_dates[index]

        requested_date_set = set(trip_dates)
        # Weather is a volatile fact. Never trust model-written weather here;
        # only preserve records returned by the configured weather services.
        aligned_weather = self._align_weather_from_source(
            trip_dates, source_weather or []
        )
        aligned_weather = self._dedupe_weather_by_date(
            aligned_weather, requested_date_set
        )
        trip_plan.weather_info = aligned_weather
        if len(aligned_weather) < len(trip_dates):
            covered_dates = {self._extract_iso_date(item.date) for item in aligned_weather}
            missing_dates = [date for date in trip_dates if date not in covered_dates]
            note = (
                f"天气预报当前未完整覆盖{request.start_date}至{request.end_date}的行程日期"
                f"（缺少{', '.join(missing_dates)}），"
                "请在出发前3-7天再次复核每日天气。"
            )
            if note not in trip_plan.overall_suggestions:
                trip_plan.overall_suggestions = f"{trip_plan.overall_suggestions} {note}".strip()

        return trip_plan

    def _align_weather_from_source(
        self,
        trip_dates: List[str],
        source_weather: List[WeatherInfo]
    ) -> List[WeatherInfo]:
        if not trip_dates or not source_weather:
            return []

        weather_by_date = {}
        for item in source_weather:
            normalized_date = self._extract_iso_date(item.date)
            if normalized_date and normalized_date not in weather_by_date:
                weather_by_date[normalized_date] = item

        aligned = []
        for trip_date in trip_dates:
            item = weather_by_date.get(trip_date)
            if item is None:
                continue
            copied = item.model_copy(deep=True) if hasattr(item, "model_copy") else item.copy(deep=True)
            copied.date = trip_date
            aligned.append(copied)
        return aligned

    def _dedupe_weather_by_date(
        self,
        weather_items: List[WeatherInfo],
        requested_date_set: set
    ) -> List[WeatherInfo]:
        unique = []
        seen = set()
        for item in weather_items:
            normalized_date = self._extract_iso_date(item.date)
            if normalized_date not in requested_date_set or normalized_date in seen:
                continue
            item.date = normalized_date
            seen.add(normalized_date)
            unique.append(item)
        return unique

    def _request_date_list(self, request: TripRequest) -> List[str]:
        try:
            start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        except ValueError:
            return []
        return [
            (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(request.travel_days)
        ]

    def _extract_iso_date(self, value: str) -> str:
        text = str(value or "").strip()
        if len(text) >= 10:
            candidate = text[:10]
            try:
                return datetime.strptime(candidate, "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                pass
        return text

    def _parse_response(self, response: str, request: TripRequest) -> TripPlan:
        """
        解析Agent响应

        Args:
            response: Agent响应文本
            request: 原始请求

        Returns:
            旅行计划
        """
        try:
            # 尝试从响应中提取JSON
            # 查找JSON代码块
            if "```json" in response:
                json_start = response.find("```json") + 7
                json_end = response.find("```", json_start)
                json_str = response[json_start:json_end].strip()
            elif "```" in response:
                json_start = response.find("```") + 3
                json_end = response.find("```", json_start)
                json_str = response[json_start:json_end].strip()
            elif "{" in response and "}" in response:
                # 直接查找JSON对象
                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                json_str = response[json_start:json_end]
            else:
                raise ValueError("响应中未找到JSON数据")

            # 解析JSON
            data = json.loads(json_str)

            # 转换为TripPlan对象
            trip_plan = TripPlan(**data)

            return trip_plan

        except Exception as e:
            logger.info(f"[planner] response parsing failed: {type(e).__name__}")
            raise ValueError("planner response is not a valid trip plan") from e

    def _apply_budget_estimate(self, request: TripRequest, trip_plan: TripPlan) -> TripPlan:
        """Replace model-written costs with a server-calculated budget."""
        # Budget provenance is server-owned. Clear the model value before the
        # service call so a provider failure can never expose invented costs.
        trip_plan.budget = None
        try:
            logger.info("[planner] applying budget estimate...")
            budget = self.budget_service.estimate_budget(request, trip_plan)
            if budget is None:
                raise ValueError("budget service returned no estimate")
            trip_plan.budget = budget
            logger.info(
                "[planner] budget ready: "
                f"total={budget.total}, "
                f"hotel={budget.total_hotels}, "
                f"transport={budget.total_transportation}"
            )
        except Exception as exc:
            logger.info(
                "[planner] budget estimate failed: %s: %s",
                type(exc).__name__, exc,
            )
            # Let the graph record a partial-enrichment quality warning while
            # preserving the plan with budget=None.
            raise
        return trip_plan

    def _apply_route_planning(self, request: TripRequest, trip_plan: TripPlan) -> TripPlan:
        """Call AMap route planning for each adjacent pair of attractions in every day."""
        route_type = self._route_type_for_request(request)
        total_routes = 0
        attempted_routes = 0
        max_segments = max(0, int(self.settings.amap_route_max_segments))
        timeout = max(1, int(self.settings.amap_route_timeout))

        try:
            logger.info(
                "[planner] applying route planning: "
                f"route_type={route_type}, request_timeout={timeout}s, max_segments={max_segments}"
            )
            for day in trip_plan.days:
                routes: List[RouteSegment] = []
                attractions = day.attractions or []
                for origin, destination in zip(attractions, attractions[1:]):
                    if attempted_routes >= max_segments:
                        logger.info(f"[planner] route planning skipped: reached max_segments={max_segments}")
                        break
                    logger.info(f"[planner] planning route: {origin.name} -> {destination.name}")
                    attempted_routes += 1
                    segment = self._plan_route_segment(request, origin, destination, route_type, timeout)
                    if segment:
                        routes.append(segment)
                        total_routes += 1
                day.routes = routes
            logger.info(f"[planner] route planning ready: segments={total_routes}, attempted={attempted_routes}")
        except Exception as e:
            logger.info(f"[planner] route planning failed: {type(e).__name__}")

        return trip_plan

    def _plan_route_segment(
        self,
        request: TripRequest,
        origin: Attraction,
        destination: Attraction,
        route_type: str,
        timeout: int
    ) -> Optional[RouteSegment]:
        origin_address = origin.address or origin.name
        destination_address = destination.address or destination.name
        resolved_route_type = route_type
        if (
            route_type == "transit"
            and self._distance_km(origin.location, destination.location) <= 1.2
        ):
            resolved_route_type = "walking"

        data = self.amap_service.plan_route(
            origin_address=origin_address,
            destination_address=destination_address,
            origin_city=request.city,
            destination_city=request.city,
            route_type=resolved_route_type,
            timeout=timeout,
            origin_location=origin.location,
            destination_location=destination.location,
        )
        if not data:
            return None
        distance = self._first_number_by_keys(data, ["distance", "walk_distance", "total_distance"])
        duration = int(self._first_number_by_keys(data, ["duration", "time", "cost_time"]))
        if distance <= 0 and duration <= 0:
            return None
        path = self._extract_route_path(data)
        description = self._route_description(
            data, origin.name, destination.name, resolved_route_type
        )

        return RouteSegment(
            from_name=origin.name,
            to_name=destination.name,
            origin_address=origin_address,
            destination_address=destination_address,
            route_type=resolved_route_type,
            distance=distance,
            duration=duration,
            description=description,
            path=path,
            source="amap_route",
            verified=len(path) >= 2,
        )

    def _extract_route_path(self, data: Any) -> List[Location]:
        route = data.get("route") if isinstance(data, dict) else None
        if not isinstance(route, dict):
            return []
        options = route.get("paths") or route.get("transits") or []
        root = options[0] if isinstance(options, list) and options else route
        path: List[Location] = []

        def append_polyline(value: Any) -> None:
            if not isinstance(value, str):
                return
            for pair in value.split(";"):
                parts = pair.split(",")
                if len(parts) < 2:
                    continue
                try:
                    point = Location(longitude=float(parts[0]), latitude=float(parts[1]))
                except (TypeError, ValueError):
                    continue
                if not path or (
                    path[-1].longitude != point.longitude
                    or path[-1].latitude != point.latitude
                ):
                    path.append(point)

        def visit(value: Any) -> None:
            if len(path) >= 2000:
                return
            if isinstance(value, dict):
                append_polyline(value.get("polyline"))
                for key, item in value.items():
                    if key != "polyline":
                        visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(root)
        return path

    def _route_type_for_request(self, request: TripRequest) -> str:
        text = f"{request.transportation or ''} {request.free_text_input or ''}".lower()
        if any(keyword in text for keyword in ("自驾", "驾车", "开车", "driving", "drive")):
            return "driving"
        if any(keyword in text for keyword in ("步行", "徒步", "walking", "walk")):
            return "walking"
        return "transit"

    def _first_number_by_keys(self, data: Any, keys: List[str]) -> float:
        value = self._first_value_by_keys(data, keys)
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return float(value)
        import re
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else 0

    def _first_value_by_keys(self, data: Any, keys: List[str]):
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if value not in (None, "", [], {}):
                    return value
            for value in data.values():
                found = self._first_value_by_keys(value, keys)
                if found not in (None, "", [], {}):
                    return found
        elif isinstance(data, list):
            for item in data:
                found = self._first_value_by_keys(item, keys)
                if found not in (None, "", [], {}):
                    return found
        return None

    def _route_description(self, data: Any, origin_name: str, destination_name: str, route_type: str) -> str:
        instructions = self._collect_route_instructions(data)
        if instructions:
            return "；".join(instructions[:5])

        route_label = {
            "walking": "步行",
            "driving": "驾车",
            "transit": "公共交通"
        }.get(route_type, route_type)
        return f"从{origin_name}前往{destination_name}，建议使用{route_label}衔接。"

    def _collect_route_instructions(self, data: Any) -> List[str]:
        instructions: List[str] = []
        keys = {"instruction", "assistant_action", "action", "name"}

        def visit(value: Any) -> None:
            if len(instructions) >= 5:
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in keys and isinstance(item, str):
                        normalized = item.strip()
                        if normalized and normalized not in instructions:
                            instructions.append(normalized)
                            if len(instructions) >= 5:
                                return
                for item in value.values():
                    visit(item)
                    if len(instructions) >= 5:
                        return
            elif isinstance(value, list):
                for item in value:
                    visit(item)
                    if len(instructions) >= 5:
                        return
            elif isinstance(value, str) and not instructions:
                normalized = value.strip()
                if normalized:
                    instructions.append(normalized[:160])

        visit(data)
        return instructions

    def _apply_web_guide(self, request: TripRequest, trip_plan: TripPlan) -> TripPlan:
        """Attach web-enhanced guide and audit output."""
        try:
            logger.info("[planner] generating web travel guide...")
            trip_plan = self.web_guide_agent.apply_to_plan(request, trip_plan)
            audit_status = trip_plan.agent_audit.status if trip_plan.agent_audit else "unknown"
            logger.info(f"[planner] web guide ready: audit={audit_status}")
        except Exception as exc:
            error_type = type(exc).__name__
            logger.info(f"[planner] web guide failed: {error_type}")
            trip_plan.agent_audit = AgentAuditResult(
                status="warning",
                source="local_fallback",
                checked_items=["联网攻略阶段执行状态"],
                issues=[f"联网攻略阶段异常，未能完成公开信息核对（{error_type}）。"],
                suggestions=["保留结构化行程，出发前人工复核预约、天气、票务和交通。"],
            )
        return trip_plan

    def _create_fallback_plan(
        self,
        request: TripRequest,
        attraction_pois: Optional[List[POIInfo]] = None,
    ) -> TripPlan:
        """Build a deterministic degraded plan using only verified POIs.

        If the map provider supplied too few POIs, affected days intentionally
        remain empty so the quality gate blocks persistence and delivery.
        """
        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        unique_pois: List[POIInfo] = []
        seen_ids: set[str] = set()
        for poi in attraction_pois or []:
            if not self._is_suitable_attraction_poi(poi):
                continue
            if poi.id and poi.id not in seen_ids:
                seen_ids.add(poi.id)
                unique_pois.append(poi)
        # Keep this deterministic safety net even when callers provide POIs
        # directly instead of going through _search_attractions.
        unique_pois = self._cap_repetitive_experiences(request, unique_pois)

        days: List[DayPlan] = []
        cursor = 0
        for index in range(request.travel_days):
            remaining_days = request.travel_days - index
            remaining_pois = len(unique_pois) - cursor
            allocation = min(2, max(0, remaining_pois - max(0, remaining_days - 1)))
            selected = unique_pois[cursor:cursor + allocation]
            cursor += allocation
            attractions = [
                Attraction(
                    name=poi.name,
                    address=poi.address,
                    location=poi.location.model_copy(deep=True),
                    visit_duration=120,
                    description="",
                    category=poi.type or "景点",
                    rating=poi.rating,
                    photos=list(poi.photos),
                    poi_id=poi.id,
                    image_url=poi.photos[0] if poi.photos else None,
                    coordinate_source="amap_poi",
                    ticket_price=0,
                )
                for poi in selected
            ]
            days.append(
                DayPlan(
                    date=(start_date + timedelta(days=index)).strftime("%Y-%m-%d"),
                    day_index=index,
                    description=f"第{index + 1}天地图可信地点备选",
                    transportation=request.transportation,
                    accommodation=request.accommodation,
                    attractions=attractions,
                    meals=[
                        Meal(type="breakfast", name="早餐待现场确认"),
                        Meal(type="lunch", name="午餐待现场确认"),
                        Meal(type="dinner", name="晚餐待现场确认"),
                    ],
                )
            )

        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            generation_mode="map_fallback",
            days=days,
            weather_info=[],
            overall_suggestions=(
                "主规划暂不可用。本方案只保留地图服务已确认的地点；"
                "若存在空白日期，质量检查会阻止自动保存和发送。"
            ),
        )



# 全局多智能体系统实例
_multi_agent_planner = None
_multi_agent_planner_lock = threading.Lock()


def get_trip_planner_agent() -> MultiAgentTripPlanner:
    """获取多智能体旅行规划系统实例(单例模式)"""
    global _multi_agent_planner

    if _multi_agent_planner is None:
        with _multi_agent_planner_lock:
            if _multi_agent_planner is None:
                _multi_agent_planner = MultiAgentTripPlanner()

    return _multi_agent_planner


def planner_is_initialized() -> bool:
    """Whether the planner singleton already exists, without constructing it.

    Lets a public readiness probe report state without paying — or triggering —
    the expensive first-call initialisation.
    """
    return _multi_agent_planner is not None


def shutdown_trip_planner_agent() -> None:
    """Drop service references that are closed during application shutdown."""
    global _multi_agent_planner
    with _multi_agent_planner_lock:
        _multi_agent_planner = None
