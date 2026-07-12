"""多智能体旅行规划系统"""

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from hello_agents import SimpleAgent
from ..services.llm_service import get_llm
from ..services.transport_budget_service import get_transport_budget_service
from ..services.amap_service import get_amap_service
from ..models.schemas import (
    TripRequest, TripPlan, DayPlan, Attraction, Meal, WeatherInfo,
    Location, Hotel, RouteSegment, POIInfo
)
from ..config import get_settings
from .web_travel_guide_agent import get_web_travel_guide_agent

# ============ Agent提示词 ============

ATTRACTION_AGENT_PROMPT = "你是景点搜索专家。景点数据由高德 Web Service 直接提供。"

WEATHER_AGENT_PROMPT = "你是天气查询专家。天气数据由高德 Web Service 直接提供。"

HOTEL_AGENT_PROMPT = "你是酒店推荐专家。酒店数据由高德 Web Service 直接提供。"

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
          "visit_duration": 120,
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
1. weather_info数组只能使用天气查询结果中明确覆盖旅行日期的数据；如果旅行日期在未来7天内,可以使用工具返回的近期预报中日期匹配的数据；如果查询结果不覆盖旅行日期,weather_info必须返回空数组,不要把其他日期天气改写成旅行日期
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
"""


class MultiAgentTripPlanner:
    """多智能体旅行规划系统"""

    def __init__(self):
        """初始化多智能体系统"""
        print("🔄 开始初始化多智能体旅行规划系统...")

        try:
            settings = get_settings()
            self.settings = settings
            self.llm = get_llm()
            self.budget_service = get_transport_budget_service()
            self.web_guide_agent = get_web_travel_guide_agent()
            self.amap_service = get_amap_service()

            # 高德查询由 AmapService 直接调用 HTTP API，避免外部子进程堆积。
            self.amap_tool = None

            # 创建景点搜索Agent
            print("  - 创建景点搜索Agent...")
            self.attraction_agent = SimpleAgent(
                name="景点搜索专家",
                llm=self.llm,
                system_prompt=ATTRACTION_AGENT_PROMPT
            )

            # 创建天气查询Agent
            print("  - 创建天气查询Agent...")
            self.weather_agent = SimpleAgent(
                name="天气查询专家",
                llm=self.llm,
                system_prompt=WEATHER_AGENT_PROMPT
            )

            # 创建酒店推荐Agent
            print("  - 创建酒店推荐Agent...")
            self.hotel_agent = SimpleAgent(
                name="酒店推荐专家",
                llm=self.llm,
                system_prompt=HOTEL_AGENT_PROMPT
            )

            # 创建行程规划Agent(不需要工具)
            print("  - 创建行程规划Agent...")
            self.planner_agent = SimpleAgent(
                name="行程规划专家",
                llm=self.llm,
                system_prompt=PLANNER_AGENT_PROMPT
            )

            print(f"✅ 多智能体系统初始化成功")
            print(f"   景点搜索Agent: {len(self.attraction_agent.list_tools())} 个工具")
            print(f"   天气查询Agent: {len(self.weather_agent.list_tools())} 个工具")
            print(f"   酒店推荐Agent: {len(self.hotel_agent.list_tools())} 个工具")
            print(f"   联网攻略Agent: {self.web_guide_agent.status()['provider']}")

        except Exception as e:
            print(f"❌ 多智能体系统初始化失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise
    
    def plan_trip(self, request: TripRequest) -> TripPlan:
        """
        使用多智能体协作生成旅行计划

        Args:
            request: 旅行请求

        Returns:
            旅行计划
        """
        try:
            print(f"\n{'='*60}")
            print(f"🚀 开始多智能体协作规划旅行...")
            print(f"目的地: {request.city}")
            print(f"日期: {request.start_date} 至 {request.end_date}")
            print(f"天数: {request.travel_days}天")
            print(f"预算: {request.budget if request.budget else '未设置'}")
            print(f"偏好: {', '.join(request.preferences) if request.preferences else '无'}")
            print(f"{'='*60}\n")

            # 步骤1: 直接通过高德 Web Service 搜索景点
            print("📍 步骤1: 搜索景点...")
            attraction_pois = self._search_attractions(request)
            attraction_response = self._format_pois_for_prompt(
                "高德景点搜索结果",
                attraction_pois,
                limit=min(20, max(12, request.travel_days * 3)),
            )
            print(f"景点搜索结果: {attraction_response[:200]}...\n")

            # 步骤2: 直接通过高德 Web Service 查询天气
            print("🌤️ 步骤2: 查询天气...")
            source_weather = self.amap_service.get_weather(
                request.city,
                request.start_date,
                request.end_date
            )
            weather_response = self._format_weather_for_prompt(request, source_weather)
            print(f"天气查询结果: {weather_response[:200]}...\n")

            # 步骤3: 直接通过高德 Web Service 搜索酒店
            print("🏨 步骤3: 搜索酒店...")
            hotel_pois = self._search_hotels(request, attraction_pois)
            hotel_response = self._format_pois_for_prompt("高德酒店搜索结果", hotel_pois, limit=10)
            print(f"酒店搜索结果: {hotel_response[:200]}...\n")

            # 步骤4: 行程规划Agent整合信息生成计划
            print("📋 步骤4: 生成行程计划...")
            planner_query = self._build_planner_query(request, attraction_response, weather_response, hotel_response)
            planner_response = self.planner_agent.run(planner_query)
            print(f"行程规划结果: {planner_response[:300]}...\n")

            # 解析最终计划
            trip_plan = self._parse_response(planner_response, request)
            trip_plan = self._normalize_plan_dates_and_weather(
                request,
                trip_plan,
                weather_response,
                source_weather
            )
            trip_plan = self._ground_trip_plan(
                request,
                trip_plan,
                attraction_pois,
                hotel_pois,
            )

            print(f"{'='*60}")
            print(f"✅ 旅行计划生成完成!")
            print(f"{'='*60}\n")

            trip_plan = self._apply_route_planning(request, trip_plan)
            trip_plan = self._apply_budget_estimate(request, trip_plan)
            return self._apply_web_guide(request, trip_plan)

        except Exception as e:
            print(f"❌ 生成旅行计划失败: {str(e)}")
            import traceback
            traceback.print_exc()
            fallback_plan = self._create_fallback_plan(request)
            fallback_plan = self._normalize_plan_dates_and_weather(request, fallback_plan)
            fallback_plan = self._apply_route_planning(request, fallback_plan)
            fallback_plan = self._apply_budget_estimate(request, fallback_plan)
            return self._apply_web_guide(request, fallback_plan)
    
    def _search_attractions(self, request: TripRequest) -> List[POIInfo]:
        preference_queries = {
            "历史文化": "博物馆",
            "自然风光": "风景区",
            "艺术": "美术馆",
            "休闲": "公园",
            "购物": "特色街区",
        }
        preferred: List[POIInfo] = []
        for preference in request.preferences:
            query = preference_queries.get(preference)
            if query:
                preferred = self._merge_pois(
                    preferred,
                    self.amap_service.search_poi(query, request.city),
                )
        general = self.amap_service.search_poi("景点", request.city)
        return self._merge_pois(preferred, general)

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
        source_pool = list(attraction_pois)
        used_ids: set[str] = set()
        fallback_index = 0
        search_cache: Dict[str, List[POIInfo]] = {}

        for day in trip_plan.days:
            for attraction in day.attractions:
                matched, score = self._best_poi_match(attraction.name, source_pool)
                if score < 0.72:
                    if attraction.name not in search_cache:
                        search_cache[attraction.name] = self.amap_service.search_poi(
                            attraction.name,
                            request.city,
                        )
                    searched, searched_score = self._best_poi_match(
                        attraction.name,
                        search_cache[attraction.name],
                    )
                    if searched is not None and searched_score > score:
                        matched, score = searched, searched_score

                if matched is None or score < 0.45:
                    matched = next(
                        (poi for poi in source_pool if poi.id not in used_ids),
                        None,
                    )
                    if matched is None and source_pool:
                        matched = source_pool[fallback_index % len(source_pool)]
                    fallback_index += 1
                if matched is None:
                    continue
                used_ids.add(matched.id)
                self._apply_verified_poi(attraction, matched)

        selected_hotel = self._select_central_hotel(
            request,
            trip_plan,
            hotel_pois,
        )
        if selected_hotel is not None:
            for day in trip_plan.days:
                day.hotel = selected_hotel.model_copy(deep=True)
        return trip_plan

    def _apply_verified_poi(self, attraction: Attraction, poi: POIInfo) -> None:
        attraction.name = poi.name
        attraction.address = poi.address
        attraction.location = poi.location.model_copy(deep=True)
        attraction.poi_id = poi.id
        attraction.rating = poi.rating
        attraction.photos = list(poi.photos)
        attraction.image_url = poi.photos[0] if poi.photos else attraction.image_url
        attraction.coordinate_source = "amap_poi"

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
        nearby = self.amap_service.search_poi_around(
            "酒店",
            center,
            radius=12000,
            city=request.city,
        )
        candidates = self._merge_pois(nearby, hotel_pois)
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
- 城市: {request.city}
- 日期: {request.start_date} 至 {request.end_date}
- 天数: {request.travel_days}天
- 总预算: {f'{request.budget}元' if request.budget else '未设置'}
- 交通方式: {request.transportation}
- 住宿: {request.accommodation}
- 偏好: {', '.join(request.preferences) if request.preferences else '无'}

**景点信息:**
{attractions}

**天气信息:**
{weather}

**酒店信息:**
{hotels}

**要求:**
1. 每天安排2-3个景点，只能从上方“高德景点搜索结果”中选择，不得编造景点名称或坐标
2. 每天必须包含早中晚三餐
3. 每天推荐一个具体的酒店(从酒店信息中选择)
3. 考虑景点之间的距离和交通方式
4. 返回完整的JSON格式数据
5. 景点的经纬度坐标要真实准确
6. 如果用户设置了总预算,酒店、餐饮、交通和门票估算应尽量控制在该预算内,并在budget.total中体现
7. weather_info只能填写与{request.start_date}至{request.end_date}日期完全匹配的天气；如果旅行日期在未来7天内,可以使用近期预报中日期匹配的数据；如果天气来源日期不匹配,weather_info返回空数组,并在overall_suggestions提示出发前复核天气。
"""
        if request.free_text_input:
            query += f"\n**额外要求:** {request.free_text_input}"

        return query

    def _normalize_plan_dates_and_weather(
        self,
        request: TripRequest,
        trip_plan: TripPlan,
        source_weather_text: str = "",
        source_weather: Optional[List[WeatherInfo]] = None
    ) -> TripPlan:
        """Keep generated dates and weather aligned with the user's requested trip dates."""
        trip_plan.city = request.city
        trip_plan.start_date = request.start_date
        trip_plan.end_date = request.end_date

        trip_dates = self._request_date_list(request)
        for index, day in enumerate(trip_plan.days):
            day.day_index = index
            if index < len(trip_dates):
                day.date = trip_dates[index]

        requested_date_set = set(trip_dates)
        original_weather = trip_plan.weather_info or []
        aligned_weather = self._align_weather_from_source(trip_dates, source_weather or [])

        if not aligned_weather:
            source_covers_trip_dates = self._source_weather_covers_dates(source_weather_text, trip_dates)
            trip_is_near_term = self._trip_dates_within_forecast_window(trip_dates)
            if source_covers_trip_dates or trip_is_near_term or not source_weather_text:
                for weather in original_weather:
                    normalized_date = self._extract_iso_date(weather.date)
                    if normalized_date in requested_date_set:
                        weather.date = normalized_date
                        aligned_weather.append(weather)

        aligned_weather = self._dedupe_weather_by_date(aligned_weather, requested_date_set)
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

    def _source_weather_covers_dates(self, source_weather_text: str, trip_dates: List[str]) -> bool:
        if not source_weather_text or not trip_dates:
            return False
        if any(marker in source_weather_text for marker in ("不覆盖", "无法获取", "不能查询", "暂未查询到", "未查询到", "没有查询到")):
            return False
        return all(
            any(candidate in source_weather_text for candidate in self._date_text_candidates(trip_date))
            for trip_date in trip_dates
        )

    def _trip_dates_within_forecast_window(self, trip_dates: List[str], window_days: int = 7) -> bool:
        if not trip_dates:
            return False
        today = datetime.now().date()
        parsed_dates = []
        for value in trip_dates:
            try:
                parsed_dates.append(datetime.strptime(value, "%Y-%m-%d").date())
            except ValueError:
                return False
        return all(today <= value <= today + timedelta(days=window_days) for value in parsed_dates)

    def _date_text_candidates(self, value: str) -> List[str]:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return [value]
        return [
            parsed.strftime("%Y-%m-%d"),
            f"{parsed.year}-{parsed.month}-{parsed.day}",
            parsed.strftime("%Y/%m/%d"),
            f"{parsed.year}/{parsed.month}/{parsed.day}",
            f"{parsed.month}月{parsed.day}日",
            f"{parsed.month:02d}月{parsed.day:02d}日",
        ]
    
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
            print(f"⚠️  解析响应失败: {str(e)}")
            print(f"   将使用备用方案生成计划")
            return self._create_fallback_plan(request)

    def _apply_budget_estimate(self, request: TripRequest, trip_plan: TripPlan) -> TripPlan:
        """Use the budget service to normalize hotel and transport costs."""
        try:
            print("[planner] applying budget estimate...")
            trip_plan.budget = self.budget_service.estimate_budget(request, trip_plan)
            if trip_plan.budget is not None:
                print(
                    "[planner] budget ready: "
                    f"total={trip_plan.budget.total}, "
                    f"hotel={trip_plan.budget.total_hotels}, "
                    f"transport={trip_plan.budget.total_transportation}"
                )
        except Exception as e:
            print(f"⚠️  预算估算失败: {str(e)}")
        return trip_plan

    def _apply_route_planning(self, request: TripRequest, trip_plan: TripPlan) -> TripPlan:
        """Call AMap route planning for each adjacent pair of attractions in every day."""
        route_type = self._route_type_for_request(request)
        total_routes = 0
        attempted_routes = 0
        max_segments = max(0, int(self.settings.amap_route_max_segments))
        timeout = max(1, int(self.settings.amap_route_timeout))

        try:
            print(
                "[planner] applying route planning: "
                f"route_type={route_type}, request_timeout={timeout}s, max_segments={max_segments}"
            )
            for day in trip_plan.days:
                routes: List[RouteSegment] = []
                attractions = day.attractions or []
                for origin, destination in zip(attractions, attractions[1:]):
                    if attempted_routes >= max_segments:
                        print(f"[planner] route planning skipped: reached max_segments={max_segments}")
                        break
                    print(f"[planner] planning route: {origin.name} -> {destination.name}")
                    attempted_routes += 1
                    segment = self._plan_route_segment(request, origin, destination, route_type, timeout)
                    if segment:
                        routes.append(segment)
                        total_routes += 1
                day.routes = routes
            print(f"[planner] route planning ready: segments={total_routes}, attempted={attempted_routes}")
        except Exception as e:
            print(f"⚠️  路线规划失败: {str(e)}")

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

        data = self.amap_service.plan_route(
            origin_address=origin_address,
            destination_address=destination_address,
            origin_city=request.city,
            destination_city=request.city,
            route_type=route_type,
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
        description = self._route_description(data, origin.name, destination.name, route_type)

        return RouteSegment(
            from_name=origin.name,
            to_name=destination.name,
            origin_address=origin_address,
            destination_address=destination_address,
            route_type=route_type,
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
            print("[planner] generating web travel guide...")
            trip_plan = self.web_guide_agent.apply_to_plan(request, trip_plan)
            audit_status = trip_plan.agent_audit.status if trip_plan.agent_audit else "unknown"
            print(f"[planner] web guide ready: audit={audit_status}")
        except Exception as e:
            print(f"⚠️  联网攻略Agent失败: {str(e)}")
        return trip_plan
    
    def _create_fallback_plan(self, request: TripRequest) -> TripPlan:
        """创建备用计划(当Agent失败时)"""
        from datetime import datetime, timedelta
        
        # 解析日期
        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        
        # 创建每日行程
        days = []
        for i in range(request.travel_days):
            current_date = start_date + timedelta(days=i)
            
            day_plan = DayPlan(
                date=current_date.strftime("%Y-%m-%d"),
                day_index=i,
                description=f"第{i+1}天行程",
                transportation=request.transportation,
                accommodation=request.accommodation,
                attractions=[
                    Attraction(
                        name=f"{request.city}景点{j+1}",
                        address=f"{request.city}市",
                        location=Location(longitude=116.4 + i*0.01 + j*0.005, latitude=39.9 + i*0.01 + j*0.005),
                        visit_duration=120,
                        description=f"这是{request.city}的著名景点",
                        category="景点"
                    )
                    for j in range(2)
                ],
                meals=[
                    Meal(type="breakfast", name=f"第{i+1}天早餐", description="当地特色早餐"),
                    Meal(type="lunch", name=f"第{i+1}天午餐", description="午餐推荐"),
                    Meal(type="dinner", name=f"第{i+1}天晚餐", description="晚餐推荐")
                ]
            )
            days.append(day_plan)
        
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=[],
            overall_suggestions=f"这是为您规划的{request.city}{request.travel_days}日游行程,建议提前查看各景点的开放时间。"
        )


# 全局多智能体系统实例
_multi_agent_planner = None


def get_trip_planner_agent() -> MultiAgentTripPlanner:
    """获取多智能体旅行规划系统实例(单例模式)"""
    global _multi_agent_planner

    if _multi_agent_planner is None:
        _multi_agent_planner = MultiAgentTripPlanner()

    return _multi_agent_planner
