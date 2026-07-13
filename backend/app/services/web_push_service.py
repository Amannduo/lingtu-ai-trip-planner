"""Authenticated Web Push subscription persistence and best-effort delivery."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

import dns.exception
import dns.resolver
import requests
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from .database_service import execute, fetch_all, fetch_one, fetch_scalar
from .schema import init_db

logger = logging.getLogger(__name__)


class WebPushConfigurationError(RuntimeError):
    """Raised when VAPID configuration is absent or incomplete."""


@dataclass(frozen=True)
class SavedPushSubscription:
    subscription_id: str
    created: bool


class _NoRedirectSession(requests.Session):
    """Push endpoints must never redirect or inherit process proxy settings."""

    def __init__(self) -> None:
        super().__init__()
        self.trust_env = False

    def request(self, method, url, **kwargs):
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


def _allowed_host_suffixes() -> tuple[str, ...]:
    return tuple(
        value.strip().lower().lstrip(".")
        for value in get_settings().web_push_allowed_host_suffixes.split(",")
        if value.strip()
    )


def _resolve_global_addresses(
    hostname: str,
    port: int,
    timeout: float | None = None,
) -> set[str]:
    del port
    try:
        literal_address = ipaddress.ip_address(hostname)
    except ValueError:
        literal_address = None
    if literal_address is not None:
        if not literal_address.is_global:
            raise ValueError(
                "Push endpoint must resolve only to public IP addresses"
            )
        return {str(literal_address)}

    configured_timeout = max(
        0.1,
        min(
            float(get_settings().web_push_dns_timeout_seconds),
            10.0,
        ),
    )
    resolution_timeout = (
        configured_timeout
        if timeout is None
        else min(configured_timeout, float(timeout))
    )
    if resolution_timeout <= 0:
        raise ValueError("Push endpoint DNS resolution budget is exhausted")

    resolver = dns.resolver.Resolver(configure=True)
    resolver.timeout = min(1.0, resolution_timeout)
    deadline = time.monotonic() + resolution_timeout
    addresses: set[str] = set()
    for record_type in ("A", "AAAA"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Push endpoint DNS resolution timed out")
        try:
            answers = resolver.resolve(
                hostname,
                record_type,
                lifetime=remaining,
                search=False,
            )
        except dns.resolver.NoAnswer:
            continue
        except dns.resolver.NXDOMAIN as exc:
            raise ValueError(
                "Push endpoint hostname could not be resolved"
            ) from exc
        except (dns.exception.Timeout, dns.resolver.NoNameservers) as exc:
            raise ValueError(
                "Push endpoint DNS resolution timed out"
            ) from exc
        addresses.update(str(answer.address) for answer in answers)

    if not addresses:
        raise ValueError("Push endpoint hostname could not be resolved")
    if any(
        not ipaddress.ip_address(address).is_global
        for address in addresses
    ):
        raise ValueError(
            "Push endpoint must resolve only to public IP addresses"
        )
    return addresses


def _validate_endpoint(
    raw_endpoint: Any,
    *,
    dns_timeout: float | None = None,
    resolved_addresses: set[str] | None = None,
) -> str:
    endpoint = str(raw_endpoint or "").strip()
    if not endpoint or len(endpoint) > 4096:
        raise ValueError("Push endpoint is missing or too long")
    parsed = urlsplit(endpoint)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme.lower() != "https" or not hostname:
        raise ValueError("Push endpoint must be an HTTPS URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Push endpoint port is invalid") from exc
    if port not in {None, 443}:
        raise ValueError("Push endpoint must use HTTPS port 443")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Push endpoint contains unsupported URL components")

    allowed_suffixes = _allowed_host_suffixes()
    if allowed_suffixes and not any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in allowed_suffixes
    ):
        raise ValueError("Push endpoint hostname is not allowed")

    addresses = _resolve_global_addresses(hostname, 443, dns_timeout)
    if resolved_addresses is not None:
        resolved_addresses.update(addresses)
    return endpoint


def _required_key(keys: Mapping[str, Any], name: str) -> str:
    value = str(keys.get(name) or "").strip()
    if not value or len(value) > 1024:
        raise ValueError(f"Push subscription key {name} is missing or invalid")
    return value


def _normalise_expiration(raw_value: Any) -> int | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, bool):
        raise ValueError("Push subscription expirationTime is invalid")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Push subscription expirationTime is invalid") from exc
    if value < 0:
        raise ValueError("Push subscription expirationTime is invalid")
    return value


def _endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def _update_subscription(
    subscription_id: str,
    user_id: str,
    endpoint: str,
    p256dh: str,
    auth: str,
    expiration_time: int | None,
    user_agent: str | None,
) -> None:
    execute(
        "UPDATE push_subscriptions SET user_id = :user_id, endpoint = :endpoint, "
        "p256dh = :p256dh, auth = :auth, expiration_time = :expiration_time, "
        "user_agent = :user_agent, failure_count = 0, updated_at = CURRENT_TIMESTAMP "
        "WHERE subscription_id = :subscription_id",
        {
            "subscription_id": subscription_id,
            "user_id": user_id,
            "endpoint": endpoint,
            "p256dh": p256dh,
            "auth": auth,
            "expiration_time": expiration_time,
            "user_agent": user_agent,
        },
    )


def save_push_subscription(
    user_id: str,
    subscription: Mapping[str, Any],
    user_agent: str | None = None,
) -> SavedPushSubscription:
    init_db()
    endpoint = _validate_endpoint(subscription.get("endpoint"))
    keys = subscription.get("keys")
    if not isinstance(keys, Mapping):
        raise ValueError("Push subscription keys are missing")
    p256dh = _required_key(keys, "p256dh")
    auth = _required_key(keys, "auth")
    expiration_time = _normalise_expiration(
        subscription.get("expirationTime", subscription.get("expiration_time"))
    )
    safe_user_agent = (user_agent or "").strip()[:512] or None
    digest = _endpoint_hash(endpoint)
    existing = fetch_one(
        "SELECT subscription_id, endpoint FROM push_subscriptions "
        "WHERE endpoint_hash = :endpoint_hash",
        {"endpoint_hash": digest},
    )
    if existing:
        if existing["endpoint"] != endpoint:
            raise ValueError("Push endpoint hash collision")
        subscription_id = str(existing["subscription_id"])
        _update_subscription(
            subscription_id,
            user_id,
            endpoint,
            p256dh,
            auth,
            expiration_time,
            safe_user_agent,
        )
        return SavedPushSubscription(subscription_id=subscription_id, created=False)

    settings = get_settings()
    subscription_limit = max(
        1,
        min(int(settings.web_push_max_subscriptions_per_user), 100),
    )
    current_count = int(
        fetch_scalar(
            "SELECT COUNT(*) FROM push_subscriptions WHERE user_id = :user_id",
            {"user_id": user_id},
        )
        or 0
    )
    if current_count >= subscription_limit:
        raise ValueError("Push subscription limit reached for this user")

    subscription_id = uuid.uuid4().hex
    try:
        execute(
            "INSERT INTO push_subscriptions "
            "(subscription_id, user_id, endpoint_hash, endpoint, p256dh, auth, "
            "expiration_time, user_agent) VALUES "
            "(:subscription_id, :user_id, :endpoint_hash, :endpoint, :p256dh, :auth, "
            ":expiration_time, :user_agent)",
            {
                "subscription_id": subscription_id,
                "user_id": user_id,
                "endpoint_hash": digest,
                "endpoint": endpoint,
                "p256dh": p256dh,
                "auth": auth,
                "expiration_time": expiration_time,
                "user_agent": safe_user_agent,
            },
        )
        return SavedPushSubscription(subscription_id=subscription_id, created=True)
    except IntegrityError:
        existing = fetch_one(
            "SELECT subscription_id, endpoint FROM push_subscriptions "
            "WHERE endpoint_hash = :endpoint_hash",
            {"endpoint_hash": digest},
        )
        if not existing or existing["endpoint"] != endpoint:
            raise
        subscription_id = str(existing["subscription_id"])
        _update_subscription(
            subscription_id,
            user_id,
            endpoint,
            p256dh,
            auth,
            expiration_time,
            safe_user_agent,
        )
        return SavedPushSubscription(subscription_id=subscription_id, created=False)


def delete_push_subscription(user_id: str, raw_endpoint: Any) -> bool:
    init_db()
    endpoint = _validate_endpoint(raw_endpoint)
    deleted = execute(
        "DELETE FROM push_subscriptions WHERE user_id = :user_id "
        "AND endpoint_hash = :endpoint_hash AND endpoint = :endpoint",
        {
            "user_id": user_id,
            "endpoint_hash": _endpoint_hash(endpoint),
            "endpoint": endpoint,
        },
    )
    return deleted > 0


def _configured_settings():
    settings = get_settings()
    values = (
        settings.web_push_vapid_public_key.strip(),
        settings.web_push_vapid_private_key.strip(),
        settings.web_push_vapid_subject.strip(),
    )
    if not all(values):
        raise WebPushConfigurationError("Web Push VAPID is not configured")
    if not values[2].startswith(("mailto:", "https://")):
        raise WebPushConfigurationError(
            "WEB_PUSH_VAPID_SUBJECT must use mailto: or https://"
        )
    return settings


def get_vapid_public_key() -> str:
    return _configured_settings().web_push_vapid_public_key.strip()


def _deliver_web_push(
    subscription_info: dict[str, Any],
    data: str,
    private_key: str,
    subject: str,
    ttl: int,
    timeout: float,
):
    try:
        from pywebpush import webpush
    except ImportError as exc:
        raise WebPushConfigurationError("pywebpush is not installed") from exc
    with _NoRedirectSession() as session:
        return webpush(
            subscription_info=subscription_info,
            data=data,
            vapid_private_key=private_key,
            vapid_claims={"sub": subject},
            ttl=ttl,
            timeout=timeout,
            requests_session=session,
        )


def _exception_status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    raw_status = getattr(response, "status_code", None)
    try:
        return int(raw_status) if raw_status is not None else None
    except (TypeError, ValueError):
        return None


def _remove_subscription(subscription_id: str, user_id: str) -> bool:
    return (
        execute(
            "DELETE FROM push_subscriptions "
            "WHERE subscription_id = :subscription_id AND user_id = :user_id",
            {"subscription_id": subscription_id, "user_id": user_id},
        )
        > 0
    )


def _mark_success(subscription_id: str, user_id: str) -> None:
    execute(
        "UPDATE push_subscriptions SET failure_count = 0, "
        "last_success_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
        "WHERE subscription_id = :subscription_id AND user_id = :user_id",
        {"subscription_id": subscription_id, "user_id": user_id},
    )


def _mark_failure(subscription_id: str, user_id: str) -> None:
    execute(
        "UPDATE push_subscriptions SET failure_count = failure_count + 1, "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE subscription_id = :subscription_id AND user_id = :user_id",
        {"subscription_id": subscription_id, "user_id": user_id},
    )


def _safe_mark_failure(subscription_id: str, user_id: str) -> None:
    try:
        _mark_failure(subscription_id, user_id)
    except Exception:
        logger.exception(
            "Could not record Web Push failure for subscription %s",
            subscription_id,
        )


def send_trip_ready_push_notifications(
    user_id: str,
    destination: str,
    plan_no: str,
) -> dict[str, Any]:
    settings = _configured_settings()
    init_db()
    subscription_limit = max(
        1,
        min(int(settings.web_push_max_subscriptions_per_user), 100),
    )
    subscriptions = fetch_all(
        "SELECT subscription_id, endpoint, p256dh, auth, expiration_time "
        "FROM push_subscriptions WHERE user_id = :user_id "
        "ORDER BY updated_at DESC LIMIT :subscription_limit",
        {"user_id": user_id, "subscription_limit": subscription_limit},
    )
    result: dict[str, Any] = {
        "configured": True,
        "subscriptions": len(subscriptions),
        "delivered": 0,
        "removed": 0,
        "failed": 0,
        "retry_attempts": 0,
        "skipped": 0,
    }
    payload = json.dumps(
        {
            "title": "\u884c\u7a0b\u5df2\u751f\u6210",
            "body": f"{destination}\u65c5\u884c\u8ba1\u5212\u5df2\u751f\u6210\uff0c"
            "\u70b9\u51fb\u67e5\u770b\u8be6\u60c5\u3002",
            "tag": f"trip-{plan_no}",
            "data": {
                "url": f"/result?plan={quote(plan_no, safe='')}",
                "plan_no": plan_no,
                "destination": destination,
            },
        },
        ensure_ascii=False,
    )
    max_retries = max(0, min(int(settings.web_push_max_retries), 5))
    retry_delay = max(
        0.0,
        min(float(settings.web_push_retry_delay_seconds), 5.0),
    )
    ttl = max(0, min(int(settings.web_push_ttl_seconds), 86400))
    timeout = max(1.0, min(float(settings.web_push_timeout_seconds), 60.0))
    delivery_budget = max(
        1.0,
        min(float(settings.web_push_delivery_budget_seconds), 300.0),
    )
    deadline = time.monotonic() + delivery_budget
    now_ms = int(time.time() * 1000)

    for index, subscription in enumerate(subscriptions):
        if time.monotonic() >= deadline:
            result["skipped"] += len(subscriptions) - index
            break
        subscription_id = str(subscription["subscription_id"])
        expiration_time = subscription.get("expiration_time")
        if expiration_time is not None and int(expiration_time) <= now_ms:
            try:
                if _remove_subscription(subscription_id, user_id):
                    result["removed"] += 1
            except Exception:
                result["failed"] += 1
                logger.exception(
                    "Could not remove expired Web Push subscription %s",
                    subscription_id,
                )
            continue

        remaining_budget = deadline - time.monotonic()
        if remaining_budget <= 0:
            result["skipped"] += len(subscriptions) - index
            break
        dns_timeout = min(
            max(
                0.1,
                min(
                    float(settings.web_push_dns_timeout_seconds),
                    10.0,
                ),
            ),
            remaining_budget,
        )
        resolved_addresses: set[str] = set()
        try:
            _validate_endpoint(
                subscription["endpoint"],
                dns_timeout=dns_timeout,
                resolved_addresses=resolved_addresses,
            )
        except ValueError:
            result["failed"] += 1
            _safe_mark_failure(subscription_id, user_id)
            logger.warning(
                "Web Push endpoint validation failed for subscription %s",
                subscription_id,
            )
            continue
        subscription_info = {
            "endpoint": subscription["endpoint"],
            "keys": {
                "p256dh": subscription["p256dh"],
                "auth": subscription["auth"],
            },
        }
        budget_exhausted = False
        for attempt in range(max_retries + 1):
            remaining_budget = deadline - time.monotonic()
            if remaining_budget <= 0:
                budget_exhausted = True
                break
            if attempt > 0:
                result["retry_attempts"] += 1
            try:
                _deliver_web_push(
                    subscription_info,
                    payload,
                    settings.web_push_vapid_private_key.strip(),
                    settings.web_push_vapid_subject.strip(),
                    ttl,
                    min(
                        timeout,
                        remaining_budget / (len(resolved_addresses) + 1),
                    ),
                )
            except Exception as exc:
                status_code = _exception_status_code(exc)
                if status_code in {404, 410}:
                    try:
                        if _remove_subscription(subscription_id, user_id):
                            result["removed"] += 1
                    except Exception:
                        result["failed"] += 1
                        logger.exception(
                            "Could not remove invalid Web Push subscription %s",
                            subscription_id,
                        )
                    break
                retryable = (
                    status_code is None
                    or status_code in {408, 425, 429}
                    or status_code >= 500
                )
                if (
                    retryable
                    and attempt < max_retries
                    and not isinstance(
                        exc,
                        (ValueError, TypeError, WebPushConfigurationError),
                    )
                ):
                    if retry_delay:
                        remaining_budget = deadline - time.monotonic()
                        if remaining_budget <= 0:
                            budget_exhausted = True
                            break
                        time.sleep(
                            min(
                                retry_delay * (2**attempt),
                                remaining_budget,
                            )
                        )
                    continue
                result["failed"] += 1
                _safe_mark_failure(subscription_id, user_id)
                logger.warning(
                    "Web Push delivery failed for subscription %s (status=%s)",
                    subscription_id,
                    status_code,
                )
                break
            else:
                result["delivered"] += 1
                try:
                    _mark_success(subscription_id, user_id)
                except Exception:
                    logger.exception(
                        "Could not record Web Push success for subscription %s",
                        subscription_id,
                    )
                break
        if budget_exhausted:
            result["skipped"] += len(subscriptions) - index
            break
    return result


def notify_trip_plan_ready(
    user_id: str,
    destination: str,
    plan_no: str,
) -> dict[str, Any]:
    """Never let push delivery failure escape into the trip persistence flow."""
    try:
        return send_trip_ready_push_notifications(user_id, destination, plan_no)
    except WebPushConfigurationError as exc:
        logger.info("Web Push skipped for plan %s: %s", plan_no, exc)
        return {
            "configured": False,
            "subscriptions": 0,
            "delivered": 0,
            "removed": 0,
            "failed": 0,
            "retry_attempts": 0,
            "skipped": 0,
        }
    except Exception:
        logger.exception(
            "Best-effort Web Push notification failed for plan %s",
            plan_no,
        )
        return {
            "configured": False,
            "subscriptions": 0,
            "delivered": 0,
            "removed": 0,
            "failed": 1,
            "retry_attempts": 0,
            "skipped": 0,
        }
