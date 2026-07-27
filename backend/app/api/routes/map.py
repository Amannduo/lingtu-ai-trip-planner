"""地图服务API路由"""

from __future__ import annotations

import math
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ...models.schemas import (
    Location,
    MapContextPOI,
    POISearchResponse,
    RouteInfo,
    RouteRequest,
    RouteResponse,
    WeatherResponse,
)
from ...services.amap_service import get_amap_service

router = APIRouter(prefix="/map", tags=["地图服务"])

# All C0 controls including CR/LF/TAB/NUL — never accepted in Map query text.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f]")
_CITY_MAX = 64
_KEYWORDS_MAX = 100
_ADDRESS_MAX = 300


def _clean_text(value: str, *, field: str, max_length: int) -> str:
    """Strip spaces, reject control characters, enforce non-empty length."""
    raw = value or ""
    # Reject CR/LF/NUL and other controls before whitespace stripping so that
    # leading/trailing control bytes cannot be silently removed by strip().
    if _CONTROL_CHARS.search(raw):
        raise HTTPException(status_code=422, detail=f"{field}包含非法字符")
    text = raw.strip(" \t")
    if not text:
        raise HTTPException(status_code=422, detail=f"{field}不能为空")
    if len(text) > max_length:
        raise HTTPException(status_code=422, detail=f"{field}过长")
    return text


def _safe_provider_error(label: str) -> HTTPException:
    """Map provider failures to a stable client-safe 502 without leaking details."""
    return HTTPException(
        status_code=502,
        detail=f"{label}暂时不可用，请稍后重试。",
    )


class MapContextRequest(BaseModel):
    city: str = Field(..., min_length=1, max_length=_CITY_MAX, description="目的地城市")
    locations: list[Location] = Field(..., min_length=1, max_length=100)
    limit: int = Field(default=24, ge=8, le=32)

    @field_validator("city")
    @classmethod
    def _validate_city(cls, value: str) -> str:
        raw = value or ""
        if _CONTROL_CHARS.search(raw):
            raise ValueError("城市包含非法字符")
        text = raw.strip(" \t")
        if not text:
            raise ValueError("城市不能为空")
        return text


class MapContextResponse(BaseModel):
    success: bool
    center: Location
    radius: int
    data: list[MapContextPOI]


class BoundedRouteRequest(RouteRequest):
    """RouteRequest with tighter optional city bounds for HTTP entry."""

    origin_city: Optional[str] = Field(default=None, max_length=_CITY_MAX)
    destination_city: Optional[str] = Field(default=None, max_length=_CITY_MAX)

    @field_validator(
        "origin_address",
        "destination_address",
        "origin_city",
        "destination_city",
        mode="before",
    )
    @classmethod
    def _normalize_route_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        # Reject controls before stripping so CR/LF cannot be silently removed.
        if _CONTROL_CHARS.search(value):
            raise ValueError("字段包含非法字符")
        text = value.strip(" \t")
        return text or None


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
        if not math.isfinite(value):
            return 0
        return float(value)
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
        "transit": "公共交通",
    }.get(route_type, "交通")
    # Truncate labels in description to avoid echoing huge user strings.
    origin_label = (origin_name or "")[:80]
    dest_label = (destination_name or "")[:80]
    return f"从{origin_label}前往{dest_label}，建议使用{route_label}衔接。"


@router.get(
    "/poi",
    response_model=POISearchResponse,
    summary="搜索POI",
    description="根据关键词搜索POI(兴趣点)",
)
def search_poi(
    keywords: str = Query(..., min_length=1, max_length=_KEYWORDS_MAX, description="搜索关键词"),
    city: str = Query(..., min_length=1, max_length=_CITY_MAX, description="城市"),
    citylimit: bool = Query(True, description="是否限制在城市范围内"),
):
    """Search POI via the synchronous AMap client (threadpool-bound by FastAPI)."""
    try:
        keywords = _clean_text(keywords, field="关键词", max_length=_KEYWORDS_MAX)
        city = _clean_text(city, field="城市", max_length=_CITY_MAX)
        service = get_amap_service()
        pois = service.search_poi(keywords, city, citylimit)
        return POISearchResponse(
            success=True,
            message="POI搜索成功",
            data=pois,
        )
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[map] POI search failed: {type(exc).__name__}")
        raise _safe_provider_error("POI 搜索") from exc


@router.get(
    "/weather",
    response_model=WeatherResponse,
    summary="查询天气",
    description="查询指定城市的天气信息",
)
def get_weather(
    city: str = Query(..., min_length=1, max_length=_CITY_MAX, description="城市名称"),
):
    try:
        city = _clean_text(city, field="城市", max_length=_CITY_MAX)
        service = get_amap_service()
        weather_info = service.get_weather(city)
        return WeatherResponse(
            success=True,
            message="天气查询成功",
            data=weather_info,
        )
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[map] weather query failed: {type(exc).__name__}")
        raise _safe_provider_error("天气查询") from exc


@router.post(
    "/route",
    response_model=RouteResponse,
    summary="规划路线",
    description="规划两点之间的路线",
)
def plan_route(request: BoundedRouteRequest):
    try:
        origin = _clean_text(
            request.origin_address, field="起点地址", max_length=_ADDRESS_MAX
        )
        destination = _clean_text(
            request.destination_address, field="终点地址", max_length=_ADDRESS_MAX
        )
        origin_city = None
        destination_city = None
        if request.origin_city:
            origin_city = _clean_text(
                request.origin_city, field="起点城市", max_length=_CITY_MAX
            )
        if request.destination_city:
            destination_city = _clean_text(
                request.destination_city, field="终点城市", max_length=_CITY_MAX
            )

        service = get_amap_service()
        route_data = service.plan_route(
            origin_address=origin,
            destination_address=destination,
            origin_city=origin_city,
            destination_city=destination_city,
            route_type=request.route_type,
        )
        if not route_data:
            # Empty/failed provider result — not a fabricated success payload.
            return RouteResponse(
                success=False,
                message="路线规划失败",
                data=None,
            )

        route_info = RouteInfo(
            distance=_first_number_by_keys(
                route_data, ["distance", "walk_distance", "total_distance"]
            ),
            duration=int(
                _first_number_by_keys(route_data, ["duration", "time", "cost_time"])
            ),
            route_type=request.route_type,
            description=_route_description(
                route_data,
                origin,
                destination,
                request.route_type,
            ),
        )
        return RouteResponse(
            success=True,
            message="路线规划成功",
            data=route_info,
        )
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[map] route planning failed: {type(exc).__name__}")
        raise _safe_provider_error("路线规划") from exc


@router.post(
    "/context",
    response_model=MapContextResponse,
    summary="查询行程周边场所",
    description="按行程坐标中心查询高德餐饮、商店、周边景点和交通站点",
)
def get_map_context(request: MapContextRequest):
    try:
        city = _clean_text(request.city, field="城市", max_length=_CITY_MAX)
        # Location already enforces finite lon/lat ranges via schema.
        center = Location(
            longitude=sum(item.longitude for item in request.locations)
            / len(request.locations),
            latitude=sum(item.latitude for item in request.locations)
            / len(request.locations),
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
                city=city,
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
                result.append(
                    MapContextPOI(
                        name=poi.name,
                        category=category,
                        address=poi.address,
                        location=poi.location,
                        poi_id=poi.id,
                        source="amap_poi",
                    )
                )
                added += 1

        return MapContextResponse(
            success=True,
            center=center,
            radius=radius,
            data=result,
        )
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[map] context query failed: {type(exc).__name__}")
        raise _safe_provider_error("周边场所查询") from exc


@router.get(
    "/health",
    summary="健康检查",
    description="检查地图服务是否正常",
)
def health_check():
    """Health check — reports only whether a key is configured, never the value."""
    try:
        service = get_amap_service()
        return {
            "status": "healthy",
            "service": "map-service",
            "api_mode": "http",
            "amap_key_configured": bool(service.settings.amap_api_key),
        }
    except Exception as exc:
        print(f"[map] health check failed: {type(exc).__name__}")
        raise HTTPException(
            status_code=503,
            detail="地图服务暂时不可用，请稍后重试。",
        ) from exc
