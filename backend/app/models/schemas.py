"""数据模型定义"""

from typing import List, Optional, Union
from pydantic import BaseModel, Field, field_validator
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
    preferences: List[str] = Field(default=[], description="旅行偏好标签", example=["历史文化", "美食"])
    free_text_input: Optional[str] = Field(default="", description="额外要求", example="希望多安排一些博物馆")
    
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
    recommendation_count: int = Field(default=3, description="推荐数量", ge=1, le=3)
    preferences: List[str] = Field(default=[], description="旅行偏好")
    transportation: Optional[str] = Field(default=None, description="交通方式")
    accommodation: Optional[str] = Field(default=None, description="住宿偏好")


class DestinationChatRequest(BaseModel):
    """目的地推荐对话请求"""
    messages: List[ChatMessage] = Field(..., description="对话消息")
    context: RecommendationContext = Field(default_factory=RecommendationContext, description="当前表单上下文")


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
    issues: List[str] = Field(default_factory=list, description="审核发现的问题")
    suggestions: List[str] = Field(default_factory=list, description="后续建议")


class TripPlan(BaseModel):
    """旅行计划"""
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(..., description="每日行程")
    weather_info: List[WeatherInfo] = Field(default=[], description="天气信息")
    overall_suggestions: str = Field(..., description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")
    web_guide: Optional[str] = Field(default=None, description="联网智能体生成的旅行攻略补充")
    web_references: List[WebReference] = Field(default_factory=list, description="联网智能体引用来源")
    agent_audit: Optional[AgentAuditResult] = Field(default=None, description="联网智能体输出审核结果")
    map_context: List[MapContextPOI] = Field(default_factory=list, description="地图手册周边场所")


class TripPlanResponse(BaseModel):
    """旅行计划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[TripPlan] = Field(default=None, description="旅行计划数据")
    plan_no: Optional[str] = Field(default=None, description="已保存的旅行计划编号")


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
    travel_days: Optional[int] = Field(default=None, description="建议天数")
    budget: Optional[int] = Field(default=None, description="建议预算")
    transportation: Optional[str] = Field(default=None, description="交通方式")
    accommodation: Optional[str] = Field(default=None, description="住宿偏好")
    preferences: List[str] = Field(default=[], description="建议偏好")
    free_text_input: str = Field(default="", description="建议写入额外要求的内容")


class DestinationRecommendation(BaseModel):
    """目的地推荐卡片"""
    city: str = Field(..., description="城市")
    reason: str = Field(..., description="推荐理由")
    suggested_days: int = Field(default=3, description="建议游玩天数")
    budget_fit: str = Field(default="可控", description="预算匹配度")
    origin_note: Optional[str] = Field(default=None, description="从出发地出行的说明")
    highlights: List[str] = Field(default=[], description="代表景点/亮点")
    weather_summary: Optional[str] = Field(default=None, description="天气摘要")
    suggested_preferences: List[str] = Field(default=[], description="推荐偏好")
    form_patch: RecommendationFormPatch = Field(..., description="一键回填数据")


class DestinationChatResponse(BaseModel):
    """目的地推荐对话响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    reply: str = Field(default="", description="AI回复")
    needs_more_info: bool = Field(default=False, description="是否需要继续追问")
    recommendations: List[DestinationRecommendation] = Field(default=[], description="目的地推荐列表")


# ============ 错误响应 ============

class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = Field(default=None, description="错误代码")
