"""地图服务API路由"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from ...models.schemas import (
    POISearchRequest,
    POISearchResponse,
    RouteInfo,
    RouteRequest,
    RouteResponse,
    WeatherResponse
)
from ...services.amap_service import get_amap_service

router = APIRouter(prefix="/map", tags=["地图服务"])


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
