"""Durable abuse limits for real SMTP delivery attempts."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..config import get_settings
from .database_service import engine
from .schema import init_db

logger = logging.getLogger(__name__)


class _QuotaExceeded(RuntimeError):
    def __init__(
        self,
        scope: str,
        message: str,
        retry_after_seconds: int,
    ) -> None:
        super().__init__(message)
        self.scope = scope
        self.retry_after_seconds = retry_after_seconds


def _decision(
    allowed: bool,
    message: str = "",
    scope: str | None = None,
    retry_after_seconds: int = 0,
) -> dict[str, Any]:
    return {
        "allowed": allowed,
        "message": message,
        "scope": scope,
        "retry_after_seconds": retry_after_seconds,
    }


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _normalise_ip(raw_value: str) -> str:
    value = raw_value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return value[:128]


def _scope_hash(secret: str, scope: str, value: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"{scope}:{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _increment_scope(
    connection,
    *,
    scope: str,
    scope_hash: str,
    period_start: datetime,
    period_end: datetime,
    limit: int,
) -> bool:
    count = connection.execute(
        text(
            "INSERT INTO email_send_quotas "
            "(scope_type, scope_hash, period_start, send_count, expires_at) "
            "VALUES (:scope_type, :scope_hash, :period_start, 1, :expires_at) "
            "ON CONFLICT (scope_type, scope_hash, period_start) DO UPDATE SET "
            "send_count = email_send_quotas.send_count + 1, "
            "expires_at = excluded.expires_at, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE email_send_quotas.send_count < :send_limit "
            "RETURNING send_count"
        ),
        {
            "scope_type": scope,
            "scope_hash": scope_hash,
            "period_start": _utc_text(period_start),
            "expires_at": _utc_text(period_end),
            "send_limit": limit,
        },
    ).scalar_one_or_none()
    return count is not None


def consume_email_quota(
    user_id: str | None,
    client_ip: str | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.email_quota_enabled:
        return _decision(True)

    safe_user_id = (user_id or "").strip()[:128]
    safe_client_ip = _normalise_ip(client_ip or "")
    if not safe_user_id or not safe_client_ip:
        return _decision(
            False,
            "\u90ae\u4ef6\u53d1\u9001\u4e0a\u4e0b\u6587\u4e0d\u5b8c\u6574\uff0c\u5df2\u963b\u6b62\u771f\u5b9e\u6295\u9012\u3002",
            "context",
        )

    secret = settings.auth_secret_key.strip()
    if len(secret) < 32:
        return _decision(
            False,
            "\u90ae\u4ef6\u9650\u989d\u670d\u52a1\u914d\u7f6e\u4e0d\u5b8c\u6574\uff0c\u5df2\u963b\u6b62\u771f\u5b9e\u6295\u9012\u3002",
            "configuration",
        )

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_start = current.replace(minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    hour_end = hour_start + timedelta(hours=1)
    user_limit = max(1, min(int(settings.email_user_daily_limit), 1000))
    ip_limit = max(1, min(int(settings.email_ip_hourly_limit), 5000))

    scopes = (
        (
            "user",
            _scope_hash(secret, "user", safe_user_id),
            day_start,
            day_end,
            user_limit,
            "\u4eca\u65e5\u90ae\u4ef6\u53d1\u9001\u6b21\u6570\u5df2\u8fbe\u4e0a\u9650\uff0c\u8bf7\u660e\u5929\u518d\u8bd5\u3002",
        ),
        (
            "ip",
            _scope_hash(secret, "ip", safe_client_ip),
            hour_start,
            hour_end,
            ip_limit,
            "\u5f53\u524d\u7f51\u7edc\u7684\u90ae\u4ef6\u53d1\u9001\u9891\u7387\u5df2\u8fbe\u4e0a\u9650\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002",
        ),
    )

    try:
        init_db()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM email_send_quotas "
                    "WHERE expires_at <= :current_time"
                ),
                {"current_time": _utc_text(current)},
            )
            for (
                scope,
                digest,
                period_start,
                period_end,
                limit,
                message,
            ) in scopes:
                if not _increment_scope(
                    connection,
                    scope=scope,
                    scope_hash=digest,
                    period_start=period_start,
                    period_end=period_end,
                    limit=limit,
                ):
                    retry_after = max(
                        1,
                        int((period_end - current).total_seconds()),
                    )
                    raise _QuotaExceeded(
                        scope,
                        message,
                        retry_after,
                    )
    except _QuotaExceeded as exc:
        return _decision(
            False,
            str(exc),
            exc.scope,
            exc.retry_after_seconds,
        )
    except SQLAlchemyError as exc:
        logger.warning("Email quota persistence failed: %s", type(exc).__name__)
        return _decision(
            False,
            "\u90ae\u4ef6\u9650\u989d\u670d\u52a1\u6682\u65f6\u4e0d\u53ef\u7528\uff0c\u5df2\u963b\u6b62\u771f\u5b9e\u6295\u9012\u3002",
            "unavailable",
        )

    return _decision(True)
