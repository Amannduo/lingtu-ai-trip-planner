"""目的地推荐对话Agent"""

import json
import re
import threading
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from hello_agents import SimpleAgent

from ..models.schemas import (
    DestinationChatRequest,
    DestinationChatResponse,
    DestinationRecommendation,
    RecommendationFormPatch,
    RecommendationContext,
    SemanticTripContract,
)
from ..services.amap_service import get_amap_service
from ..services.destination_feasibility_service import (
    SHORT_TRIP_CITY_GRAPH,
    get_destination_feasibility_service,
)
from ..services.llm_service import get_llm
from ..services.business_calendar import resolve_business_date
from ..services.city_mention_service import (
    COMMON_DESTINATION_CITIES,
    extract_mentioned_destination,
    known_destination_cities,
)
from ..services.semantic_contract_service import (
    EARLY_ARRIVAL_HINT_DEFAULT,
    get_semantic_contract_service,
)

HIGH_INTENSITY_PHRASES = (
    "特种兵",
    "高强度",
    "暴走",
    "紧凑打卡",
    "疯狂打卡",
    "连轴转",
    "马不停蹄",
    "极限一日",
)


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
    "眉县": {
        "reason": "从扶风前往更省时，可围绕太白山或红河谷选择一处主景区，适合把车程和体力消耗控制得更低；周末客流与索道运行需提前复核。",
        "preferences": ["自然风光", "休闲"],
        "themes": ["避暑", "父母友好", "轻松"],
        "highlights": ["太白山国家森林公园", "红河谷森林公园"]
    },
    "麟游县": {
        "reason": "山地县城节奏舒缓，从扶风短途前往更容易控制车程，适合陪父母避暑休闲；具体气温与道路情况需出发前复核。",
        "preferences": ["自然风光", "休闲", "历史文化"],
        "themes": ["避暑", "父母友好", "轻松"],
        "highlights": ["九成宫碑亭", "慈善寺石窟"]
    },
    "太白县": {
        "reason": "山地自然景观集中，适合把两天安排成低强度森林休闲；山区天气和景区开放情况需出发前复核。",
        "preferences": ["自然风光", "休闲"],
        "themes": ["避暑", "父母友好", "轻松"],
        "highlights": ["黄柏塬原生态风景区", "青峰峡森林公园"]
    },
    "凤县": {
        "reason": "森林与峡谷景观较多，可采用一处主景区加县城休息的两天节奏；往返车程和山区路况需提前确认。",
        "preferences": ["自然风光", "休闲"],
        "themes": ["避暑", "父母友好", "轻松"],
        "highlights": ["通天河国家森林公园", "灵官峡"]
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


# Backwards-compatible name used by intent extraction and budget estimation.
# The source of truth lives in DestinationFeasibilityService.
NEARBY_CITY_OPTIONS = SHORT_TRIP_CITY_GRAPH
# COMMON_DESTINATION_CITIES imported from city_mention_service (shared).


class DestinationRecommenderAgent:
    """目的地推荐对话系统"""

    def __init__(self):
        self.agent: Optional[SimpleAgent] = None
        self.llm = None
        try:
            self.llm = get_llm()
            self.agent = SimpleAgent(
                name="目的地推荐助手",
                llm=self.llm,
                system_prompt=RECOMMENDER_PROMPT
            )
        except Exception as e:
            print(f"⚠️ 目的地推荐LLM初始化失败,将使用规则推荐: {type(e).__name__}")

    def chat(self, request: DestinationChatRequest) -> DestinationChatResponse:
        """理解自然语言需求，最多追问一个关键问题，并给出有明确取舍的方案。"""
        latest_message = self._latest_user_message(request)
        contract = self._build_semantic_contract(latest_message, request.context)
        context = self._context_from_contract(contract, request.context)
        inferred = self._flat_from_contract(contract)
        effective_request = request.model_copy(update={"context": context})
        count = self._recommendation_count(context)
        semantics = get_semantic_contract_service()

        needs_more, question = semantics.needs_more_info(
            contract, request.context, latest_message
        )
        if needs_more:
            return DestinationChatResponse(
                success=True,
                message="需要更多信息",
                reply=question
                or "你更想要自然放松、城市美食，还是历史文化？选一个方向就够了。",
                needs_more_info=True,
                interpreted_context=semantics.interpreted_payload(contract),
                semantic_contract=contract,
                recommendations=[],
            )

        if inferred.get("destination_city"):
            seeds = self._fallback_candidates(effective_request)
        else:
            seeds = self._generate_candidate_seeds(effective_request)
        explicit_city = self._normalize_city(inferred.get("destination_city"))
        seeds = self._filter_and_rank_candidates(
            seeds, context, explicit_city, latest_message
        )
        if not seeds and not get_destination_feasibility_service().nearby_destinations(
            context.origin_city
        ):
            seeds = [
                {"city": city, **self._profile_for_city(city)}
                for city in list(FALLBACK_CITY_PROFILES.keys())[:count]
            ]

        user_pace = (
            contract.pace.value
            if contract.pace.is_known() and not contract.pace.pending_confirmation
            else None
        )
        if user_pace == "轻松":
            # Alternatives stay within the gentle band; do not rewrite user pace to 适中.
            decision_specs = [
                ("最省心", "交通和行程最容易落地，但热门时段人流可能更多。", "轻松", 560),
                ("更松弛", "节奏更慢、留白更多，但体验密度不会追求最大化。", "舒缓", 700),
                (
                    "体验丰富",
                    "增加一项代表性体验，但仍控制步行强度、换乘次数和每日主景点数量。",
                    "轻松",
                    800,
                ),
            ]
        else:
            decision_specs = [
                ("最省心", "交通和行程最容易落地，但热门时段人流可能更多。", "轻松", 560),
                ("更松弛", "节奏更慢、留白更多，但体验密度不会追求最大化。", "舒缓", 700),
                ("体验丰富", "特色体验更多，但移动和体力投入相对更高。", "充实", 860),
            ]
        recommendations = []
        seen = set()
        for seed in seeds:
            city = str(seed.get("city") or "").strip()
            if not city or city in seen or len(recommendations) >= count:
                continue
            seen.add(city)
            label, tradeoff, pace, daily_budget = decision_specs[len(recommendations)]
            enriched_seed = {
                **seed,
                "decision_label": seed.get("decision_label") or label,
                "tradeoff": seed.get("tradeoff") or tradeoff,
                "pace": seed.get("pace") or pace,
                "daily_budget": seed.get("daily_budget") or daily_budget,
                "schedule_option": "default_weekend"
                if self._is_default_weekend(contract)
                else None,
            }
            item = self._build_recommendation(enriched_seed, context, contract)
            if item is not None:
                item = self._apply_recommendation_checklist(item, contract, context)
            if item is not None:
                recommendations.append(item)

        # Optional Friday-early card (user must click); never auto-expand default weekend.
        # Appended as an extra decision card so city ranking stays intact.
        if self._is_default_weekend(contract) and recommendations:
            friday_card = self._build_friday_early_option(
                recommendations[0], context, contract
            )
            if friday_card is not None:
                recommendations.append(friday_card)

        understood = self._understood_summary(contract, context)
        pending_note = ""
        if contract.pending_fields:
            pending_note = (
                f" 仍待确认：{'、'.join(contract.pending_fields[:4])}。"
            )
        if contract.conflicts:
            pending_note += " 已检测到需求冲突，表单保留原确认值。"
        if len(recommendations) == 3:
            direction_text = "下面三个方向分别偏向省心、松弛和体验丰富"
        elif len(recommendations) == 1:
            direction_text = "下面是当前约束下最匹配的一个方向"
        else:
            direction_text = f"下面有{len(recommendations)}个可比较方向"
        reply = (
            f"我理解的是：{understood}。{direction_text}，"
            f"可以直接比较后选择；识别有误的内容随时可以修改。{pending_note}"
        )
        return DestinationChatResponse(
            success=True,
            message="推荐成功",
            reply=reply,
            needs_more_info=False,
            interpreted_context=semantics.interpreted_payload(contract),
            semantic_contract=contract,
            recommendations=recommendations,
        )

    def _latest_user_message(self, request: DestinationChatRequest) -> str:
        for message in reversed(request.messages):
            if message.role == "user":
                return message.content.strip()
        return ""

    def _build_semantic_contract(
        self,
        text: str,
        context: RecommendationContext,
    ) -> SemanticTripContract:
        semantics = get_semantic_contract_service()
        # Contract extraction already writes destination_city via shared city_mention.
        message_contract = semantics.extract_from_text(text)
        origin = (
            str(message_contract.origin_city.value)
            if message_contract.origin_city.is_known()
            else context.origin_city
        )
        # Prefer contract destination; _mentioned_destination is compatibility fallback only.
        if not message_contract.destination_city.is_known():
            destination = self._mentioned_destination(text, origin)
            if destination:
                message_contract = semantics.set_destination(
                    message_contract,
                    destination,
                    explicit=True,
                    evidence=destination,
                )
        form_contract = semantics.contract_from_form(context)
        return semantics.merge(form_contract, message_contract)

    def _flat_from_contract(self, contract: SemanticTripContract) -> Dict[str, Any]:
        return get_semantic_contract_service().flat_values(
            contract, include_pending=True
        )

    def _context_from_contract(
        self,
        contract: SemanticTripContract,
        base: RecommendationContext,
    ) -> RecommendationContext:
        return get_semantic_contract_service().to_recommendation_context(
            contract,
            base=base,
            include_pending=True,
        )

    def _infer_trip_intent(
        self,
        text: str,
        context: RecommendationContext,
    ) -> Dict[str, Any]:
        """兼容旧测试：返回扁平意图（含 pending 的规则值，便于内部规划）。"""
        contract = self._build_semantic_contract(text, context)
        return self._flat_from_contract(contract)

    def _clean_location_label(self, value: str) -> str:
        return "".join(str(value or "").split()).strip("，,。 ")

    def _mentioned_destination(self, text: str, origin_city: Optional[str]) -> Optional[str]:
        """Compatibility wrapper — delegates to shared city_mention_service."""
        known = known_destination_cities(extra=FALLBACK_CITY_PROFILES.keys())
        return extract_mentioned_destination(text, origin_city, known_cities=known)

    def _merge_inferred_context(
        self,
        context: RecommendationContext,
        inferred: Dict[str, Any],
    ) -> RecommendationContext:
        """Merge flat inferred values without overwriting form-confirmed fields."""
        semantics = get_semantic_contract_service()
        form_contract = semantics.contract_from_form(context)
        # Rebuild a minimal incoming contract from flat dict (tests pass flats).
        incoming = SemanticTripContract(raw_text=str(inferred.get("raw_text") or ""))
        from ..services.semantic_contract_service import bind

        for name in (
            "origin_city",
            "destination_city",
            "start_date",
            "end_date",
            "travel_days",
            "travelers",
            "travel_party",
            "budget",
            "pace",
            "transportation",
            "accommodation",
        ):
            if name in inferred and inferred[name] is not None:
                setattr(
                    incoming,
                    name,
                    bind(inferred[name], "rule_inferred", "high", evidence="flat_merge"),
                )
        if inferred.get("preferences"):
            incoming.preferences = bind(
                list(inferred["preferences"]),
                "rule_inferred",
                "high",
                evidence="flat_merge",
            )
        merged = semantics.merge(form_contract, incoming)
        return semantics.to_recommendation_context(
            merged, base=context, include_pending=True
        )

    def _needs_more_info(
        self,
        latest_message: str,
        context: RecommendationContext,
        inferred: Dict[str, Any],
    ) -> bool:
        contract = self._build_semantic_contract(latest_message, context)
        needs, _ = get_semantic_contract_service().needs_more_info(
            contract, context, latest_message
        )
        return needs

    def _understood_summary(
        self,
        contract: SemanticTripContract,
        context: RecommendationContext,
    ) -> str:
        parts = []
        if context.origin_city:
            parts.append(f"从{context.origin_city}出发")
        if (
            context.start_date
            and context.end_date
            and not contract.start_date.pending_confirmation
        ):
            duration = f"（{context.travel_days}天）" if context.travel_days else ""
            parts.append(f"{context.start_date} 至 {context.end_date}{duration}")
        elif context.travel_days:
            suffix = "（待确认具体日期）" if contract.start_date.pending_confirmation else ""
            parts.append(f"{context.travel_days}天{suffix}")
        if context.travelers:
            party = (
                contract.travel_party.value
                if contract.travel_party.is_known()
                else None
            )
            if party and contract.travelers.value == context.travelers:
                parts.append(f"{context.travelers}人（{party}）")
            else:
                parts.append(f"{context.travelers}人")
        if context.budget is not None and contract.budget.is_known():
            parts.append(f"总预算约{context.budget}元")
        if context.preferences:
            parts.append("偏好" + "、".join(context.preferences[:3]))
        if contract.pace.is_known():
            parts.append(str(contract.pace.value) + "节奏")
        return "，".join(parts) or "目的地开放、先看不同旅行方向"

    def _recommendation_count(self, context: RecommendationContext) -> int:
        return max(1, min(3, context.recommendation_count or 3))

    def _generate_candidate_seeds(self, request: DestinationChatRequest) -> List[Dict[str, Any]]:
        if getattr(self, "llm", None) is not None:
            prompt = self._build_ai_prompt(request)
            try:
                # Do not reuse SimpleAgent history across users or requests.
                request_agent = SimpleAgent(
                    name="目的地推荐助手",
                    llm=self.llm,
                    system_prompt=RECOMMENDER_PROMPT,
                )
                response = request_agent.run(prompt)
                seeds = self._parse_ai_candidates(response)
                if seeds:
                    return seeds
            except Exception as e:
                print(f"⚠️ AI推荐失败,使用规则推荐: {type(e).__name__}")

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
- 人数: {context.travelers if context.travelers else '未设置'}
- 日期: {context.start_date or '未设置'} 至 {context.end_date or '未设置'}
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
        requested_city = self._mentioned_destination(text, origin_city)

        if requested_city:
            alternatives = [
                city for city in NEARBY_CITY_OPTIONS.get(origin_city, ["杭州", "南京", "成都"])
                if city != requested_city
            ]
            cities = [requested_city, *alternatives]
            return [{"city": city, **self._profile_for_city(city)} for city in cities[:3]]

        if origin_city and origin_city in NEARBY_CITY_OPTIONS:
            nearby = NEARBY_CITY_OPTIONS[origin_city]
            if any(word in text for word in ["海", "海边"]):
                coastal = [city for city in ["厦门", "宁波", "青岛", "威海"] if city != origin_city]
                nearby = [*coastal, *nearby]
            cities = list(dict.fromkeys(nearby))[:3]
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

    def _filter_and_rank_candidates(
        self,
        seeds: List[Dict[str, Any]],
        context: RecommendationContext,
        explicit_city: str = "",
        intent_text: str = "",
    ) -> List[Dict[str, Any]]:
        """Apply deterministic feasibility after the LLM and fill safe gaps.

        A model may propose candidates, but it cannot make a two-day trip from
        Baoji to Kunming or Urumqi feasible. Explicit user choices are retained
        with a warning; automatically proposed infeasible cities are removed.
        """
        service = get_destination_feasibility_service()
        target_count = self._recommendation_count(context)
        accepted: List[tuple[int, int, int, Dict[str, Any]]] = []
        seen: set[str] = set()

        def consider(seed: Dict[str, Any], order: int) -> None:
            city = service.normalize_city(str(seed.get("city") or ""))
            if not city or city in seen:
                return
            is_explicit = bool(explicit_city and city == explicit_city)
            assessment = service.assess(
                context.origin_city,
                city,
                context.travel_days,
                explicit_destination=is_explicit,
            )
            if not assessment.allowed:
                return
            seen.add(city)
            relevance_score = self._candidate_relevance(
                seed, context, intent_text
            )
            accepted.append(
                (
                    1 if is_explicit else 0,
                    assessment.score + relevance_score,
                    -order,
                    {
                        **seed,
                        "city": city,
                        "feasibility_reason": assessment.reason,
                        "transport_note": assessment.transport_note,
                    },
                )
            )

        for index, seed in enumerate(seeds):
            if isinstance(seed, dict):
                consider(seed, index)

        # For weekend trips, deterministic nearby options replace infeasible
        # model output. For longer trips they only fill genuine candidate gaps.
        short_window = context.travel_days is not None and context.travel_days <= 2
        should_fill_nearby = short_window or len(accepted) < target_count
        if should_fill_nearby:
            for index, city in enumerate(service.nearby_destinations(context.origin_city)):
                consider({"city": city, **self._profile_for_city(city)}, len(seeds) + index)

        accepted.sort(key=lambda item: item[:3], reverse=True)
        return [item[3] for item in accepted[:target_count]]

    def _candidate_relevance(
        self,
        seed: Dict[str, Any],
        context: RecommendationContext,
        intent_text: str,
    ) -> int:
        city = str(seed.get("city") or "")
        profile = FALLBACK_CITY_PROFILES.get(city, {})
        candidate_preferences = {
            str(value)
            for value in [
                *(profile.get("preferences") or []),
                *(seed.get("preferences") or []),
            ]
        }
        score = 6 * len(set(context.preferences).intersection(candidate_preferences))
        themes = {
            str(value)
            for value in [
                *(profile.get("themes") or []),
                *(seed.get("themes") or []),
            ]
        }
        if "避暑" in intent_text:
            score += 30 if "避暑" in themes else -8
        if any(word in intent_text for word in ("父母", "爸妈", "老人")):
            score += 10 if "父母友好" in themes else 0
        if any(word in intent_text for word in ("轻松", "不想太累", "慢一点")):
            score += 6 if "轻松" in themes or "休闲" in candidate_preferences else 0
        return score

    def _profile_for_city(self, city: str) -> Dict[str, Any]:
        if city in FALLBACK_CITY_PROFILES:
            return FALLBACK_CITY_PROFILES[city]
        return {
            "reason": f"{city}适合作为短途目的地,可以根据你的预算和天数安排轻量行程。",
            "preferences": ["休闲", "美食"],
            "highlights": []
        }

    def _normalize_city(self, city: Optional[str]) -> str:
        return get_destination_feasibility_service().normalize_city(city)

    def _build_recommendation(
        self,
        seed: Dict[str, Any],
        context: RecommendationContext,
        contract: SemanticTripContract,
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
        start_date, end_date, suggested_days = self._resolve_trip_window(
            context,
            contract,
            seed.get("suggested_days"),
        )
        origin_note = self._origin_note(context.origin_city, city)
        reason = str(seed.get("reason") or self._profile_for_city(city).get("reason") or "和你的偏好比较匹配。")
        # Estimation only — never promote to user budget constraint.
        estimate_days = suggested_days if suggested_days else 3
        estimated_budget = self._estimate_budget(
            city=city,
            days=estimate_days,
            travelers=context.travelers or 1,
            daily_budget=int(seed.get("daily_budget") or 700),
            origin_city=context.origin_city,
        )

        schedule_option = seed.get("schedule_option")
        is_friday_early = schedule_option == "friday_early"
        early_hint = self._early_arrival_hint_for(contract, is_friday_early=is_friday_early)
        free_text = self._build_structured_free_text(
            contract=contract,
            city=city,
            reason=reason,
            origin_note=origin_note,
            transport_note=seed.get("transport_note"),
            highlights=highlights,
            early_hint=early_hint,
            is_friday_early=is_friday_early,
        )

        explicit_destination = (
            contract.destination_city.is_known()
            and self._normalize_city(str(contract.destination_city.value))
            == self._normalize_city(city)
        )

        # form_patch: only contract-backed constraints. Never promote estimated budget
        # or invent default preferences (e.g. ["休闲"]) into the user form.
        patch_budget = self._patch_scalar(contract.budget, context.budget)
        patch_origin = self._patch_scalar(contract.origin_city, context.origin_city)
        patch_travelers = self._patch_scalar(contract.travelers, context.travelers)
        patch_transport = self._patch_scalar(
            contract.transportation, context.transportation
        )
        patch_accommodation = self._patch_scalar(
            contract.accommodation, context.accommodation
        )
        patch_preferences: List[str] = []
        if (
            contract.preferences.is_apply_safe()
            or contract.preferences.source == "form_confirmed"
        ) and isinstance(contract.preferences.value, list):
            patch_preferences = [
                str(item).strip()
                for item in contract.preferences.value
                if str(item).strip()
            ]
        elif context.preferences:
            patch_preferences = list(context.preferences)

        if is_friday_early:
            patch_start, patch_end, patch_days = self._friday_sunday_window(contract, context)
            departure_mode = "evening_before"
            weekend_style = "fri_sun_optional"
            date_pattern = "explicit"
            schedule_summary = "建议行程 3 天（周五—周日）· 周五下午/傍晚出发"
        else:
            if (
                contract.start_date.is_apply_safe()
                or contract.start_date.source == "form_confirmed"
            ):
                patch_start = (
                    str(contract.start_date.value)
                    if contract.start_date.is_known()
                    else context.start_date
                )
                patch_end = (
                    str(contract.end_date.value)
                    if contract.end_date.is_known()
                    else context.end_date
                )
            elif context.start_date and context.end_date and (
                contract.start_date.source in {"form_confirmed", "unknown"}
            ):
                patch_start = context.start_date
                patch_end = context.end_date
            else:
                # rule_inferred weekend dates stay pending — do not auto-fill form
                patch_start = None
                patch_end = None

            if (
                contract.travel_days.is_apply_safe()
                or contract.travel_days.source == "form_confirmed"
            ):
                patch_days = (
                    int(contract.travel_days.value)
                    if contract.travel_days.is_known()
                    else context.travel_days
                )
            elif patch_start and patch_end:
                try:
                    patch_days = (
                        date.fromisoformat(patch_end) - date.fromisoformat(patch_start)
                    ).days + 1
                except ValueError:
                    patch_days = context.travel_days
            else:
                # Default weekend: still expose travel_days=2 without concrete dates.
                patch_days = (
                    int(contract.travel_days.value)
                    if contract.travel_days.is_known()
                    else (2 if self._is_default_weekend(contract) else None)
                )

            departure_mode = (
                str(contract.departure_mode.value)
                if contract.departure_mode.is_known()
                and contract.departure_mode.source in {"user_explicit", "form_confirmed"}
                else None
            )
            weekend_style = (
                str(contract.weekend_style.value)
                if contract.weekend_style.is_known()
                else ("sat_sun" if self._is_default_weekend(contract) else None)
            )
            date_pattern = (
                str(contract.date_pattern.value)
                if contract.date_pattern.is_known()
                else None
            )
            if self._is_default_weekend(contract):
                schedule_summary = "建议行程 2 天（周六—周日）· 可选周五下午提前抵达"
            elif patch_days:
                schedule_summary = f"建议行程 {patch_days} 天"
            else:
                schedule_summary = None

        # Safety: never let early_arrival_hint expand default weekend days.
        if self._is_default_weekend(contract) and not is_friday_early:
            if patch_days is not None and int(patch_days) != 2:
                patch_days = 2
            if patch_start and patch_end:
                try:
                    span = (
                        date.fromisoformat(str(patch_end))
                        - date.fromisoformat(str(patch_start))
                    ).days + 1
                    if span != 2:
                        patch_start = None
                        patch_end = None
                except ValueError:
                    patch_start = None
                    patch_end = None

        return DestinationRecommendation(
            city=city,
            reason=reason,
            decision_label=str(seed.get("decision_label") or "综合匹配"),
            tradeoff=str(seed.get("tradeoff") or ""),
            suggested_days=int(patch_days or estimate_days),
            estimated_budget=estimated_budget,
            pace=str(
                seed.get("pace")
                or (contract.pace.value if contract.pace.is_known() else None)
                or "适中"
            ),
            budget_fit=self._budget_fit(
                patch_budget if isinstance(patch_budget, int) else None,
                estimated_budget,
            ),
            origin_note=origin_note,
            highlights=highlights,
            weather_summary=weather_summary,
            suggested_preferences=suggested_preferences,
            date_pattern=date_pattern,  # type: ignore[arg-type]
            weekend_style=weekend_style,  # type: ignore[arg-type]
            early_arrival_hint=early_hint,
            departure_mode=departure_mode,  # type: ignore[arg-type]
            schedule_option=schedule_option,  # type: ignore[arg-type]
            schedule_summary=schedule_summary,
            form_patch=RecommendationFormPatch(
                city=city,
                destination_source="manual" if explicit_destination else "recommendation",
                origin_city=patch_origin if isinstance(patch_origin, str) or patch_origin is None else str(patch_origin),
                start_date=patch_start,
                end_date=patch_end,
                travel_days=patch_days if isinstance(patch_days, int) or patch_days is None else int(patch_days),
                travelers=patch_travelers if isinstance(patch_travelers, int) or patch_travelers is None else int(patch_travelers),
                budget=patch_budget if isinstance(patch_budget, int) or patch_budget is None else int(patch_budget),
                transportation=patch_transport if isinstance(patch_transport, str) or patch_transport is None else str(patch_transport),
                accommodation=patch_accommodation if isinstance(patch_accommodation, str) or patch_accommodation is None else str(patch_accommodation),
                preferences=patch_preferences,
                free_text_input=free_text,
                date_pattern=date_pattern,  # type: ignore[arg-type]
                weekend_style=weekend_style,  # type: ignore[arg-type]
                early_arrival_hint=early_hint,
                departure_mode=departure_mode,  # type: ignore[arg-type]
                schedule_option=schedule_option,  # type: ignore[arg-type]
            ),
        )

    def _patch_scalar(self, binding, fallback):
        """Return form-patch value only when binding is apply-safe or form-confirmed."""
        if binding.is_apply_safe() or binding.source == "form_confirmed":
            return binding.value if binding.is_known() else fallback
        if fallback is not None and binding.source == "unknown":
            return fallback
        return None

    def _is_default_weekend(self, contract: SemanticTripContract) -> bool:
        style = (
            str(contract.weekend_style.value)
            if contract.weekend_style.is_known()
            else ""
        )
        pattern = (
            str(contract.date_pattern.value)
            if contract.date_pattern.is_known()
            else ""
        )
        if style == "sat_sun" or pattern == "weekend":
            # User-explicit Friday path is fri_sun_optional
            if style == "fri_sun_optional":
                return False
            if (
                contract.departure_mode.is_known()
                and str(contract.departure_mode.value) == "evening_before"
                and contract.departure_mode.source
                in {"user_explicit", "form_confirmed"}
            ):
                return False
            return True
        return False

    def _early_arrival_hint_for(
        self,
        contract: SemanticTripContract,
        *,
        is_friday_early: bool,
    ) -> Optional[str]:
        if is_friday_early:
            return EARLY_ARRIVAL_HINT_DEFAULT
        if contract.early_arrival_hint.is_known():
            return str(contract.early_arrival_hint.value)
        if self._is_default_weekend(contract):
            return EARLY_ARRIVAL_HINT_DEFAULT
        return None

    def _gentle_constraints(self, contract: SemanticTripContract) -> bool:
        if contract.pace.is_known() and str(contract.pace.value) in {"轻松", "舒缓"}:
            return True
        party = str(contract.travel_party.value or "") if contract.travel_party.is_known() else ""
        raw = contract.raw_text or ""
        return any(k in party or k in raw for k in ("父母", "爸妈", "老人", "长辈"))

    def _build_structured_free_text(
        self,
        *,
        contract: SemanticTripContract,
        city: str,
        reason: str,
        origin_note: Optional[str],
        transport_note: Any,
        highlights: List[str],
        early_hint: Optional[str],
        is_friday_early: bool,
    ) -> str:
        constraints: List[str] = []
        if contract.pace.is_known():
            constraints.append(str(contract.pace.value))
        if self._gentle_constraints(contract):
            constraints.append("每日主景点不超过2个")
        if contract.preferences.is_known() and isinstance(contract.preferences.value, list):
            constraints.extend(str(p) for p in contract.preferences.value[:3])

        if is_friday_early:
            period = "周五—周日·3天·evening_before"
        elif self._is_default_weekend(contract):
            period = "周末Sat-Sun·2天"
        elif contract.travel_days.is_known():
            period = f"{contract.travel_days.value}天"
        else:
            period = "未定"

        party = (
            str(contract.travel_party.value)
            if contract.travel_party.is_known()
            else (
                f"{contract.travelers.value}人"
                if contract.travelers.is_known()
                else "未定"
            )
        )
        arrival = early_hint or "无"
        if early_hint and not is_friday_early:
            arrival = f"{early_hint}（可选，尚未确认）"

        lines = [
            f"【目的地】{city}",
            f"【约束】{'；'.join(constraints) if constraints else '无'}",
            f"【时段】{period}",
            f"【抵达建议】{arrival}",
            f"【同行】{party}",
            f"【理由】{reason}",
        ]
        if origin_note:
            lines.append(f"【出发】{origin_note}")
        if transport_note:
            lines.append(f"【城际】{transport_note}")
        if highlights:
            lines.append(f"【优先】{'、'.join(highlights[:3])}")
        if contract.raw_text:
            lines.append(f"【原文】{contract.raw_text[:200]}")
        return "\n".join(lines)

    def _apply_recommendation_checklist(
        self,
        item: DestinationRecommendation,
        contract: SemanticTripContract,
        context: RecommendationContext,
    ) -> Optional[DestinationRecommendation]:
        """Deterministic alignment of reason/tradeoff with contract constraints."""
        if item.schedule_option == "friday_early":
            return item

        reason = item.reason or ""
        tradeoff = item.tradeoff or ""
        origin = context.origin_city or (
            str(contract.origin_city.value) if contract.origin_city.is_known() else ""
        )
        days = item.suggested_days or (
            int(contract.travel_days.value) if contract.travel_days.is_known() else None
        )

        # Origin rationale: only inject assessed short-haul notes.
        # Never invent “从X出发交通方便/较便利” to fake checklist pass.
        if origin:
            origin_token = self._normalize_city(origin) or origin
            has_origin = origin in reason or origin_token in reason
            if (
                not has_origin
                and item.origin_note
                and any(
                    token in item.origin_note
                    for token in ("短途", "顺路", "本地", "往返", "周边")
                )
            ):
                reason = f"{item.origin_note.rstrip('。')}。{reason}"

        # Gentle pacing: strip intensity language and enforce rest note.
        if self._gentle_constraints(contract):
            for phrase in HIGH_INTENSITY_PHRASES:
                reason = reason.replace(phrase, "")
                tradeoff = tradeoff.replace(phrase, "")
            rest_note = "每日主景点建议不超过2个，并为休息与临时调整留时间"
            if rest_note not in reason and rest_note not in tradeoff:
                tradeoff = f"{tradeoff}；{rest_note}" if tradeoff else rest_note
            if item.pace in {"充实", "紧凑"}:
                item.pace = "轻松"

        # Short trip: far destinations only if explicit manual destination.
        if days is not None and days <= 2 and origin:
            service = get_destination_feasibility_service()
            assessment = service.assess(
                origin,
                item.city,
                days,
                explicit_destination=item.form_patch.destination_source == "manual",
            )
            if not assessment.allowed and item.form_patch.destination_source != "manual":
                return None
            if assessment.severity in {"warning", "error"} and item.form_patch.destination_source == "manual":
                risk = assessment.reason or "远途短休交通占用高，有效游玩时间可能不足"
                if risk not in reason:
                    reason = f"{reason}。风险：{risk}"

        # Budget fit: if user budget known and estimate wildly higher, soft note only.
        user_budget = (
            int(contract.budget.value)
            if contract.budget.is_known() and not contract.budget.pending_confirmation
            else context.budget
        )
        if (
            user_budget
            and item.estimated_budget
            and item.estimated_budget > user_budget * 1.35
        ):
            item.budget_fit = f"预算偏紧 · 预计 ¥{item.estimated_budget}"

        # Default weekend must stay 2 days.
        if self._is_default_weekend(contract) and item.schedule_option != "friday_early":
            item.suggested_days = 2
            if item.form_patch.travel_days not in (None, 2):
                item.form_patch.travel_days = 2
            item.schedule_summary = "建议行程 2 天（周六—周日）· 可选周五下午提前抵达"
            if not item.early_arrival_hint:
                item.early_arrival_hint = EARLY_ARRIVAL_HINT_DEFAULT
                item.form_patch.early_arrival_hint = EARLY_ARRIVAL_HINT_DEFAULT

        item.reason = re.sub(r"。{2,}", "。", reason).strip("；。 ") + ("。" if reason else "")
        item.tradeoff = tradeoff.strip("； ")
        return item

    def _friday_sunday_window(
        self,
        contract: SemanticTripContract,
        context: RecommendationContext,
    ) -> tuple[str, str, int]:
        """Compute Fri–Sun window from pending Saturday or today."""
        saturday: Optional[date] = None
        if contract.start_date.is_known():
            try:
                candidate = date.fromisoformat(str(contract.start_date.value))
                # If stored start is Saturday, Friday is -1; if already Friday keep it.
                if candidate.weekday() == 5:
                    saturday = candidate
                elif candidate.weekday() == 4:
                    friday = candidate
                    return friday.isoformat(), (friday + timedelta(days=2)).isoformat(), 3
            except ValueError:
                saturday = None
        if saturday is None:
            today = resolve_business_date()
            days_until_saturday = (5 - today.weekday()) % 7
            saturday = today + timedelta(days=days_until_saturday)
            if "下周末" in (contract.raw_text or ""):
                saturday = saturday + timedelta(days=7)
        friday = saturday - timedelta(days=1)
        sunday = saturday + timedelta(days=1)
        return friday.isoformat(), sunday.isoformat(), 3

    def _build_friday_early_option(
        self,
        base: DestinationRecommendation,
        context: RecommendationContext,
        contract: SemanticTripContract,
    ) -> Optional[DestinationRecommendation]:
        """Optional decision card: user must click to apply 3-day Fri–Sun patch."""
        seed = {
            "city": base.city,
            "reason": (
                f"{base.city}适合在周五下午提前抵达，把交通放在下班后完成，"
                f"周六起再安排主要游览，节奏仍可控。"
            ),
            "decision_label": "周五提前出发",
            "tradeoff": (
                "需占用周五下午/晚上出行时间；可换来周六完整游玩日，"
                "适合短途且能提前下班或调休的出行者。"
            ),
            "pace": base.pace if base.pace in {"轻松", "舒缓", "适中"} else "轻松",
            "daily_budget": 700,
            "highlights": base.highlights,
            "preferences": base.suggested_preferences,
            "schedule_option": "friday_early",
            "transport_note": "建议周五下午或傍晚城际出发，首日仅安排抵达与轻活动",
        }
        item = self._build_recommendation(seed, context, contract)
        if item is None:
            return None
        item.decision_label = "周五提前出发"
        item.schedule_option = "friday_early"
        item.schedule_summary = "建议行程 3 天（周五—周日）· 周五下午/傍晚出发"
        item.departure_mode = "evening_before"
        item.weekend_style = "fri_sun_optional"
        item.date_pattern = "explicit"
        item.early_arrival_hint = EARLY_ARRIVAL_HINT_DEFAULT
        item.suggested_days = 3
        item.form_patch.schedule_option = "friday_early"
        item.form_patch.departure_mode = "evening_before"
        item.form_patch.weekend_style = "fri_sun_optional"
        item.form_patch.date_pattern = "explicit"
        item.form_patch.travel_days = 3
        item.form_patch.early_arrival_hint = EARLY_ARRIVAL_HINT_DEFAULT
        # Concrete dates for Friday option (user confirmed by clicking).
        start, end, days = self._friday_sunday_window(contract, context)
        item.form_patch.start_date = start
        item.form_patch.end_date = end
        item.form_patch.travel_days = days
        return item

    def _resolve_trip_window(
        self,
        context: RecommendationContext,
        contract: SemanticTripContract,
        model_suggested_days: Any,
    ) -> tuple[Optional[str], Optional[str], int]:
        """Return planning window. System defaults stay internal, not form-confirmed."""
        try:
            model_days = int(model_suggested_days)
        except (TypeError, ValueError):
            model_days = 3

        # Prefer contract/form days; model days are system_default only for ranking.
        if contract.travel_days.is_known():
            days = max(1, min(30, int(contract.travel_days.value)))
        elif context.travel_days:
            days = max(1, min(30, int(context.travel_days)))
        else:
            days = max(1, min(30, model_days or 3))

        start: Optional[date] = None
        end: Optional[date] = None
        try:
            if context.start_date and (
                contract.start_date.source == "form_confirmed"
                or not contract.start_date.pending_confirmation
                or not contract.start_date.is_known()
            ):
                # Use form dates when present; pending weekend dates still usable for planning
                start = date.fromisoformat(context.start_date)
            elif contract.start_date.is_known():
                start = date.fromisoformat(str(contract.start_date.value))
            if context.end_date and (
                contract.end_date.source == "form_confirmed"
                or not contract.end_date.pending_confirmation
                or not contract.end_date.is_known()
            ):
                end = date.fromisoformat(context.end_date)
            elif contract.end_date.is_known():
                end = date.fromisoformat(str(contract.end_date.value))
        except ValueError:
            start = None
            end = None

        # Form-confirmed dates always win
        if contract.start_date.source == "form_confirmed" and context.start_date:
            try:
                start = date.fromisoformat(context.start_date)
            except ValueError:
                pass
        if contract.end_date.source == "form_confirmed" and context.end_date:
            try:
                end = date.fromisoformat(context.end_date)
            except ValueError:
                pass

        if start is not None and end is not None and end >= start:
            days = min(30, (end - start).days + 1)
            end = start + timedelta(days=days - 1)
        elif start is not None:
            end = start + timedelta(days=days - 1)
        elif end is not None:
            start = end - timedelta(days=days - 1)
        else:
            return None, None, days
        return start.isoformat(), end.isoformat(), days

    def _estimate_budget(
        self,
        city: str,
        days: int,
        travelers: int,
        daily_budget: int,
        origin_city: Optional[str]
    ) -> int:
        people = max(1, travelers)
        city_multiplier = 1.2 if city in {"上海", "北京", "深圳", "厦门"} else 1.0
        origin = self._normalize_city(origin_city)
        nearby = get_destination_feasibility_service().nearby_destinations(origin_city)
        if origin and (city == origin or city in nearby):
            transport_per_person = 180
        elif origin:
            transport_per_person = 600
        else:
            transport_per_person = 350
        return int(round((days * people * daily_budget * city_multiplier + people * transport_per_person) / 100) * 100)

    def _origin_note(self, origin_city: Optional[str], destination_city: str) -> Optional[str]:
        origin = self._normalize_city(origin_city)
        origin_label = self._clean_location_label(origin_city or "") or origin
        if not origin:
            return None
        if origin == destination_city:
            return "本地深度游更省交通成本。"
        nearby = get_destination_feasibility_service().nearby_destinations(origin_city)
        dest_norm = self._normalize_city(destination_city) or destination_city
        if destination_city in nearby or dest_norm in nearby:
            return f"从{origin_label}出发相对顺路，适合短途或周边游。"
        # Non-short-haul: do not invent convenience copy; keep a neutral note only.
        return f"出发地：{origin_label}（请自行核对往返班次与耗时）。"

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
            print(f"⚠️ 高德POI获取失败: {type(e).__name__}")
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
            print(f"⚠️ 天气获取失败: {type(e).__name__}")
            return None

    def _budget_fit(self, budget: Optional[int], estimated_budget: int) -> str:
        if not budget:
            return f"预计约 ¥{estimated_budget}"
        ratio = budget / max(estimated_budget, 1)
        if ratio < 0.8:
            return f"预算偏紧 · 预计 ¥{estimated_budget}"
        if ratio < 1.15:
            return f"预算匹配 · 预计 ¥{estimated_budget}"
        return f"预算充足 · 预计 ¥{estimated_budget}"


_destination_recommender_agent: Optional[DestinationRecommenderAgent] = None
_destination_recommender_lock = threading.Lock()


def get_destination_recommender_agent() -> DestinationRecommenderAgent:
    """获取目的地推荐Agent实例"""
    global _destination_recommender_agent

    if _destination_recommender_agent is None:
        with _destination_recommender_lock:
            if _destination_recommender_agent is None:
                _destination_recommender_agent = DestinationRecommenderAgent()

    return _destination_recommender_agent
