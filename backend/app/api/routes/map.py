"""地图服务API路由"""

import math
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
from ...models.schemas import (
    Location,
    MapContextPOI,
    POISearchRequest,
    POISearchResponse,
    RouteInfo,
    RouteRequest,
    RouteResponse,
    WeatherResponse
)
from ...services.amap_service import get_amap_service

router = APIRouter(prefix="/map", tags=["地图服务"])


class MapContextRequest(BaseModel):
    city: str = Field(..., description="目的地城市")
    locations: list[Location] = Field(..., min_length=1, max_length=100)
    limit: int = Field(default=24, ge=8, le=32)


class MapContextResponse(BaseModel):
    success: bool
    center: Location
    radius: int
    data: list[MapContextPOI]


def _distance_m(origin: Location, destination: Location) -> float:
    lon1, lat1, lon2, lat2 = map(
        math.radians,
        [origin.longitude, origin.latitude, destination.longitude, destination.latitude],
    )
    delta_lon = lon2 - lon1
    delta_lat = lat2 - lat1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6371000 * 2 * math.asin(min(1.0, math.sqrt(value)))


def _first_value_by_keys(data, keys):
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if value not in (None, "", [], {}):
                return value
        for value in data.values():
            found = _first_value_by_keys(value, keys)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(data, list):
        for item in data:
            found = _first_value_by_keys(item, keys)
            if found not in (None, "", [], {}):
                return found
    return None


def _first_number_by_keys(data, keys) -> float:
    value = _first_value_by_keys(data, keys)
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return float(value)
    import re
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else 0


def _route_description(data, origin_name: str, destination_name: str, route_type: str) -> str:
    instructions = []
    keys = {"instruction", "assistant_action", "action", "name"}

    def visit(value):
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

    visit(data)
    if instructions:
        return "；".join(instructions[:5])

    route_label = {
        "walking": "步行",
        "driving": "驾车",
        "transit": "公共交通"
    }.get(route_type, route_type)
    return f"从{origin_name}前往{destination_name}，建议使用{route_label}衔接。"


@router.get(
    "/poi",
    response_model=POISearchResponse,
    summary="搜索POI",
    description="根据关键词搜索POI(兴趣点)"
)
async def search_poi(
    keywords: str = Query(..., description="搜索关键词", examples=["故宫"]),
    city: str = Query(..., description="城市", examples=["北京"]),
    citylimit: bool = Query(True, description="是否限制在城市范围内")
):
    """
    搜索POI
    
    Args:
        keywords: 搜索关键词
        city: 城市
        citylimit: 是否限制在城市范围内
        
    Returns:
        POI搜索结果
    """
    try:
        # 获取服务实例
        service = get_amap_service()
        
        # 搜索POI
        pois = service.search_poi(keywords, city, citylimit)
        
        return POISearchResponse(
            success=True,
            message="POI搜索成功",
            data=pois
        )
        
    except Exception as e:
        print(f"❌ POI搜索失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"POI搜索失败: {str(e)}"
        )


@router.get(
    "/weather",
    response_model=WeatherResponse,
    summary="查询天气",
    description="查询指定城市的天气信息"
)
async def get_weather(
    city: str = Query(..., description="城市名称", examples=["北京"])
):
    """
    查询天气
    
    Args:
        city: 城市名称
        
    Returns:
        天气信息
    """
    try:
        # 获取服务实例
        service = get_amap_service()
        
        # 查询天气
        weather_info = service.get_weather(city)
        
        return WeatherResponse(
            success=True,
            message="天气查询成功",
            data=weather_info
        )
        
    except Exception as e:
        print(f"❌ 天气查询失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"天气查询失败: {str(e)}"
        )


@router.post(
    "/route",
    response_model=RouteResponse,
    summary="规划路线",
    description="规划两点之间的路线"
)
async def plan_route(request: RouteRequest):
    """
    规划路线
    
    Args:
        request: 路线规划请求
        
    Returns:
        路线信息
    """
    try:
        # 获取服务实例
        service = get_amap_service()
        
        # 规划路线
        route_data = service.plan_route(
            origin_address=request.origin_address,
            destination_address=request.destination_address,
            origin_city=request.origin_city,
            destination_city=request.destination_city,
            route_type=request.route_type
        )
        if not route_data:
            return RouteResponse(
                success=False,
                message="路线规划失败",
                data=None
            )

        route_info = RouteInfo(
            distance=_first_number_by_keys(route_data, ["distance", "walk_distance", "total_distance"]),
            duration=int(_first_number_by_keys(route_data, ["duration", "time", "cost_time"])),
            route_type=request.route_type,
            description=_route_description(
                route_data,
                request.origin_address,
                request.destination_address,
                request.route_type
            )
        )
        
        return RouteResponse(
            success=True,
            message="路线规划成功",
            data=route_info
        )
        
    except Exception as e:
        print(f"❌ 路线规划失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"路线规划失败: {str(e)}"
        )


@router.post(
    "/context",
    response_model=MapContextResponse,
    summary="查询行程周边场所",
    description="按行程坐标中心查询高德餐饮、商店、周边景点和交通站点",
)
async def get_map_context(request: MapContextRequest):
    center = Location(
        longitude=sum(item.longitude for item in request.locations) / len(request.locations),
        latitude=sum(item.latitude for item in request.locations) / len(request.locations),
    )
    max_distance = max(_distance_m(center, item) for item in request.locations)
    radius = max(3000, min(30000, int(max_distance + 2500)))
    categories = [
        ("餐饮", "餐厅", 7),
        ("商店", "便利店", 5),
        ("周边景点", "景点", 7),
        ("交通", "公交站", 5),
    ]

    service = get_amap_service()
    result: list[MapContextPOI] = []
    seen: set[str] = set()
    for category, keyword, quota in categories:
        if len(result) >= request.limit:
            break
        pois = service.search_poi_around(
            keyword,
            center,
            radius=radius,
            city=request.city,
        )
        added = 0
        for poi in pois:
            if len(result) >= request.limit or added >= quota:
                break
            if poi.id in seen:
                continue
            if any(_distance_m(poi.location, item) < 80 for item in request.locations):
                continue
            seen.add(poi.id)
            result.append(MapContextPOI(
                name=poi.name,
                category=category,
                address=poi.address,
                location=poi.location,
                poi_id=poi.id,
                source="amap_poi",
            ))
            added += 1

    return MapContextResponse(
        success=True,
        center=center,
        radius=radius,
        data=result,
    )


@router.get(
    "/health",
    summary="健康检查",
    description="检查地图服务是否正常"
)
async def health_check():
    """健康检查"""
    try:
        # 检查服务是否可用
        service = get_amap_service()
        
        return {
            "status": "healthy",
            "service": "map-service",
            "api_mode": "http",
            "amap_key_configured": bool(service.settings.amap_api_key)
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"服务不可用: {str(e)}"
        )
