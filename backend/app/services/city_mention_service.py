"""Shared city mention extraction for semantic contract and recommenders.

Single source of truth for “去 X / 想去 X / 从 A 去 B” destination detection.
Does not invent destinations when none are mentioned.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Set

from .destination_feasibility_service import SHORT_TRIP_CITY_GRAPH

# Extra well-known destinations not always present in the short-trip graph.
COMMON_DESTINATION_CITIES: frozenset[str] = frozenset(
    {
        "昆明",
        "大理",
        "丽江",
        "西双版纳",
        "乌鲁木齐",
        "喀什",
        "拉萨",
        "三亚",
        "海口",
        "兰州",
        "银川",
        "呼和浩特",
        "哈尔滨",
        "沈阳",
        "南宁",
        "贵阳",
        "桂林",
        "张家界",
        "洛阳",
        "开封",
        "珠海",
        "成都",
        "重庆",
        "西安",
        "杭州",
        "苏州",
        "南京",
        "上海",
        "北京",
        "天津",
        "广州",
        "深圳",
        "厦门",
        "青岛",
        "武汉",
        "长沙",
        "济南",
        "郑州",
        "福州",
        "泉州",
        "宁波",
        "无锡",
        "扬州",
        "嘉兴",
        "湖州",
        "绍兴",
        "黄山",
        "合肥",
        "南昌",
        "太原",
        "石家庄",
        "大连",
        "威海",
        "烟台",
        "秦皇岛",
        "乐山",
        "德阳",
        "汉中",
        "天水",
        "宝鸡",
        "眉县",
        "麟游县",
        "太白县",
        "凤县",
        "武隆",
        "惠州",
        "佛山",
        "漳州",
        "宁德",
        "广元",
    }
)


def known_destination_cities(extra: Optional[Iterable[str]] = None) -> Set[str]:
    known: Set[str] = set(COMMON_DESTINATION_CITIES)
    known.update(SHORT_TRIP_CITY_GRAPH.keys())
    for values in SHORT_TRIP_CITY_GRAPH.values():
        known.update(values)
    if extra:
        for item in extra:
            text = str(item or "").strip()
            if text:
                known.add(text)
    return known


def extract_mentioned_destination(
    text: str,
    origin_city: Optional[str] = None,
    *,
    known_cities: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Return an explicit destination city mentioned in free text, or None.

    Patterns (longest city name first):
    - 目的地X
    - 想去X / 要去X
    - 去X (not preceded by 不/别/没)
    - 从A去B → B when A is origin context
    """
    raw = str(text or "")
    if not raw:
        return None

    known = (
        set(known_cities)
        if known_cities is not None
        else known_destination_cities()
    )
    origin_norm = _normalize_city_label(origin_city)

    # Prefer structured 从A去B so destination is B, not a false origin capture.
    from_to = re.search(
        r"从([\u4e00-\u9fff]{2,12}?)去([\u4e00-\u9fff]{2,12}?)"
        r"(?:玩|旅行|旅游|看看|逛逛)?",
        raw,
    )
    if from_to:
        dest_label = from_to.group(2).strip()
        for city in sorted(known, key=len, reverse=True):
            if dest_label == city or dest_label.startswith(city):
                if _normalize_city_label(city) != origin_norm:
                    return city

    for city in sorted(known, key=len, reverse=True):
        if city == origin_city or _normalize_city_label(city) == origin_norm:
            continue
        if re.search(rf"(?:不|别|没)(?:想|要|打算)?去{re.escape(city)}", raw):
            continue
        if re.search(rf"目的地\s*{re.escape(city)}", raw):
            return city
        if re.search(rf"(?:想|要|打算)去{re.escape(city)}", raw):
            return city
        if re.search(rf"(?<![不没别])去{re.escape(city)}", raw):
            return city
    return None


def _normalize_city_label(city: Optional[str]) -> str:
    value = "".join(str(city or "").split())
    for suffix in ("特别行政区", "自治州", "地区", "市", "县"):
        if value.endswith(suffix) and len(value) > len(suffix) + 1:
            value = value[: -len(suffix)]
            break
    return value
