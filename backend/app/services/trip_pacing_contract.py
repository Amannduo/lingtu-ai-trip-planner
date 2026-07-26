"""Shared gentle / family / elder pacing markers and attraction-cap helpers.

Planner finalize and quality evaluation import the same markers so density
policy cannot drift. User-named attraction priority is based only on
``free_text_input`` substring matches against already-planned attraction names;
it does not scan provider blurbs and never invents POI verification trust.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from ..models.schemas import Attraction, TripRequest

# Explicit user preference / free-text signals only (not POI descriptions).
GENTLE_PACING_MARKERS: Tuple[str, ...] = (
    "父母",
    "爸妈",
    "老人",
    "长辈",
    "亲子",
    "儿童",
    "不想太累",
    "不赶行程",
    "每天少安排",
    "少走路",
    "轻松",
    "松弛",
    "休闲",
    "慢一点",
    "慢节奏",
    "缓节奏",
    "避暑",
)

PARENT_ELDER_MARKERS: Tuple[str, ...] = (
    "父母",
    "爸妈",
    "老人",
    "长辈",
)


def pacing_request_text(request: TripRequest) -> str:
    return f"{request.free_text_input or ''} {' '.join(request.preferences or [])}"


def prefers_gentle_pacing(request: TripRequest) -> bool:
    """True when the user explicitly signals gentle / family / elder pacing."""
    text = pacing_request_text(request)
    return any(marker in text for marker in GENTLE_PACING_MARKERS)


def mentions_parent_or_elder(request: TripRequest) -> bool:
    text = pacing_request_text(request)
    return any(marker in text for marker in PARENT_ELDER_MARKERS)


def is_user_named_attraction(request: TripRequest, attraction: Attraction) -> bool:
    """Whether free_text explicitly names this attraction.

    Match is name-in-free-text only. Does not treat preferences, provider
    descriptions, or POI metadata as user requirements, and does not mark the
    attraction as map-verified.
    """
    name = (attraction.name or "").strip()
    free_text = (request.free_text_input or "").strip()
    if len(name) < 2 or not free_text:
        return False
    return name in free_text


def cap_day_attractions(
    request: TripRequest,
    attractions: Sequence[Attraction],
    cap: int,
) -> Tuple[List[Attraction], bool]:
    """Cap main attractions with free_text-named priority.

    Returns ``(selected, user_named_overflow)``.

    - Named attractions (name appears in free_text) are kept first in original
      relative order.
    - Remaining slots are filled from non-named attractions in original order.
    - If named count exceeds ``cap``, all named attractions are retained and
      non-named ones are dropped; ``user_named_overflow`` is True so callers can
      surface an advisory rather than silently claiming a gentle schedule.
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
