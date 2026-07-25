"""联网旅行攻略生成与审核Agent."""

import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..models.schemas import AgentAuditResult, TripPlan, TripRequest, WebReference
from ..services.volcengine_agent_service import get_volcengine_agent_service


WEB_GUIDE_AGENT_NAME = "旅行联网攻略审核助手"
WEB_GUIDE_AGENT_INTRO = (
    "基于已生成的结构化行程，联网核对景区预约、天气穿衣、交通、预算和注意事项，"
    "输出适合旅行者直接阅读的行前攻略。"
)
WEB_GUIDE_OPENING = "请发送目的地、日期、人数、预算和已有行程，我会联网整理行前准备与审核建议。"
WEB_GUIDE_OPENING_QUESTIONS = [
    "帮我核对这份行程的预约和注意事项",
    "把这个行程整理成出发前攻略",
    "检查门票、天气、交通和预算是否合理"
]

WEB_GUIDE_SYSTEM_PROMPT = """你是一个联网旅行攻略生成与审核助手。你的任务是基于用户给出的结构化旅行计划，联网检索并核对最新公开信息，输出清晰、可靠、适合旅行者直接阅读的中文攻略。

工作要求：
1. 必须优先核对会随时间变化的信息，包括景区预约规则、开放/闭馆安排、天气与穿衣、票务、交通、酒店位置、预算合理性。
2. 不要编造来源。无法确认的信息要明确写成“建议出发前再次确认”，不要写成确定事实。
3. 输出使用中文 Markdown，必须使用 `##`/`###` 标题、数字列表和普通段落，不要输出纯文本伪标题。
4. 保留用户行程里的关键事实：城市、日期、天数、人数、酒店、核心景点、预算、交通方式。
5. 若联网结果与输入行程冲突，先指出风险，再给出保守建议。
6. 不输出 EOF、代码块包装或命令行说明。

固定输出结构：
## 行前准备与建议

### 预约要求
1. ...

### 穿衣建议
...

### 物品准备
1. ...

### 其他注意事项
1. ...

### 行程总览
旅行总天数：...
起止日期：...

### 核心景点
1. ...

### 跨市交通方案
...

### 入住酒店
...

### 总预算估算
...

### 行程定位
...

### 资料来源
1. ...

### 审核检查
1. ...
"""


class WebTravelGuideAgent:
    """Generate a web-enhanced travel guide and audit it."""

    def __init__(self):
        self.volcengine_service = get_volcengine_agent_service()

    def apply_to_plan(self, request: TripRequest, trip_plan: TripPlan) -> TripPlan:
        guide, references, audit = self.generate(request, trip_plan)
        trip_plan.web_guide = guide
        trip_plan.web_references = references
        trip_plan.agent_audit = audit
        return trip_plan

    def generate(
        self,
        request: TripRequest,
        trip_plan: TripPlan
    ) -> Tuple[str, List[WebReference], AgentAuditResult]:
        source = "local_fallback"
        references: List[WebReference] = []
        service_error = ""
        is_configured = self.volcengine_service.is_configured

        if is_configured:
            try:
                guide, references, _ = self.volcengine_service.chat(
                    WEB_GUIDE_SYSTEM_PROMPT,
                    self._build_user_prompt(request, trip_plan),
                    knowledge=self._build_knowledge(request, trip_plan),
                    location_info=self._build_location_info(request, trip_plan),
                    user_id="lingtu-ai-trip-planner"
                )
                if guide:
                    guide = self._ensure_guide_trip_context(guide, request)
                    source = "volcengine_web_agent"
                    audit = self.audit_guide(guide, request, trip_plan, references, source)
                    return guide, references, audit
                service_error = "Volcengine agent returned empty content"
            except Exception as exc:
                service_error = str(exc)

        guide = self._create_fallback_guide(request, trip_plan, is_configured, service_error)
        audit = self.audit_guide(guide, request, trip_plan, references, source, service_error)
        return guide, references, audit

    def console_settings(self) -> Dict[str, object]:
        """Return settings that can be pasted into the Volcengine console."""
        return {
            "name": WEB_GUIDE_AGENT_NAME,
            "intro": WEB_GUIDE_AGENT_INTRO,
            "opening": WEB_GUIDE_OPENING,
            "opening_questions": WEB_GUIDE_OPENING_QUESTIONS,
            "system_prompt": WEB_GUIDE_SYSTEM_PROMPT,
            "reply_style": "中文Markdown，结构化攻略，含资料来源和审核检查",
            "networking": "强制联网或自动联网；建议开启引用角标",
            "temperature": "0.2-0.4",
            "api": {
                "url": "https://open.feedcoopapi.com/agent_api/agent/chat/completion",
                "method": "POST",
                "auth": "Authorization: Bearer <API_KEY>",
                "body": "bot_id, messages, stream=false, extension_options.browsing_mode=2"
            }
        }

    def status(self) -> Dict[str, object]:
        return {
            "name": WEB_GUIDE_AGENT_NAME,
            "configured": self.volcengine_service.is_configured,
            "provider": "volcengine_web_agent" if self.volcengine_service.is_configured else "local_fallback"
        }

    def _ensure_guide_trip_context(self, guide: str, request: TripRequest) -> str:
        """Make the requested trip facts explicit even when the web agent omits them."""
        text = (guide or "").strip()
        if (
            self._contains_date(text, request.start_date)
            and self._contains_date(text, request.end_date)
            and request.city in text
        ):
            return text

        context = (
            "## 行程总览\n"
            f"1. 目的地：{request.city}\n"
            f"2. 旅行日期：{request.start_date} 至 {request.end_date}"
            f"（{self._chinese_date(request.start_date)} 至 {self._chinese_date(request.end_date)}）\n"
            f"3. 旅行天数：{request.travel_days}天\n"
            f"4. 出行人数：{request.travelers}人"
        )
        return f"{context}\n\n{text}" if text else context

    def _build_user_prompt(self, request: TripRequest, trip_plan: TripPlan) -> str:
        plan_payload = trip_plan.model_dump(
            exclude={"web_guide", "web_references", "agent_audit"},
            mode="json"
        )
        return f"""请基于以下旅行计划，生成一份与样例风格一致的行前攻略，并联网核对最新信息。

当前日期：{datetime.now().strftime("%Y-%m-%d")}

用户需求：
- 出发城市：{request.origin_city or "未填写"}
- 目的地城市：{request.city}
- 日期：{request.start_date} 至 {request.end_date}（必须在正文“行程总览”中原样保留这个起止日期）
- 天数：{request.travel_days}
- 人数：{request.travelers}
- 总预算：{request.budget if request.budget is not None else "未设置"}
- 交通方式：{request.transportation}
- 城际交通：{request.intercity_transportation or "未设置"}
- 住宿偏好：{request.accommodation}
- 偏好：{", ".join(request.preferences) if request.preferences else "无"}
- 额外要求：{request.free_text_input or "无"}

强制对齐要求：
1. 正文必须明确写出“旅行日期：{request.start_date} 至 {request.end_date}”，不要只写“本周末”“近期”或省略年份。
2. 天气信息必须对准旅行日期。若联网天气来源只覆盖近期或不覆盖{request.start_date}至{request.end_date}，请明确写“暂不能确认旅行日期逐日天气，建议出发前3-7天复核”，不要把近期天气当作旅行日期天气。
3. 所有预约、票务、交通、酒店和预算建议都必须围绕{request.city}、{request.start_date}至{request.end_date}这段行程展开。

结构化行程JSON：
{json.dumps(plan_payload, ensure_ascii=False, indent=2)}
"""

    def _build_knowledge(self, request: TripRequest, trip_plan: TripPlan) -> str:
        attractions = "、".join(self._unique_attraction_names(trip_plan)[:8]) or "未生成景点"
        return (
            f"旅行场景：{request.travelers}人前往{request.city}，"
            f"{request.start_date}至{request.end_date}，共{request.travel_days}天。"
            f"核心景点：{attractions}。"
            f"请重点核对预约、开放、天气、交通、预算和安全注意事项。"
        )

    def _build_location_info(self, request: TripRequest, trip_plan: TripPlan) -> Dict[str, object]:
        first_location = None
        for day in trip_plan.days:
            for attraction in day.attractions:
                if attraction.location:
                    first_location = attraction.location
                    break
            if first_location:
                break

        location_info: Dict[str, object] = {"city": request.city}
        if first_location:
            location_info["longitude"] = first_location.longitude
            location_info["latitude"] = first_location.latitude
        return location_info

    def _create_fallback_guide(
        self,
        request: TripRequest,
        trip_plan: TripPlan,
        volcengine_configured: bool = False,
        service_error: str = ""
    ) -> str:
        attraction_names = self._unique_attraction_names(trip_plan)
        hotel = self._first_hotel(trip_plan)
        budget_total = trip_plan.budget.total if trip_plan.budget else request.budget
        budget_text = f"约{budget_total}元，{request.travelers}人总花费" if budget_total else "暂未形成完整预算，建议按住宿、门票、餐饮、交通分项复核"
        date_text = self._date_range_text(request)
        core_attractions = "\n".join(
            f"{index + 1}. {name}" for index, name in enumerate(attraction_names[:8])
        ) or "1. 暂未生成明确景点，建议重新生成行程后复核。"

        reservation_items = self._reservation_items(request.city, attraction_names)
        reservation_text = "\n".join(f"{index + 1}. {item}" for index, item in enumerate(reservation_items))
        packing_text = self._packing_text(request)
        hotel_text = self._hotel_text(hotel)
        transport_text = self._transport_text(request)
        source_text = self._fallback_source_text(volcengine_configured, service_error)

        return f"""行前准备与建议

预约要求：
{reservation_text}

穿衣建议：
{self._clothing_text(request)}

物品准备：
{packing_text}

其他注意事项：
1. 热门景点、博物馆和演出类项目建议出发前再次确认开放时间、预约入口和退改规则。
2. 餐饮街区人流密集，肠胃敏感者建议控制辛辣、油腻和生冷食物摄入。
3. 多数城市景点支持扫码支付，但建议准备少量现金用于临时交通、小吃摊点或押金。
4. 文物保护区、博物馆和历史遗址内请遵守拍摄、无人机、饮食和大件行李寄存规定。

行程总览：
旅行总天数：{request.travel_days}天
起止日期：{date_text}

核心景点：
{core_attractions}

跨市交通方案：
{transport_text}

入住酒店：
{hotel_text}

总预算估算：
{budget_text}，含交通、住宿、门票、餐饮等主要支出。实际价格会随日期、余票和平台活动变化，建议付款前复核。

行程定位：
本行程围绕{request.city}的{self._preference_text(request)}展开，节奏以可执行为优先，适合出发前做预约、物品和预算核对。

资料来源：
{source_text}

审核检查：
1. 已检查行程日期、核心景点、住宿、预算和行前注意事项是否完整。
2. 当前缺少联网引用，涉及开放时间、票务和天气的信息需出发前再次确认。"""

    def _fallback_source_text(self, volcengine_configured: bool, service_error: str) -> str:
        if volcengine_configured:
            reason = f"调用失败：{service_error}" if service_error else "调用未返回有效内容"
            return (
                "1. 火山联网问答Agent已配置，但本次联网攻略未成功返回，"
                f"{reason}，本段为本地降级生成。\n"
                "2. 当前缺少联网引用来源，建议检查网络连通性、Bot配置、联网权限和接口超时时间。"
            )

        return (
            "1. 当前未配置火山联网问答Agent凭证，本段为本地降级生成。\n"
            "2. 配置VOLCENGINE_AGENT_API_KEY和VOLCENGINE_AGENT_BOT_ID后，将通过联网智能体返回可核验来源。"
        )

    def audit_guide(
        self,
        guide: str,
        request: TripRequest,
        trip_plan: TripPlan,
        references: List[WebReference],
        source: str,
        service_error: str = ""
    ) -> AgentAuditResult:
        checked_items = [
            "输出结构包含行前准备、预约、穿衣、物品、注意事项、总览、预算和审核检查",
            "保留目的地、旅行日期、天数、核心景点和住宿信息",
            "检查是否存在命令行EOF或乱码",
            "检查联网引用或降级状态"
        ]
        issues: List[str] = []
        suggestions: List[str] = []

        if len((guide or "").strip()) < 120:
            issues.append("联网攻略正文过短，无法覆盖必要行前信息。")

        required_sections = [
            "行前准备", "预约要求", "穿衣建议", "物品准备", "其他注意事项",
            "行程总览", "核心景点", "跨市交通", "入住酒店", "总预算", "行程定位"
        ]
        for section in required_sections:
            if section not in guide:
                issues.append(f"缺少必要栏目：{section}。")

        if request.city not in guide:
            issues.append(f"正文未明确目的地城市：{request.city}。")

        if not self._contains_date(guide, request.start_date):
            issues.append(f"正文未明确开始日期：{request.start_date}。")

        if not self._contains_date(guide, request.end_date):
            issues.append(f"正文未明确结束日期：{request.end_date}。")

        if "EOF" in guide or "�" in guide:
            issues.append("正文中存在EOF标记或疑似乱码，需要清理后展示。")

        if source == "volcengine_web_agent" and not references:
            issues.append("火山联网Agent未返回引用来源，建议检查控制台是否开启引用/联网能力。")

        if source != "volcengine_web_agent":
            if service_error:
                issues.append(f"联网Agent未成功调用，已使用本地降级生成：{service_error}")
                if "timed out" in service_error.lower() or "timeout" in service_error.lower():
                    suggestions.append("火山联网Agent调用超时，已将 VOLCENGINE_AGENT_TIMEOUT 提高到120秒；如仍超时，请检查Bot联网检索耗时、接口网络连通性或临时关闭强制联网。")
                else:
                    suggestions.append("火山联网Agent已配置但调用失败，请检查Bot ID、API权限、接口地址和后端网络连通性。")
            else:
                issues.append("未配置火山联网Agent凭证，已使用本地降级生成。")
                suggestions.append("在backend/.env中配置VOLCENGINE_AGENT_API_KEY和VOLCENGINE_AGENT_BOT_ID后重新生成。")

        if references:
            checked_items.append(f"已接收{len(references)}条联网引用来源")
        else:
            suggestions.append("出发前人工复核景区预约入口、天气、票务和酒店价格。")

        status = "passed"
        if issues:
            status = "warning"
        if len((guide or "").strip()) < 120:
            status = "warning"

        audit_level = "format_only" if references else "offline_fallback"

        return AgentAuditResult(
            status=status,
            source=source,
            checked_items=checked_items,
            issues=issues,
            suggestions=suggestions,
            audit_level=audit_level,
        )

    def _reservation_items(self, city: str, attraction_names: List[str]) -> List[str]:
        items: List[str] = []
        names = "、".join(attraction_names)
        if city == "西安" or any("陕西历史博物馆" in name for name in attraction_names):
            items.append("陕西历史博物馆：建议提前3-7天在官方渠道完成实名预约，按预约时段入馆并携带身份证件。")
        if any("兵马俑" in name or "秦始皇" in name for name in attraction_names):
            items.append("秦始皇兵马俑博物院：建议提前通过官方或正规平台购买电子票，节假日预留排队和安检时间。")
        if any("博物馆" in name for name in attraction_names) and not any("博物馆" in item for item in items):
            items.append("博物馆类景点：通常需要实名预约或限流，请提前确认开放日、闭馆日和入馆证件要求。")
        if names:
            items.append(f"其他核心景点（{names[:80]}）：建议出发前核对开放时间、门票政策和预约入口。")
        if not items:
            items.append("暂未识别到明确景点，建议生成完整行程后再核对预约和门票。")
        return items

    def _clothing_text(self, request: TripRequest) -> str:
        month = self._start_month(request.start_date)
        if month in (3, 4, 5):
            season = "春夏过渡季，昼夜温差可能较明显"
            clothing = "轻便长袖、薄外套、舒适运动鞋、防晒用品"
        elif month in (6, 7, 8):
            season = "夏季天气偏热，户外暴晒和降雨都需要考虑"
            clothing = "透气速干衣物、防晒外套、遮阳帽、雨具、舒适运动鞋"
        elif month in (9, 10, 11):
            season = "秋季适合步行游览，但早晚可能偏凉"
            clothing = "长袖、薄外套、轻便裤装、舒适运动鞋"
        else:
            season = "冬季或早春时段，体感温度可能低于预期"
            clothing = "保暖外套、围巾、手套、舒适防滑鞋"
        return f"{request.city}{season}。推荐穿着：{clothing}。每日步行较多，鞋子舒适度优先于造型。"

    def _packing_text(self, request: TripRequest) -> str:
        items = [
            "身份证或其他有效证件、手机、充电宝、常用充电线。",
            "防晒霜、遮阳帽、墨镜、纸巾/湿巾、雨具。",
            "常用药品，如肠胃药、创可贴、晕车药和个人长期用药。",
            "拍摄设备或手机稳定器；博物馆和文物区请提前确认拍摄限制。"
        ]
        return "\n".join(f"{index + 1}. {item}" for index, item in enumerate(items))

    def _transport_text(self, request: TripRequest) -> str:
        if request.origin_city and request.origin_city != request.city:
            intercity = request.intercity_transportation or request.transportation
            return f"从{request.origin_city}前往{request.city}，优先按{intercity}安排往返；市内以{request.transportation}衔接景点。"
        return f"无明确跨市行程，全程以{request.city}市内游为主，市内交通建议采用{request.transportation}。"

    def _hotel_text(self, hotel) -> str:
        if not hotel:
            return "暂未生成具体酒店，建议选择交通便利、靠近地铁或核心景点的住宿。"
        parts = [f"{hotel.name}。"]
        if hotel.address:
            parts.append(f"地址：{hotel.address}。")
        if hotel.price_range:
            parts.append(f"价格：{hotel.price_range}。")
        if hotel.estimated_cost:
            parts.append(f"参考价：约{hotel.estimated_cost}元/晚。")
        return "\n".join(parts)

    def _preference_text(self, request: TripRequest) -> str:
        return "、".join(request.preferences[:4]) if request.preferences else "城市观光与休闲体验"

    def _unique_attraction_names(self, trip_plan: TripPlan) -> List[str]:
        names: List[str] = []
        for day in trip_plan.days:
            for attraction in day.attractions:
                name = attraction.name.strip()
                if name and name not in names:
                    names.append(name)
        return names

    def _first_hotel(self, trip_plan: TripPlan):
        for day in trip_plan.days:
            if day.hotel:
                return day.hotel
        return None

    def _date_range_text(self, request: TripRequest) -> str:
        start = self._date_with_weekday(request.start_date)
        end = self._date_with_weekday(request.end_date)
        return f"{start}，至{end}"

    def _date_with_weekday(self, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return value
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return f"{parsed.year}年{parsed.month}月{parsed.day}日，{weekdays[parsed.weekday()]}"

    def _chinese_date(self, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return value
        return f"{parsed.year}年{parsed.month}月{parsed.day}日"

    def _contains_date(self, text: str, value: str) -> bool:
        return any(candidate in text for candidate in self._date_candidates(value))

    def _date_candidates(self, value: str) -> List[str]:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return [value]
        year = parsed.year
        month = parsed.month
        day = parsed.day
        return [
            parsed.strftime("%Y-%m-%d"),
            f"{year}-{month}-{day}",
            parsed.strftime("%Y/%m/%d"),
            f"{year}/{month}/{day}",
            f"{year}年{month}月{day}日",
            f"{year}年{month:02d}月{day:02d}日",
        ]

    def _start_month(self, value: str) -> int:
        try:
            return datetime.strptime(value, "%Y-%m-%d").month
        except ValueError:
            return datetime.now().month

    def _safe_provider_error(self, exc: Exception) -> str:
        if type(exc).__name__ == "ZhipuSearchError":
            message = str(exc).lower()
            if "1113" in message:
                return "智谱账户余额不足或无可用搜索资源包（错误码1113）"
            if "rate limit" in message:
                return "智谱搜索请求过于频繁，请稍后重试"
            if "authorization" in message or "permission" in message:
                return "智谱搜索鉴权或接口权限校验失败"
            if "not configured" in message:
                return "智谱搜索尚未配置"
            if "size limit" in message or "exceeded" in message:
                return "智谱搜索响应超过安全大小限制"
            if "invalid response" in message or "invalid json" in message:
                return "智谱搜索返回格式异常"
            if "api url is invalid" in message:
                return "智谱搜索接口配置无效"
            return "智谱搜索暂时不可用"
        return f"智谱搜索暂时不可用（{type(exc).__name__}）"


_web_travel_guide_agent: Optional[WebTravelGuideAgent] = None


def get_web_travel_guide_agent() -> WebTravelGuideAgent:
    """Get singleton web travel guide agent."""
    global _web_travel_guide_agent
    if _web_travel_guide_agent is None:
        _web_travel_guide_agent = WebTravelGuideAgent()
    return _web_travel_guide_agent
