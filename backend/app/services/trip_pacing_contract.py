"""Shared gentle / family pacing markers and named-attraction helpers.

Planner finalize and quality evaluation import the same pure helpers so density
policy cannot drift. Matching is rule-based (not full NLP) and never invents
POI verification trust or scans provider/model descriptions.
"""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from ..models.schemas import Attraction, TripRequest

# Structured preference / chip values (explicit UI or form selections).
STRUCTURED_GENTLE_PREFERENCES: frozenset[str] = frozenset(
    {
        "亲子",
        "儿童同行",
        "父母同行",
        "老人同行",
        "长辈同行",
        "休闲",
        "轻松",
        "轻松出游",
        "缓节奏",
        "慢节奏",
        "少走路",
        "不赶行程",
    }
)

STRUCTURED_PARENT_ELDER_PREFERENCES: frozenset[str] = frozenset(
    {
        "父母同行",
        "老人同行",
        "长辈同行",
    }
)

# Free-text phrases only — bare identity words (儿童/老人/亲子/父母/长辈) are NOT enough.
FREE_TEXT_GENTLE_PHRASES: Tuple[str, ...] = (
    "亲子游",
    "亲子旅行",
    "带孩子",
    "带小孩",
    "带儿童",
    "和孩子一起",
    "儿童同行",
    "一家人出游",
    "带父母",
    "和父母一起",
    "父母同行",
    "和爸妈一起",
    "带爸妈",
    "跟父母",
    "跟爸妈",
    "带老人",
    "老人同行",
    "带长辈",
    "长辈同行",
    "不想太累",
    "不赶行程",
    "每天少安排",
    "少走路",
    "轻松一点",
    "轻松出游",
    "希望轻松",
    "慢一点",
    "慢节奏",
    "缓节奏",
    "松弛一点",
)

FREE_TEXT_PARENT_ELDER_PHRASES: Tuple[str, ...] = (
    "带父母",
    "和父母一起",
    "父母同行",
    "和爸妈一起",
    "带爸妈",
    "跟父母",
    "跟爸妈",
    "带老人",
    "老人同行",
    "带长辈",
    "长辈同行",
)

# Names that are pure generic category labels (exact match only).
GENERIC_ATTRACTION_LABELS: frozenset[str] = frozenset(
    {
        "公园",
        "博物馆",
        "广场",
        "古城",
        "老街",
        "步行街",
        "景区",
        "风景区",
        "商场",
        "酒店",
        "餐厅",
        "动物园",
        "科技馆",
        "美术馆",
        "纪念馆",
    }
)

_POSITIVE_INTENTS: Tuple[str, ...] = (
    "一定要去",
    "必须去",
    "希望去",
    "想去",
    "要去",
    "必去",
    "安排",
    "参观",
    "游览",
    "打卡",
    "看看",
)

_NEGATIVE_MARKERS: Tuple[str, ...] = (
    "不想去",
    "不要去",
    "不去",
    "不安排",
    "避开",
    "排除",
    "别去",
    "不用去",
    "去过了不用再去",
    "不用再去",
)

_QUOTE_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("「", "」"),
    ("『", "』"),
    ("“", "”"),
    ('"', '"'),
    ("'", "'"),
    ("《", "》"),
)


def normalize_user_text(text: str) -> str:
    """Normalize whitespace and common full-width spaces for stable matching."""
    if not text:
        return ""
    normalized = (
        str(text)
        .replace("\u3000", " ")
        .replace("\xa0", " ")
        .replace("＂", '"')
        .replace("＇", "'")
    )
    return " ".join(normalized.split())


def prefers_gentle_pacing(request: TripRequest) -> bool:
    """True only for explicit structured prefs or free-text intent phrases."""
    if _prefers_gentle_from_preferences(request.preferences or []):
        return True
    contract = getattr(request, "semantic_contract", None)
    if (
        contract is not None
        and getattr(contract, "pace", None) is not None
        and contract.pace.is_known()
        and str(contract.pace.value) in {"轻松", "舒缓"}
    ):
        return True
    # Only user-authored words and decided constraints: the recommender writes
    # a machine block into free_text, and reading its 【理由】 prose here would
    # let generated copy assert a pacing the user never chose.
    from .semantic_contract_service import decided_constraint_text

    return _prefers_gentle_from_free_text(
        decided_constraint_text(request.free_text_input)
    )


def mentions_parent_or_elder(request: TripRequest) -> bool:
    """Identity wording for notes only when parents/elders are explicit."""
    for preference in request.preferences or []:
        if normalize_user_text(preference) in STRUCTURED_PARENT_ELDER_PREFERENCES:
            return True
    text = normalize_user_text(request.free_text_input or "")
    return any(phrase in text for phrase in FREE_TEXT_PARENT_ELDER_PHRASES)


def is_user_named_attraction(request: TripRequest, attraction: Attraction) -> bool:
    """Whether free_text explicitly requests this attraction by name.

    Only ``free_text_input`` is scanned. Requires positive mention context and
    rejects pure generic category labels and negative phrasing. Does not mark
    the attraction as map-verified.
    """
    name = (attraction.name or "").strip()
    free_text = normalize_user_text(request.free_text_input or "")
    if len(name) < 2 or not free_text:
        return False
    if name in GENERIC_ATTRACTION_LABELS:
        return False
    return contains_explicit_positive_attraction_mention(free_text, name)


def contains_explicit_positive_attraction_mention(free_text: str, name: str) -> bool:
    """True when free_text positively names the attraction (rule-based).

    Supports both exact name mentions and colloquial short forms that remain a
    contiguous substring of the grounded official POI name (e.g. free_text
    ``想去西湖`` vs name ``西湖风景名胜区``). Generic category labels alone never
    match. Does not use fuzzy edit distance.
    """
    text = normalize_user_text(free_text)
    name = (name or "").strip()
    if len(name) < 2 or not text:
        return False
    if name in GENERIC_ATTRACTION_LABELS:
        return False

    for open_q, close_q in _QUOTE_PAIRS:
        token = f"{open_q}{name}{close_q}"
        start = 0
        while True:
            index = text.find(token, start)
            if index < 0:
                break
            if not _has_negative_context(text, index):
                return True
            start = index + len(token)

    list_pattern = re.compile(
        r"(?:必去|想去|要去|希望去|安排|参观|游览|打卡)[：:]\s*([^\n。；;]+)"
    )
    for match in list_pattern.finditer(text):
        segment = match.group(1)
        if _segment_refers_to_attraction(segment, name) and not _has_negative_context(
            text, match.start()
        ):
            return True

    for intent in _POSITIVE_INTENTS:
        # Exact official name after intent.
        exact = re.compile(re.escape(intent) + r"[一下了]{0,2}" + re.escape(name))
        for match in exact.finditer(text):
            if not _has_negative_context(text, match.start()):
                return True
        # Colloquial token after intent (2–20 chars), matched into official name.
        token_pattern = re.compile(
            re.escape(intent) + r"[一下了]{0,2}([^\s，,。；;、！!？?\n]{2,20})"
        )
        for match in token_pattern.finditer(text):
            token = match.group(1).strip("《》「」『』\"'“”")
            if _token_matches_attraction_name(token, name) and not _has_negative_context(
                text, match.start()
            ):
                return True
    return False


def _segment_refers_to_attraction(segment: str, name: str) -> bool:
    segment = normalize_user_text(segment)
    if not segment:
        return False
    if name in segment:
        return True
    for part in re.split(r"[、，,/\s]+", segment):
        part = part.strip()
        if _token_matches_attraction_name(part, name):
            return True
    return False


def _token_matches_attraction_name(token: str, name: str) -> bool:
    token = (token or "").strip()
    name = (name or "").strip()
    if len(token) < 2 or len(name) < 2:
        return False
    if token in GENERIC_ATTRACTION_LABELS:
        return False
    if token == name:
        return True
    # Colloquial short form contained in official grounded name only.
    return token in name


def cap_day_attractions(
    request: TripRequest,
    attractions: Sequence[Attraction],
    cap: int,
) -> Tuple[List[Attraction], bool]:
    """Cap main attractions with free_text-named priority.

    Returns ``(selected, user_named_overflow)``.

    Named attractions are retained first (original relative order). If named
    count exceeds ``cap``, all named attractions are kept and non-named ones
    are dropped so callers can surface an advisory instead of silent deletion.
    """
    items = list(attractions or [])
    if cap <= 0:
        return [], False
    if len(items) <= cap:
        return items, False

    named = [item for item in items if is_user_named_attraction(request, item)]
    named_ids = {id(item) for item in named}
    others = [item for item in items if id(item) not in named_ids]

    if len(named) > cap:
        return named, True

    selected = named + others
    return selected[:cap], False


def gentle_pacing_note(request: TripRequest) -> str:
    """User-visible density note: neutral unless parents/elders are explicit."""
    if mentions_parent_or_elder(request):
        return (
            "已按父母/老人同行或轻松节奏需求降低每日主景点密度，"
            "并为休息和临时调整预留时间。"
        )
    return "已降低每日主景点密度，并为休息和临时调整预留时间。"


def user_named_overflow_note() -> str:
    return (
        "用户明确点名的景点数量超过当日主景点上限，"
        "已优先保留点名景点，请确认是否接受更满安排或自行取舍。"
    )


def _prefers_gentle_from_preferences(preferences: Sequence[str]) -> bool:
    for preference in preferences or []:
        if normalize_user_text(preference) in STRUCTURED_GENTLE_PREFERENCES:
            return True
    return False


def _prefers_gentle_from_free_text(free_text: str) -> bool:
    text = normalize_user_text(free_text)
    if not text:
        return False
    return any(phrase in text for phrase in FREE_TEXT_GENTLE_PHRASES)


def _has_negative_context(text: str, position: int) -> bool:
    # Include a short span after the match start so phrases like 「不想去」
    # are visible when the positive pattern begins at 「想去」.
    window = text[max(0, position - 8) : min(len(text), position + 4)]
    return any(marker in window for marker in _NEGATIVE_MARKERS)
