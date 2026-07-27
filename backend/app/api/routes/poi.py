"""POI相关API路由"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from ...services.amap_service import get_amap_service
from ...services.unsplash_service import get_unsplash_service

router = APIRouter(prefix="/poi", tags=["POI"])

_CONTROL_CHARS = re.compile(r"[\x00-\x1f]")
_POI_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CITY_MAX = 64
_KEYWORDS_MAX = 100
_NAME_MAX = 200


def _clean_text(value: str, *, field: str, max_length: int) -> str:
    raw = value or ""
    if _CONTROL_CHARS.search(raw):
        raise HTTPException(status_code=422, detail=f"{field}包含非法字符")
    text = raw.strip(" \t")
    if not text:
        raise HTTPException(status_code=422, detail=f"{field}不能为空")
    if len(text) > max_length:
        raise HTTPException(status_code=422, detail=f"{field}过长")
    return text


def _safe_provider_error(label: str) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail=f"{label}暂时不可用，请稍后重试。",
    )


class POIDetailResponse(BaseModel):
    """POI详情响应"""

    success: bool
    message: str
    data: Optional[dict] = None


@router.get(
    "/detail/{poi_id}",
    response_model=POIDetailResponse,
    summary="获取POI详情",
    description="根据POI ID获取详细信息,包括图片",
)
def get_poi_detail(
    poi_id: str = Path(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
):
    try:
        poi_id = poi_id.strip()
        if not _POI_ID_RE.fullmatch(poi_id):
            raise HTTPException(status_code=422, detail="POI ID 无效")
        amap_service = get_amap_service()
        result = amap_service.get_poi_detail(poi_id)
        return POIDetailResponse(
            success=True,
            message="获取POI详情成功",
            data=result if result else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[poi] detail lookup failed: {type(exc).__name__}")
        raise _safe_provider_error("POI 详情") from exc


@router.get(
    "/search",
    summary="搜索POI",
    description="根据关键词搜索POI",
)
def search_poi(
    keywords: str = Query(..., min_length=1, max_length=_KEYWORDS_MAX),
    city: str = Query("北京", min_length=1, max_length=_CITY_MAX),
):
    try:
        keywords = _clean_text(keywords, field="关键词", max_length=_KEYWORDS_MAX)
        city = _clean_text(city, field="城市", max_length=_CITY_MAX)
        amap_service = get_amap_service()
        result = amap_service.search_poi(keywords, city)
        return {
            "success": True,
            "message": "搜索成功",
            "data": result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[poi] search failed: {type(exc).__name__}")
        raise _safe_provider_error("POI 搜索") from exc


@router.get(
    "/photo",
    summary="获取景点图片",
    description="根据景点名称从Unsplash获取图片",
)
def get_attraction_photo(
    name: str = Query(..., min_length=1, max_length=_NAME_MAX),
):
    try:
        name = _clean_text(name, field="景点名称", max_length=_NAME_MAX)
        unsplash_service = get_unsplash_service()
        photo_url = unsplash_service.get_photo_url(f"{name} China landmark")
        if not photo_url:
            photo_url = unsplash_service.get_photo_url(name)
        return {
            "success": True,
            "message": "获取图片成功",
            "data": {
                "name": name,
                "photo_url": photo_url,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[poi] photo lookup failed: {type(exc).__name__}")
        raise _safe_provider_error("景点图片") from exc
