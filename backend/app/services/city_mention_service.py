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


# Shared negation vocabulary. Lives here because this is the leaf module both
# the contract service and destination extraction depend on — one list, so
# "不能去青岛" cannot be a rejection in one place and a choice in another.
# Bare 不 / 别 are excluded on purpose: "不想太累" and "特别喜欢" are not negations.
NEGATORS: tuple[str, ...] = (
    "不想", "不要", "不去", "不能", "不用", "不太想", "不打算", "不喜欢",
    "不考虑", "没想", "没打算", "别去", "别选", "别安排", "无需", "避开",
    "讨厌", "排除", "拒绝",
)
NEGATOR_ALTERNATION = "|".join(sorted(NEGATORS, key=len, reverse=True))
# A short adverbial or companion phrase may sit between the negator and the
# verb ("不想再去大同", "不想跟他们去大同"). The filler list is deliberately
# closed so "不想太累去大同" keeps 大同 as the destination.
NEGATION_FILLER = r"(?:(?:再|又|还|一定|专门|特意|马上|这次)|(?:跟|和|与)[^，,。；;]{1,3})?"
_NEGATION_PREFIX_RE = re.compile(
    r"(?:" + NEGATOR_ALTERNATION + r")" + NEGATION_FILLER + r"[去到看玩的往选]{0,2}\s*$"
)


def is_negated_at(text: str, start: int, *, window: int = 10) -> bool:
    """Whether the token starting at *start* is directly negated."""
    return bool(_NEGATION_PREFIX_RE.search(text[max(0, start - window):start]))


def _destination_mentions(text: str, city: str) -> list[re.Match[str]]:
    """All "去<city>" / "目的地<city>" mentions in *text*."""
    escaped = re.escape(city)
    pattern = rf"(?:目的地\s*|(?:想|要|打算|准备)?去\s*){escaped}"
    return list(re.finditer(pattern, text))


def _mention_is_negated(text: str, match: re.Match[str], city: str) -> bool:
    """Negation sits before the whole phrase, so anchor on the city itself."""
    return is_negated_at(text, match.end() - len(city))


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
        mentions = _destination_mentions(raw, city)
        # Only rule the city out when *every* mention of it is negated:
        # "不想去大同，还是去大同吧" ends on an affirmative choice.
        if any(not _mention_is_negated(raw, match, city) for match in mentions):
            return city
    return None


def _normalize_city_label(city: Optional[str]) -> str:
    value = "".join(str(city or "").split())
    for suffix in ("特别行政区", "自治州", "地区", "市", "县"):
        if value.endswith(suffix) and len(value) > len(suffix) + 1:
            value = value[: -len(suffix)]
            break
    return value
