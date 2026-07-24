"""Business-calendar helpers for relative date resolution.

Production priority for the "current" civil date:
1. Explicit reference_date (tests / callers)
2. Explicit now converted into user_timezone or business timezone
3. Settings.business_timezone (default Asia/Shanghai)
4. Server local zone only as last resort inside datetime.now(tz)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_BUSINESS_TIMEZONE = "Asia/Shanghai"


def _zone(name: Optional[str]) -> ZoneInfo:
    candidate = (name or "").strip() or DEFAULT_BUSINESS_TIMEZONE
    try:
        return ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_BUSINESS_TIMEZONE)


def get_business_timezone_name() -> str:
    try:
        from ..config import get_settings

        configured = (get_settings().business_timezone or "").strip()
        if configured:
            return configured
    except Exception:
        pass
    return DEFAULT_BUSINESS_TIMEZONE


def resolve_business_date(
    *,
    reference_date: Optional[date] = None,
    now: Optional[datetime] = None,
    user_timezone: Optional[str] = None,
    business_timezone: Optional[str] = None,
) -> date:
    """Resolve the civil date used by weekend / relative-date rules.

    ``reference_date`` always wins (fixed, timezone-free calendar day).
    Otherwise convert ``now`` (default: current instant) into the effective
    timezone: user_timezone → business_timezone → settings → Asia/Shanghai.
    Naive ``now`` is treated as UTC so UTC servers do not silently use local
    wall-clock under a misconfigured host timezone.
    """
    if reference_date is not None:
        return reference_date

    tz = _zone(user_timezone or business_timezone or get_business_timezone_name())
    if now is None:
        return datetime.now(tz).date()

    instant = now
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(tz).date()
