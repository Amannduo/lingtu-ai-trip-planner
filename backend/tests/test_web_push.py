from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import get_settings
from app.services.database_service import execute, fetch_one, fetch_scalar
from app.services.web_push_service import (
    _NoRedirectSession,
    notify_trip_plan_ready,
    save_push_subscription,
    send_trip_ready_push_notifications,
)


def _delete_user(user_id: str) -> None:
    execute(
        "DELETE FROM push_subscriptions WHERE user_id = :user_id",
        {"user_id": user_id},
    )
    execute(
        "DELETE FROM user_profiles WHERE user_id = :user_id",
        {"user_id": user_id},
    )
    execute(
        "DELETE FROM travel_plans WHERE user_id = :user_id",
        {"user_id": user_id},
    )
    execute("DELETE FROM users WHERE user_id = :user_id", {"user_id": user_id})


def _configure_vapid(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "web_push_vapid_public_key",
        "test-public-key",
    )
    monkeypatch.setattr(
        settings,
        "web_push_vapid_private_key",
        "test-private-key",
    )
    monkeypatch.setattr(
        settings,
        "web_push_vapid_subject",
        "mailto:test@example.com",
    )
    monkeypatch.setattr(settings, "web_push_max_retries", 2)
    monkeypatch.setattr(settings, "web_push_retry_delay_seconds", 0.0)
    monkeypatch.setattr(settings, "web_push_ttl_seconds", 300)
    monkeypatch.setattr(settings, "web_push_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "web_push_dns_timeout_seconds", 3.0)
    monkeypatch.setattr(settings, "web_push_max_subscriptions_per_user", 20)
    monkeypatch.setattr(settings, "web_push_delivery_budget_seconds", 30.0)
    monkeypatch.setattr(settings, "web_push_allowed_host_suffixes", "example.test")
    monkeypatch.setattr(
        "app.services.web_push_service._resolve_global_addresses",
        lambda _hostname, _port, _timeout=None: {"8.8.8.8"},
    )


def _subscription(endpoint: str, auth: str = "auth-token") -> dict:
    return {
        "endpoint": endpoint,
        "expirationTime": None,
        "keys": {
            "p256dh": "B" + ("a" * 86),
            "auth": auth,
        },
    }


def test_push_delivery_session_ignores_environment_proxies() -> None:
    with _NoRedirectSession() as session:
        assert session.trust_env is False


def test_push_subscription_routes_are_authenticated_and_idempotent(
    monkeypatch,
) -> None:
    _configure_vapid(monkeypatch)
    suffix = uuid.uuid4().hex[:10]
    endpoint = f"https://push.example.test/subscriptions/{suffix}"
    user_id = ""

    try:
        with TestClient(app) as client:
            key_response = client.get("/api/push/vapid-public-key")
            assert key_response.status_code == 200
            assert key_response.json() == {
                "success": True,
                "public_key": "test-public-key",
            }

            unauthenticated = client.post(
                "/api/push/subscriptions",
                json={"subscription": _subscription(endpoint)},
            )
            assert unauthenticated.status_code == 401

            registered = client.post(
                "/api/auth/register",
                json={
                    "username": f"push_{suffix}",
                    "email": f"push_{suffix}@example.com",
                    "password": "Passw0rd123",
                },
            )
            assert registered.status_code == 201
            user_id = registered.json()["user"]["user_id"]

            created = client.post(
                "/api/push/subscriptions",
                json={"subscription": _subscription(endpoint)},
            )
            assert created.status_code == 200
            assert created.json()["created"] is True
            subscription_id = created.json()["subscription_id"]

            updated = client.post(
                "/api/push/subscriptions",
                json={
                    "subscription": _subscription(
                        endpoint,
                        "rotated-auth-token",
                    )
                },
            )
            assert updated.status_code == 200
            assert updated.json() == {
                "success": True,
                "subscription_id": subscription_id,
                "created": False,
            }
            row = fetch_one(
                "SELECT user_id, auth FROM push_subscriptions "
                "WHERE subscription_id = :sid",
                {"sid": subscription_id},
            )
            assert row == {
                "user_id": user_id,
                "auth": "rotated-auth-token",
            }

            deleted = client.request(
                "DELETE",
                "/api/push/subscriptions",
                json={"subscription": {"endpoint": endpoint}},
            )
            assert deleted.status_code == 200
            assert deleted.json() == {"success": True, "deleted": True}
            deleted_again = client.request(
                "DELETE",
                "/api/push/subscriptions",
                json={"subscription": {"endpoint": endpoint}},
            )
            assert deleted_again.json() == {
                "success": True,
                "deleted": False,
            }
    finally:
        if user_id:
            _delete_user(user_id)


def test_subscription_quota_is_atomic_under_concurrency(monkeypatch) -> None:
    _configure_vapid(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "web_push_max_subscriptions_per_user", 2)
    suffix = uuid.uuid4().hex[:10]
    user_id = ""

    try:
        with TestClient(app) as client:
            registered = client.post(
                "/api/auth/register",
                json={
                    "username": f"quota_{suffix}",
                    "password": "Passw0rd123",
                },
            )
            assert registered.status_code == 201
            user_id = registered.json()["user"]["user_id"]

        endpoints = [
            f"https://push.example.test/quota/{suffix}/{index}"
            for index in range(8)
        ]

        def register(endpoint: str) -> str:
            try:
                saved = save_push_subscription(user_id, _subscription(endpoint))
                return "created" if saved.created else "updated"
            except ValueError as exc:
                assert "limit" in str(exc).lower()
                return "limited"

        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(register, endpoints))

        assert outcomes.count("created") == 2
        assert outcomes.count("limited") == 6
        assert fetch_scalar(
            "SELECT COUNT(*) FROM push_subscriptions WHERE user_id = :user_id",
            {"user_id": user_id},
        ) == 2
    finally:
        if user_id:
            _delete_user(user_id)


def test_delivery_retries_and_removes_invalid_subscriptions(monkeypatch) -> None:
    _configure_vapid(monkeypatch)
    suffix = uuid.uuid4().hex[:10]
    user_id = ""
    gone_endpoint = f"https://push.example.test/gone/{suffix}"
    retry_endpoint = f"https://push.example.test/retry/{suffix}"
    fail_endpoint = f"https://push.example.test/fail/{suffix}"

    class FakePushError(RuntimeError):
        def __init__(self, status_code: int):
            super().__init__(f"push failed: {status_code}")
            self.response = type(
                "FakeResponse",
                (),
                {"status_code": status_code},
            )()

    attempts: dict[str, int] = {}
    delivered_payloads: list[dict] = []

    def fake_delivery(
        subscription_info,
        data,
        private_key,
        subject,
        ttl,
        timeout,
    ):
        endpoint = subscription_info["endpoint"]
        attempts[endpoint] = attempts.get(endpoint, 0) + 1
        if endpoint == gone_endpoint:
            raise FakePushError(410)
        if endpoint == retry_endpoint and attempts[endpoint] == 1:
            raise FakePushError(503)
        if endpoint == fail_endpoint:
            raise FakePushError(503)
        delivered_payloads.append(json.loads(data))
        return object()

    monkeypatch.setattr(
        "app.services.web_push_service._deliver_web_push",
        fake_delivery,
    )

    try:
        with TestClient(app) as client:
            registered = client.post(
                "/api/auth/register",
                json={
                    "username": f"delivery_{suffix}",
                    "password": "Passw0rd123",
                },
            )
            assert registered.status_code == 201
            user_id = registered.json()["user"]["user_id"]

        save_push_subscription(user_id, _subscription(gone_endpoint))
        save_push_subscription(user_id, _subscription(retry_endpoint))
        save_push_subscription(user_id, _subscription(fail_endpoint))
        result = send_trip_ready_push_notifications(
            user_id,
            "\u676d\u5dde",
            "P-123",
        )

        assert result == {
            "configured": True,
            "subscriptions": 3,
            "delivered": 1,
            "removed": 1,
            "failed": 1,
            "retry_attempts": 3,
            "skipped": 0,
        }
        assert attempts[gone_endpoint] == 1
        assert attempts[retry_endpoint] == 2
        assert attempts[fail_endpoint] == 3
        assert delivered_payloads[0]["data"]["url"] == "/result?plan=P-123"
        assert delivered_payloads[0]["data"]["destination"] == "\u676d\u5dde"
        assert fetch_scalar(
            "SELECT COUNT(*) FROM push_subscriptions WHERE endpoint = :endpoint",
            {"endpoint": gone_endpoint},
        ) == 0
        failed = fetch_one(
            "SELECT failure_count FROM push_subscriptions "
            "WHERE endpoint = :endpoint",
            {"endpoint": fail_endpoint},
        )
        assert failed and failed["failure_count"] == 1
    finally:
        if user_id:
            _delete_user(user_id)


def test_delivery_attempts_and_backoff_stay_within_budget(monkeypatch) -> None:
    _configure_vapid(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "web_push_max_retries", 2)
    monkeypatch.setattr(settings, "web_push_retry_delay_seconds", 5.0)
    monkeypatch.setattr(settings, "web_push_timeout_seconds", 10.0)
    monkeypatch.setattr(settings, "web_push_dns_timeout_seconds", 10.0)
    monkeypatch.setattr(settings, "web_push_delivery_budget_seconds", 2.5)

    clock = {"now": 0.0}
    timeouts: list[float] = []
    dns_timeouts: list[float] = []
    sleeps: list[float] = []
    dns_tracking = {"enabled": False}

    def fake_monotonic() -> float:
        return clock["now"]

    def fake_sleep(duration: float) -> None:
        sleeps.append(duration)
        clock["now"] += duration

    def fake_resolve(_hostname, _port, timeout=None):
        if dns_tracking["enabled"]:
            dns_timeouts.append(timeout)
            clock["now"] += 1.0
        return {"8.8.8.8"}

    def fake_delivery(
        _subscription_info,
        _data,
        _private_key,
        _subject,
        _ttl,
        timeout,
    ):
        timeouts.append(timeout)
        clock["now"] += 1.0
        raise RuntimeError("temporary push service failure")

    monkeypatch.setattr(
        "app.services.web_push_service.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr(
        "app.services.web_push_service.time.sleep",
        fake_sleep,
    )
    monkeypatch.setattr(
        "app.services.web_push_service._resolve_global_addresses",
        fake_resolve,
    )
    monkeypatch.setattr(
        "app.services.web_push_service._deliver_web_push",
        fake_delivery,
    )

    suffix = uuid.uuid4().hex[:10]
    user_id = ""
    try:
        with TestClient(app) as client:
            registered = client.post(
                "/api/auth/register",
                json={
                    "username": f"budget_{suffix}",
                    "password": "Passw0rd123",
                },
            )
            assert registered.status_code == 201
            user_id = registered.json()["user"]["user_id"]

        save_push_subscription(
            user_id,
            _subscription(f"https://push.example.test/first/{suffix}"),
        )
        save_push_subscription(
            user_id,
            _subscription(f"https://push.example.test/second/{suffix}"),
        )

        dns_tracking["enabled"] = True
        result = send_trip_ready_push_notifications(
            user_id,
            "成都",
            "P-BUDGET",
        )

        assert dns_timeouts == [pytest.approx(2.5)]
        assert timeouts == [pytest.approx(0.75)]
        assert sleeps == [pytest.approx(0.5)]
        assert result["subscriptions"] == 2
        assert result["delivered"] == 0
        assert result["failed"] == 0
        assert result["retry_attempts"] == 0
        assert result["skipped"] == 2
    finally:
        if user_id:
            _delete_user(user_id)


def test_best_effort_notification_never_raises(monkeypatch) -> None:
    def explode(*_args, **_kwargs):
        raise RuntimeError("simulated database outage")

    monkeypatch.setattr(
        "app.services.web_push_service.send_trip_ready_push_notifications",
        explode,
    )
    result = notify_trip_plan_ready("u_test", "\u82cf\u5dde", "P-FAIL")
    assert result["failed"] == 1
    assert result["delivered"] == 0
    assert result["skipped"] == 0
