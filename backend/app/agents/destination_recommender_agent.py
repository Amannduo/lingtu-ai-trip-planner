"""目的地推荐对话Agent"""

import json
import re
from typing import Any, Dict, List, Optional

from hello_agents import SimpleAgent

from ..models.schemas import (
    DestinationChatRequest,
    DestinationChatResponse,
    DestinationRecommendation,
    RecommendationFormPatch,
    RecommendationContext
)
from ..services.amap_service import get_amap_service
from ..services.llm_service import get_llm


RECOMMENDER_PROMPT = """你是一个目的地推荐助手。用户还不知道去哪旅行时,你需要根据出发地、预算、天数、偏好、交通和自然语言描述推荐1-3个中国城市。

请只返回JSON,格式如下:
{
  "needs_more_info": false,
  "question": "",
  "candidates": [
    {
      "city": "南京",
      "reason": "历史文化密度高,美食集中,3天节奏合适",
      "suggested_days": 3,
      "preferences": ["历史文化", "美食", "休闲"]
    }
  ]
}

如果信息太少,返回:
{
  "needs_more_info": true,
  "question": "你更偏好自然风光、城市休闲还是历史文化?",
  "candidates": []
}

要求:
1. 推荐城市必须适合用户的出发地、天数和预算。
2. 如果用户提供了出发地,优先推荐周边或交通更便利的城市。
3. 不要推荐过多城市,最多3个,并遵守用户要求的推荐数量。
4. reason要具体,不要空泛。
5. 只返回JSON,不要输出Markdown。"""


FALLBACK_CITY_PROFILES: Dict[str, Dict[str, Any]] = {
    "南京": {
        "reason": "历史文化密度高,景点集中,3天内能兼顾博物馆、老城街区和本地美食。",
        "preferences": ["历史文化", "美食", "休闲"],
        "highlights": ["南京博物院", "中山陵", "夫子庙"]
    },
    "成都": {
        "reason": "餐饮选择丰富,节奏松弛,适合预算可控的城市休闲和美食体验。",
        "preferences": ["美食", "休闲", "历史文化"],
        "highlights": ["宽窄巷子", "武侯祠", "锦里"]
    },
    "杭州": {
        "reason": "自然景观和城市配套均衡,适合轻松行程,公共交通和步行体验较好。",
        "preferences": ["自然风光", "休闲", "艺术"],
        "highlights": ["西湖", "灵隐寺", "良渚古城遗址公园"]
    },
    "西安": {
        "reason": "历史遗迹集中,文化辨识度强,适合把预算集中在重点景区和特色餐饮上。",
        "preferences": ["历史文化", "美食"],
        "highlights": ["秦始皇兵马俑", "大雁塔", "回民街"]
    },
    "厦门": {
        "reason": "海边休闲感强,行程压力小,适合想放松和拍照散步的旅行。",
        "preferences": ["自然风光", "休闲"],
        "highlights": ["鼓浪屿", "厦门大学", "环岛路"]
    },
    "长沙": {
        "reason": "美食和夜生活集中,交通成本相对可控,适合短途高密度体验。",
        "preferences": ["美食", "购物", "休闲"],
        "highlights": ["橘子洲", "岳麓山", "五一广场"]
    },
    "苏州": {
        "reason": "园林、古城和水乡气质突出,适合偏安静的文化休闲行程。",
        "preferences": ["历史文化", "艺术", "休闲"],
        "highlights": ["拙政园", "苏州博物馆", "平江路"]
    },
    "桂林": {
        "reason": "自然风光辨识度高,适合把行程重点放在山水和轻户外体验上。",
        "preferences": ["自然风光", "休闲"],
        "highlights": ["漓江", "象鼻山", "阳朔西街"]
    },
    "上海": {
        "reason": "展览、购物、城市漫步和餐饮选择丰富,适合预算较充足的都市旅行。",
        "preferences": ["购物", "艺术", "美食"],
        "highlights": ["外滩", "上海博物馆", "武康路"]
    }
}


NEARBY_CITY_OPTIONS: Dict[str, List[str]] = {
    "北京": ["天津", "秦皇岛", "济南"],
    "天津": ["北京", "秦皇岛", "济南"],
    "上海": ["苏州", "杭州", "南京"],
    "杭州": ["苏州", "上海", "南京"],
    "苏州": ["上海", "杭州", "南京"],
    "南京": ["苏州", "杭州", "上海"],
    "广州": ["深圳", "珠海", "佛山"],
    "深圳": ["广州", "珠海", "厦门"],
    "成都": ["重庆", "乐山", "西安"],
    "重庆": ["成都", "贵阳", "西安"],
    "西安": ["洛阳", "成都", "重庆"],
    "长沙": ["武汉", "张家界", "南昌"],
    "武汉": ["长沙", "南京", "合肥"],
    "厦门": ["泉州", "福州", "深圳"],
    "福州": ["厦门", "泉州", "杭州"],
    "青岛": ["济南", "烟台", "威海"],
    "济南": ["青岛", "天津", "北京"],
    "郑州": ["洛阳", "西安", "开封"]
}


class DestinationRecommenderAgent:
    """目的地推荐对话系统"""

    def __init__(self):
        self.agent: Optional[SimpleAgent] = None
        try:
            self.agent = SimpleAgent(
                name="目的地推荐助手",
                llm=get_llm(),
                system_prompt=RECOMMENDER_PROMPT
            )
        except Exception as e:
            print(f"⚠️ 目的地推荐LLM初始化失败,将使用规则推荐: {str(e)}")

    def chat(self, request: DestinationChatRequest) -> DestinationChatResponse:
        """根据对话和当前表单上下文推荐目的地"""
        latest_message = self._latest_user_message(request)
        context = request.context
        count = self._recommendation_count(context)

        if self._needs_more_info(latest_message, context):
            return DestinationChatResponse(
                success=True,
                message="需要更多信息",
                reply="你可以告诉我大概预算、旅行天数、喜欢城市休闲还是自然风光,我会给你几个更准确的目的地选择。",
                needs_more_info=True,
                recommendations=[]
            )

        seeds = self._generate_candidate_seeds(request)
        recommendations = [self._build_recommendation(seed, context) for seed in seeds[:count]]
        recommendations = [item for item in recommendations if item is not None]

        if not recommendations:
            recommendations = [
                self._build_recommendation({"city": city, **self._profile_for_city(city)}, context)
                for city in list(FALLBACK_CITY_PROFILES.keys())[:count]
            ]
            recommendations = [item for item in recommendations if item is not None]

        reply = "我按你的预算、天数和偏好筛了几个方向。可以先选一个城市,我会把它回填到旅行计划表单里。"
        return DestinationChatResponse(
            success=True,
            message="推荐成功",
            reply=reply,
            needs_more_info=False,
            recommendations=recommendations
        )

    def _latest_user_message(self, request: DestinationChatRequest) -> str:
        for message in reversed(request.messages):
            if message.role == "user":
                return message.content.strip()
        return ""

    def _needs_more_info(self, latest_message: str, context: RecommendationContext) -> bool:
        has_context = bool(context.budget or context.travel_days or context.preferences)
        return len(latest_message) < 4 and not has_context

    def _recommendation_count(self, context: RecommendationContext) -> int:
        return max(1, min(3, context.recommendation_count or 3))

    def _generate_candidate_seeds(self, request: DestinationChatRequest) -> List[Dict[str, Any]]:
        if self.agent is not None:
            prompt = self._build_ai_prompt(request)
            try:
                response = self.agent.run(prompt)
                seeds = self._parse_ai_candidates(response)
                if seeds:
                    return seeds
            except Exception as e:
                print(f"⚠️ AI推荐失败,使用规则推荐: {str(e)}")

        return self._fallback_candidates(request)

    def _build_ai_prompt(self, request: DestinationChatRequest) -> str:
        messages = "\n".join(f"{item.role}: {item.content}" for item in request.messages[-8:])
        context = request.context
        return f"""请基于以下对话和表单上下文推荐目的地:

对话:
{messages}

上下文:
- 预算: {context.budget if context.budget else '未设置'}
- 天数: {context.travel_days if context.travel_days else '未设置'}
- 出发地: {context.origin_city or '未设置'}
- 推荐数量: {self._recommendation_count(context)}
- 偏好: {', '.join(context.preferences) if context.preferences else '未设置'}
- 交通: {context.transportation or '未设置'}
- 住宿: {context.accommodation or '未设置'}
"""

    def _parse_ai_candidates(self, response: str) -> List[Dict[str, Any]]:
        json_text = response.strip()
        if "```json" in json_text:
            start = json_text.find("```json") + 7
            end = json_text.find("```", start)
            json_text = json_text[start:end].strip()
        elif "```" in json_text:
            start = json_text.find("```") + 3
            end = json_text.find("```", start)
            json_text = json_text[start:end].strip()
        elif "{" in json_text and "}" in json_text:
            json_text = json_text[json_text.find("{"):json_text.rfind("}") + 1]

        try:
            data = json.loads(json_text)
        except Exception:
            return []

        if data.get("needs_more_info"):
            return []

        candidates = data.get("candidates", [])
        if not isinstance(candidates, list):
            return []

        seeds = []
        for item in candidates:
            if not isinstance(item, dict) or not item.get("city"):
                continue
            raw_days = item.get("suggested_days") or 3
            days_match = re.search(r"\d+", str(raw_days))
            seeds.append({
                "city": str(item.get("city")),
                "reason": str(item.get("reason") or ""),
                "suggested_days": int(days_match.group(0)) if days_match else 3,
                "preferences": item.get("preferences") if isinstance(item.get("preferences"), list) else []
            })
        return seeds[:3]

    def _fallback_candidates(self, request: DestinationChatRequest) -> List[Dict[str, Any]]:
        context = request.context
        text = self._latest_user_message(request)
        preferences = set(context.preferences)
        origin_city = self._normalize_city(context.origin_city)

        if origin_city and origin_city in NEARBY_CITY_OPTIONS:
            cities = NEARBY_CITY_OPTIONS[origin_city]
            return [{"city": city, **self._profile_for_city(city)} for city in cities]

        if any(word in text for word in ["海", "海边", "放松", "散步"]):
            cities = ["厦门", "杭州", "苏州"]
        elif any(word in text for word in ["历史", "博物馆", "古城"]) or "历史文化" in preferences:
            cities = ["南京", "西安", "苏州"]
        elif any(word in text for word in ["吃", "美食", "小吃"]) or "美食" in preferences:
            cities = ["成都", "长沙", "南京"]
        elif any(word in text for word in ["自然", "山水", "风景"]) or "自然风光" in preferences:
            cities = ["桂林", "杭州", "厦门"]
        elif context.budget and context.budget <= 2000:
            cities = ["长沙", "南京", "成都"]
        elif context.budget and context.budget >= 6000:
            cities = ["上海", "杭州", "厦门"]
        else:
            cities = ["杭州", "南京", "成都"]

        return [{"city": city, **self._profile_for_city(city)} for city in cities]

    def _profile_for_city(self, city: str) -> Dict[str, Any]:
        if city in FALLBACK_CITY_PROFILES:
            return FALLBACK_CITY_PROFILES[city]
        return {
            "reason": f"{city}适合作为短途目的地,可以根据你的预算和天数安排轻量行程。",
            "preferences": ["休闲", "美食"],
            "highlights": []
        }

    def _normalize_city(self, city: Optional[str]) -> str:
        text = (city or "").strip()
        for suffix in ["市", "地区"]:
            if text.endswith(suffix):
                text = text[:-len(suffix)]
        return text

    def _build_recommendation(
        self,
        seed: Dict[str, Any],
        context: RecommendationContext
    ) -> Optional[DestinationRecommendation]:
        city = str(seed.get("city") or "").strip()
        if not city:
            return None

        suggested_preferences = self._merge_preferences(seed.get("preferences"), context.preferences)
        pois = self._search_city_highlights(city, suggested_preferences)
        highlights = [poi.name for poi in pois[:3]]
        if not highlights:
            highlights = list(seed.get("highlights") or FALLBACK_CITY_PROFILES.get(city, {}).get("highlights", []))[:3]

        weather_summary = self._weather_summary(city)
        suggested_days = int(seed.get("suggested_days") or context.travel_days or 3)
        origin_note = self._origin_note(context.origin_city, city)
        reason = str(seed.get("reason") or self._profile_for_city(city).get("reason") or "和你的偏好比较匹配。")

        free_text = f"目的地灵感助手推荐{city}: {reason}"
        if origin_note:
            free_text += origin_note
        if highlights:
            free_text += f"。优先安排: {'、'.join(highlights)}"

        return DestinationRecommendation(
            city=city,
            reason=reason,
            suggested_days=suggested_days,
            budget_fit=self._budget_fit(context.budget, suggested_days),
            origin_note=origin_note,
            highlights=highlights,
            weather_summary=weather_summary,
            suggested_preferences=suggested_preferences,
            form_patch=RecommendationFormPatch(
                city=city,
                travel_days=suggested_days,
                budget=context.budget,
                transportation=context.transportation,
                accommodation=context.accommodation,
                preferences=suggested_preferences,
                free_text_input=free_text
            )
        )

    def _origin_note(self, origin_city: Optional[str], destination_city: str) -> Optional[str]:
        origin = self._normalize_city(origin_city)
        if not origin:
            return None
        if origin == destination_city:
            return "本地深度游更省交通成本。"
        if destination_city in NEARBY_CITY_OPTIONS.get(origin, []):
            return f"从{origin}出发相对顺路,适合短途或周边游。"
        return f"已按从{origin}出发的需求纳入考虑。"

    def _merge_preferences(self, seed_preferences: Any, context_preferences: List[str]) -> List[str]:
        merged: List[str] = []
        for value in [*context_preferences, *(seed_preferences if isinstance(seed_preferences, list) else [])]:
            text = str(value).strip()
            if text and text not in merged:
                merged.append(text)
        return merged[:4] or ["休闲"]

    def _search_city_highlights(self, city: str, preferences: List[str]):
        try:
            amap_service = get_amap_service()
            keywords = preferences[:1] or ["景点"]
            pois = []
            for keyword in keywords:
                pois.extend(amap_service.search_poi(keyword, city)[:3])
            if not pois:
                pois = amap_service.search_poi("景点", city)[:3]
            unique = []
            seen = set()
            for poi in pois:
                if poi.name in seen:
                    continue
                seen.add(poi.name)
                unique.append(poi)
            return unique
        except Exception as e:
            print(f"⚠️ 获取{city}高德POI失败: {str(e)}")
            return []

    def _weather_summary(self, city: str) -> Optional[str]:
        try:
            weather = get_amap_service().get_weather(city)
            if not weather:
                return None
            first = weather[0]
            if first.day_weather:
                return f"{first.date} 白天{first.day_weather}, 约{first.day_temp}°C"
            return None
        except Exception as e:
            print(f"⚠️ 获取{city}天气失败: {str(e)}")
            return None

    def _budget_fit(self, budget: Optional[int], days: int) -> str:
        if not budget:
            return "未设置预算"
        per_day = budget / max(days, 1)
        if per_day < 500:
            return "偏紧,建议控制住宿和餐饮"
        if per_day < 1000:
            return "较匹配"
        return "比较充足"


_destination_recommender_agent: Optional[DestinationRecommenderAgent] = None


def get_destination_recommender_agent() -> DestinationRecommenderAgent:
    """获取目的地推荐Agent实例"""
    global _destination_recommender_agent

    if _destination_recommender_agent is None:
        _destination_recommender_agent = DestinationRecommenderAgent()

    return _destination_recommender_agent
