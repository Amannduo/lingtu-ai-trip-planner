"""数据模型定义"""

from typing import Any, ClassVar, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from datetime import date


# ============ 请求模型 ============

class TripRequest(BaseModel):
    """旅行规划请求"""
    origin_city: Optional[str] = Field(default=None, description="出发城市", example="上海")
    city: str = Field(..., description="目的地城市", example="北京")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD", example="2025-06-01")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD", example="2025-06-03")
    travel_days: int = Field(..., description="旅行天数", ge=1, le=30, example=3)
    travelers: int = Field(default=1, description="出行人数", ge=1, le=20, example=2)
    budget: Optional[int] = Field(default=None, description="旅行总预算(元)", ge=0, example=3000)
    transportation: str = Field(..., description="交通方式", example="公共交通")
    intercity_transportation: Optional[str] = Field(default=None, description="城际交通方式", example="火车/高铁")
    accommodation: str = Field(..., description="住宿偏好", example="经济型酒店")
    preferences: List[str] = Field(default_factory=list, description="旅行偏好标签", example=["历史文化", "美食"])
    free_text_input: Optional[str] = Field(default="", description="额外要求", example="希望多安排一些博物馆")
    destination_source: Literal["manual", "recommendation"] = Field(
        default="manual",
        description="目的地来源：用户直接指定或智能推荐",
    )
    semantic_contract: Optional["SemanticTripContract"] = Field(
        default=None,
        description="跨跳语义契约快照",
    )
    semantic_risks_acknowledged: bool = Field(
        default=False,
        description="用户已二次确认语义风险",
    )
    date_pattern: Optional[Literal["weekend", "explicit", "unknown"]] = Field(
        default=None,
        description="日期模式：周末推断 / 明确日期 / 未知",
    )
    weekend_style: Optional[Literal["sat_sun", "fri_sun_optional"]] = Field(
        default=None,
        description="周末形态：默认六日两天，或用户确认的周五—周日",
    )
    early_arrival_hint: Optional[str] = Field(
        default=None,
        max_length=500,
        description="周五下午提前抵达建议（建议文案，不自动改日期）",
    )
    departure_mode: Optional[Literal["morning_first_day", "evening_before"]] = Field(
        default=None,
        description="出发模式：首日上午抵达 / 前一晚提前抵达",
    )
    email_on_completion: bool = Field(default=False, description="生成完成后发送邮件")
    delivery_email: Optional[EmailStr] = Field(default=None, description="本次行程收件邮箱")

    @model_validator(mode="after")
    def validate_date_consistency(self):
        try:
            start = date.fromisoformat(self.start_date)
            end = date.fromisoformat(self.end_date)
        except ValueError as exc:
            raise ValueError("出行日期必须使用 YYYY-MM-DD 格式") from exc
        if end < start:
            raise ValueError("结束日期不能早于开始日期")
        expected_days = (end - start).days + 1
        if expected_days != self.travel_days:
            raise ValueError(f"旅行天数应为 {expected_days} 天")
        return self

    class Config:
        json_schema_extra = {
            "example": {
                "origin_city": "上海",
                "city": "北京",
                "start_date": "2025-06-01",
                "end_date": "2025-06-03",
                "travel_days": 3,
                "travelers": 2,
                "budget": 3000,
                "transportation": "公共交通",
                "intercity_transportation": "火车/高铁",
                "accommodation": "经济型酒店",
                "preferences": ["历史文化", "美食"],
                "free_text_input": "希望多安排一些博物馆"
            }
        }


class POISearchRequest(BaseModel):
    """POI搜索请求"""
    keywords: str = Field(..., description="搜索关键词", example="故宫")
    city: str = Field(..., description="城市", example="北京")
    citylimit: bool = Field(default=True, description="是否限制在城市范围内")


class RouteRequest(BaseModel):
    """路线规划请求"""
    origin_address: str = Field(..., description="起点地址", example="北京市朝阳区阜通东大街6号")
    destination_address: str = Field(..., description="终点地址", example="北京市海淀区上地十街10号")
    origin_city: Optional[str] = Field(default=None, description="起点城市")
    destination_city: Optional[str] = Field(default=None, description="终点城市")
    route_type: str = Field(default="walking", description="路线类型: walking/driving/transit")


class ChatMessage(BaseModel):
    """目的地推荐对话消息"""
    role: str = Field(..., description="消息角色: user/assistant", example="user")
    content: str = Field(..., description="消息内容", example="我想玩3天,预算3000,喜欢历史和美食")


class RecommendationContext(BaseModel):
    """目的地推荐上下文"""
    origin_city: Optional[str] = Field(default=None, description="出发城市")
    budget: Optional[int] = Field(default=None, description="旅行总预算(元)", ge=0)
    travel_days: Optional[int] = Field(default=None, description="旅行天数", ge=1, le=30)
    travelers: Optional[int] = Field(default=None, description="出行人数", ge=1, le=20)
    start_date: Optional[str] = Field(default=None, description="已确认开始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="已确认结束日期 YYYY-MM-DD")
    recommendation_count: int = Field(default=3, description="推荐数量", ge=1, le=3)
    preferences: List[str] = Field(default_factory=list, description="旅行偏好")
    transportation: Optional[str] = Field(default=None, description="交通方式")
    accommodation: Optional[str] = Field(default=None, description="住宿偏好")

    @model_validator(mode="after")
    def normalize_date_window(self):
        if not self.start_date and not self.end_date:
            return self
        try:
            start = date.fromisoformat(self.start_date) if self.start_date else None
            end = date.fromisoformat(self.end_date) if self.end_date else None
        except ValueError as exc:
            raise ValueError("推荐上下文日期必须使用 YYYY-MM-DD 格式") from exc
        if start and end:
            if end < start:
                raise ValueError("推荐上下文结束日期不能早于开始日期")
            self.travel_days = (end - start).days + 1
        return self


class DestinationChatRequest(BaseModel):
    """目的地推荐对话请求"""
    messages: List[ChatMessage] = Field(..., description="对话消息")
    context: RecommendationContext = Field(default_factory=RecommendationContext, description="当前表单上下文")



FieldSource = Literal[
    "user_explicit",
    "rule_inferred",
    "form_confirmed",
    "system_default",
    "unknown",
]
FieldConfidence = Literal["high", "medium", "low"]


class FieldBinding(BaseModel):
    """Single field with provenance for semantic contract."""

    value: Optional[Any] = Field(default=None, description="字段值")
    source: FieldSource = Field(default="unknown", description="字段来源")
    confidence: FieldConfidence = Field(default="low", description="置信度")
    pending_confirmation: bool = Field(default=False, description="是否待确认")
    evidence: str = Field(default="", description="抽取证据")
    conflicts: List[str] = Field(default_factory=list, description="字段级冲突")

    def is_known(self) -> bool:
        if self.source == "unknown":
            return False
        if self.value is None:
            return False
        if isinstance(self.value, str) and not self.value.strip():
            return False
        if isinstance(self.value, list) and len(self.value) == 0:
            return False
        return True

    def is_apply_safe(self) -> bool:
        if self.pending_confirmation or not self.is_known():
            return False
        if self.source == "form_confirmed":
            return True
        if self.source == "user_explicit" and self.confidence == "high":
            return True
        if self.source == "rule_inferred" and self.confidence == "high":
            return True
        return False


class SemanticTripContract(BaseModel):
    """跨跳传递的旅行语义契约。"""

    IDENTITY_FIELDS: ClassVar[List[str]] = [
        "origin_city",
        "destination_city",
        "start_date",
        "end_date",
        "travel_days",
        "travelers",
        "travel_party",
        "budget",
        "pace",
        "date_pattern",
        "weekend_style",
        "departure_mode",
    ]

    raw_text: str = Field(default="", description="触发本次抽取的用户原文")
    origin_city: FieldBinding = Field(default_factory=FieldBinding)
    destination_city: FieldBinding = Field(default_factory=FieldBinding)
    start_date: FieldBinding = Field(default_factory=FieldBinding)
    end_date: FieldBinding = Field(default_factory=FieldBinding)
    travel_days: FieldBinding = Field(default_factory=FieldBinding)
    travelers: FieldBinding = Field(default_factory=FieldBinding)
    travel_party: FieldBinding = Field(default_factory=FieldBinding)
    budget: FieldBinding = Field(default_factory=FieldBinding)
    pace: FieldBinding = Field(default_factory=FieldBinding)
    preferences: FieldBinding = Field(default_factory=FieldBinding)
    transportation: FieldBinding = Field(default_factory=FieldBinding)
    accommodation: FieldBinding = Field(default_factory=FieldBinding)
    date_pattern: FieldBinding = Field(default_factory=FieldBinding)
    weekend_style: FieldBinding = Field(default_factory=FieldBinding)
    early_arrival_hint: FieldBinding = Field(default_factory=FieldBinding)
    departure_mode: FieldBinding = Field(default_factory=FieldBinding)
    conflicts: List[str] = Field(default_factory=list)
    pending_fields: List[str] = Field(default_factory=list)

    def refresh_pending_fields(self) -> None:
        pending: List[str] = []
        tracked = self.IDENTITY_FIELDS + [
            "preferences",
            "transportation",
            "accommodation",
            "early_arrival_hint",
        ]
        for name in tracked:
            binding = getattr(self, name)
            if not isinstance(binding, FieldBinding):
                continue
            if binding.pending_confirmation:
                pending.append(name)
            elif not binding.is_known() and name in {
                "origin_city",
                "travel_days",
                "travelers",
                "budget",
            }:
                pending.append(name)
        seen: set[str] = set()
        ordered: List[str] = []
        for item in pending:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        self.pending_fields = ordered


# ============ 响应模型 ============

class Location(BaseModel):
    """地理位置"""
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")


class MapContextPOI(BaseModel):
    """用于打印地图的高德周边场所。"""
    name: str = Field(..., description="场所名称")
    category: str = Field(..., description="分类：餐饮/商店/周边景点/交通")
    address: str = Field(default="", description="地址")
    location: Location = Field(..., description="高德 GCJ-02 坐标")
    poi_id: str = Field(default="", description="高德 POI ID")
    source: str = Field(default="amap_poi", description="数据来源")


class Attraction(BaseModel):
    """景点信息"""
    name: str = Field(..., description="景点名称")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    visit_duration: int = Field(..., description="建议游览时间(分钟)")
    description: str = Field(..., description="景点描述")
    category: Optional[str] = Field(default="景点", description="景点类别")
    rating: Optional[float] = Field(default=None, description="评分")
    photos: Optional[List[str]] = Field(default_factory=list, description="景点图片URL列表")
    poi_id: Optional[str] = Field(default="", description="POI ID")
    image_url: Optional[str] = Field(default=None, description="图片URL")
    coordinate_source: str = Field(default="", description="坐标来源")
    ticket_price: int = Field(default=0, description="门票价格(元)")


class Meal(BaseModel):
    """餐饮信息"""
    type: str = Field(..., description="餐饮类型: breakfast/lunch/dinner/snack")
    name: str = Field(..., description="餐饮名称")
    address: Optional[str] = Field(default=None, description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标")
    description: Optional[str] = Field(default=None, description="描述")
    estimated_cost: int = Field(default=0, description="预估费用(元)")


class Hotel(BaseModel):
    """酒店信息"""
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="酒店地址")
    location: Optional[Location] = Field(default=None, description="酒店位置")
    price_range: str = Field(default="", description="价格范围")
    rating: str = Field(default="", description="评分")
    distance: str = Field(default="", description="距离景点距离")
    type: str = Field(default="", description="酒店类型")
    estimated_cost: int = Field(default=0, description="预估费用(元/晚)")
    poi_id: str = Field(default="", description="高德 POI ID")
    selection_reason: str = Field(default="", description="酒店位置选择说明")


class RouteSegment(BaseModel):
    """单日景点间路线信息"""
    from_name: str = Field(default="", description="起点名称")
    to_name: str = Field(default="", description="终点名称")
    origin_address: str = Field(default="", description="起点地址")
    destination_address: str = Field(default="", description="终点地址")
    route_type: str = Field(default="walking", description="路线类型: walking/driving/transit")
    distance: float = Field(default=0, description="距离(米)")
    duration: int = Field(default=0, description="时间(秒)")
    description: str = Field(default="", description="路线说明")
    path: List[Location] = Field(default_factory=list, description="高德路线折线坐标")
    source: str = Field(default="", description="路线数据来源")
    verified: bool = Field(default=False, description="是否由地图服务验证")


class DayPlan(BaseModel):
    """单日行程"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_index: int = Field(..., description="第几天(从0开始)")
    description: str = Field(..., description="当日行程描述")
    transportation: str = Field(..., description="交通方式")
    accommodation: str = Field(..., description="住宿")
    hotel: Optional[Hotel] = Field(default=None, description="推荐酒店")
    attractions: List[Attraction] = Field(default=[], description="景点列表")
    routes: List[RouteSegment] = Field(default_factory=list, description="景点间路线规划")
    meals: List[Meal] = Field(default=[], description="餐饮列表")


class WeatherInfo(BaseModel):
    """天气信息"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    day_temp: Union[int, str] = Field(default=0, description="白天温度")
    night_temp: Union[int, str] = Field(default=0, description="夜间温度")
    wind_direction: str = Field(default="", description="风向")
    wind_power: str = Field(default="", description="风力")

    @field_validator('day_temp', 'night_temp', mode='before')
    @classmethod
    def parse_temperature(cls, v):
        """解析温度,移除°C等单位"""
        if isinstance(v, str):
            # 移除°C, ℃等单位符号
            v = v.replace('°C', '').replace('℃', '').replace('°', '').strip()
            try:
                return int(v)
            except ValueError:
                return 0
        return v


class Budget(BaseModel):
    """预算信息"""
    total_attractions: int = Field(default=0, description="景点门票总费用")
    total_hotels: int = Field(default=0, description="酒店总费用")
    total_meals: int = Field(default=0, description="餐饮总费用")
    total_transportation: int = Field(default=0, description="交通总费用")
    total: int = Field(default=0, description="总费用")
    hotel_nights: int = Field(default=0, description="酒店晚数")
    hotel_rooms: int = Field(default=1, description="酒店间数")
    hotel_unit_price: int = Field(default=0, description="酒店单晚参考价")
    intercity_transportation: int = Field(default=0, description="城际交通总费用")
    local_transportation: int = Field(default=0, description="市内交通总费用")
    transport_unit_price: int = Field(default=0, description="城际交通单人往返参考价")
    budget_source: str = Field(default="", description="预算数据来源")
    hotel_reference: Optional[str] = Field(default=None, description="酒店参考结果")
    transport_reference: Optional[str] = Field(default=None, description="交通参考结果")
    budget_notes: List[str] = Field(default_factory=list, description="预算备注")


class WebReference(BaseModel):
    """联网智能体引用来源"""
    title: str = Field(default="", description="来源标题")
    url: str = Field(default="", description="来源URL")
    site_name: str = Field(default="", description="站点名称")
    source_type: str = Field(default="", description="来源类型")
    publish_time: Optional[int] = Field(default=None, description="发布时间Unix时间戳")


class AgentAuditResult(BaseModel):
    """联网智能体输出审核结果"""
    status: str = Field(default="warning", description="审核状态: passed/warning/failed")
    source: str = Field(default="", description="输出来源")
    checked_items: List[str] = Field(default_factory=list, description="已检查项目")
    audit_level: Literal["format_only", "semantic_verified", "offline_fallback"] = Field(
        default="format_only",
        description="Audit capability: format/citation checks or semantic verification",
    )
    issues: List[str] = Field(default_factory=list, description="审核发现的问题")
    suggestions: List[str] = Field(default_factory=list, description="后续建议")



class TripPlanQualityIssue(BaseModel):
    """行程质量门发现的结构化问题。"""
    code: str = Field(..., description="稳定的问题代码")
    severity: str = Field(default="warning", description="info/warning/error")
    path: str = Field(default="", description="问题在TripPlan中的字段路径")
    message: str = Field(..., description="面向用户的问题说明")
    suggestion: str = Field(default="", description="可执行的修复建议")
    auto_repaired: bool = Field(default=False, description="是否已自动修复")


class TripPlanQualityResult(BaseModel):
    """行程业务校验结果。"""
    status: str = Field(default="warning", description="passed/warning/failed")
    score: int = Field(default=100, ge=0, le=100)
    constraint_score: int = Field(default=100, ge=0, le=100)
    executability_score: int = Field(default=100, ge=0, le=100)
    evidence_score: int = Field(default=0, ge=0, le=100)
    readiness_score: int = Field(default=100, ge=0, le=100)
    publishable: bool = Field(
        default=False,
        description="Whether automatic persistence and delivery are allowed",
    )
    review_required: bool = Field(
        default=False,
        description="Whether human or advisory review is requested",
    )
    checked_items: List[str] = Field(default_factory=list)
    issues: List[TripPlanQualityIssue] = Field(default_factory=list)
    verified_facts: int = Field(default=0)
    generated_at: str = Field(default="")


class TripPlan(BaseModel):
    """旅行计划"""
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    generation_mode: Literal["primary", "repaired", "map_fallback"] = Field(
        default="primary",
        description="生成路径：主规划、条件式结构修复或地图降级",
    )
    days: List[DayPlan] = Field(..., description="每日行程")
    weather_info: List[WeatherInfo] = Field(default=[], description="天气信息")
    overall_suggestions: str = Field(..., description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")
    web_guide: Optional[str] = Field(default=None, description="联网智能体生成的旅行攻略补充")
    web_references: List[WebReference] = Field(default_factory=list, description="联网智能体引用来源")
    agent_audit: Optional[AgentAuditResult] = Field(default=None, description="联网智能体输出审核结果")
    quality: Optional[TripPlanQualityResult] = Field(default=None, description="规划完成前的业务质量校验结果")
    map_context: List[MapContextPOI] = Field(default_factory=list, description="地图手册周边场所")


class EmailDeliveryResult(BaseModel):
    """旅行计划邮件投递状态。"""

    requested: bool = True
    sent: bool = False
    dry_run: bool = False
    blocked: bool = False
    to: Optional[str] = None
    message: str = ""


class TripPlanResponse(BaseModel):
    """旅行计划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[TripPlan] = Field(default=None, description="旅行计划数据")
    plan_no: Optional[str] = Field(default=None, description="已保存的旅行计划编号")
    email_delivery: Optional[EmailDeliveryResult] = None


class POIInfo(BaseModel):
    """POI信息"""
    id: str = Field(..., description="POI ID")
    name: str = Field(..., description="名称")
    type: str = Field(..., description="类型")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    tel: Optional[str] = Field(default=None, description="电话")
    rating: Optional[float] = Field(default=None, description="高德评分")
    photos: List[str] = Field(default_factory=list, description="高德 POI 图片")
    district: str = Field(default="", description="所在区县")


class POISearchResponse(BaseModel):
    """POI搜索响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[POIInfo] = Field(default=[], description="POI列表")


class RouteInfo(BaseModel):
    """路线信息"""
    distance: float = Field(..., description="距离(米)")
    duration: int = Field(..., description="时间(秒)")
    route_type: str = Field(..., description="路线类型")
    description: str = Field(..., description="路线描述")


class RouteResponse(BaseModel):
    """路线规划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[RouteInfo] = Field(default=None, description="路线信息")


class WeatherResponse(BaseModel):
    """天气查询响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[WeatherInfo] = Field(default=[], description="天气信息")


class RecommendationFormPatch(BaseModel):
    """推荐结果可回填到旅行表单的数据"""
    city: str = Field(..., description="推荐城市")
    destination_source: Literal["manual", "recommendation"] = Field(
        default="recommendation",
        description="目的地来自用户明确指定或系统自动推荐",
    )
    origin_city: Optional[str] = Field(default=None, description="识别出的出发城市")
    start_date: Optional[str] = Field(default=None, description="识别出的开始日期")
    end_date: Optional[str] = Field(default=None, description="识别出的结束日期")
    travel_days: Optional[int] = Field(default=None, description="建议天数")
    travelers: Optional[int] = Field(default=None, description="识别出的出行人数")
    budget: Optional[int] = Field(default=None, description="建议预算")
    transportation: Optional[str] = Field(default=None, description="交通方式")
    accommodation: Optional[str] = Field(default=None, description="住宿偏好")
    preferences: List[str] = Field(default=[], description="建议偏好")
    free_text_input: str = Field(default="", description="建议写入额外要求的内容")
    date_pattern: Optional[Literal["weekend", "explicit", "unknown"]] = Field(default=None)
    weekend_style: Optional[Literal["sat_sun", "fri_sun_optional"]] = Field(default=None)
    early_arrival_hint: Optional[str] = Field(default=None, max_length=500)
    departure_mode: Optional[Literal["morning_first_day", "evening_before"]] = Field(default=None)
    schedule_option: Optional[Literal["default_weekend", "friday_early"]] = Field(default=None)


class DestinationRecommendation(BaseModel):
    """目的地推荐卡片"""
    city: str = Field(..., description="城市")
    reason: str = Field(..., description="推荐理由")
    decision_label: str = Field(default="综合匹配", description="方案的核心取舍标签")
    tradeoff: str = Field(default="", description="选择该方案需要接受的取舍")
    suggested_days: int = Field(default=3, description="建议游玩天数")
    estimated_budget: Optional[int] = Field(default=None, description="按人数与天数估算的总预算")
    pace: str = Field(default="适中", description="行程节奏")
    budget_fit: str = Field(default="可控", description="预算匹配度")
    origin_note: Optional[str] = Field(default=None, description="从出发地出行的说明")
    highlights: List[str] = Field(default=[], description="代表景点/亮点")
    weather_summary: Optional[str] = Field(default=None, description="天气摘要")
    suggested_preferences: List[str] = Field(default=[], description="推荐偏好")
    form_patch: RecommendationFormPatch = Field(..., description="一键回填数据")
    date_pattern: Optional[Literal["weekend", "explicit", "unknown"]] = Field(default=None)
    weekend_style: Optional[Literal["sat_sun", "fri_sun_optional"]] = Field(default=None)
    early_arrival_hint: Optional[str] = Field(default=None, max_length=500)
    departure_mode: Optional[Literal["morning_first_day", "evening_before"]] = Field(default=None)
    schedule_option: Optional[Literal["default_weekend", "friday_early"]] = Field(default=None)
    schedule_summary: Optional[str] = Field(default=None, max_length=200)


class DestinationChatResponse(BaseModel):
    """目的地推荐对话响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    reply: str = Field(default="", description="AI回复")
    needs_more_info: bool = Field(default=False, description="是否需要继续追问")
    interpreted_context: Dict[str, Any] = Field(default_factory=dict)
    semantic_contract: Optional[SemanticTripContract] = Field(default=None)
    recommendations: List[DestinationRecommendation] = Field(default=[], description="目的地推荐列表")


TripRequest.model_rebuild()


# ============ 错误响应 ============

class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = Field(default=None, description="错误代码")
