from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.agents import destination_recommender_agent as destination_module
from app.agents.graph import travel_agent_graph
from app.api.routes.auth import _set_auth_cookie
from app.config import get_settings
from app.services import web_push_service
from app.services.auth_service import _can_claim_legacy_plans
from app.services.unsplash_service import UnsplashService


def _request(scheme: str) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": scheme,
            "path": "/api/auth/login",
            "raw_path": b"/api/auth/login",
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 1234),
            "server": ("testserver", 443 if scheme == "https" else 80),
        }
    )


def test_auth_cookie_is_secure_for_direct_https_even_without_forced_setting(
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "auth_cookie_secure", False)
    response = Response()

    _set_auth_cookie(response, "signed-test-token", _request("https"))

    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_only_bootstrap_admin_can_claim_unowned_legacy_plans() -> None:
    assert _can_claim_legacy_plans(0, "admin") is True
    assert _can_claim_legacy_plans(0, "user") is False
    assert _can_claim_legacy_plans(0, "manager") is False
    assert _can_claim_legacy_plans(1, "admin") is False


def test_push_subscription_cannot_be_reassigned_across_users(monkeypatch) -> None:
    endpoint = "https://push.example.test/subscriptions/unguessable"
    captured: dict[str, object] = {"updated": False}

    monkeypatch.setattr(web_push_service, "init_db", lambda: None)
    monkeypatch.setattr(
        web_push_service,
        "_validate_endpoint",
        lambda raw_endpoint: str(raw_endpoint),
    )

    def fake_fetch_one(sql, _params):
        captured["sql"] = sql
        return {
            "subscription_id": "subscription-a",
            "user_id": "user-a",
            "endpoint": endpoint,
        }

    monkeypatch.setattr(web_push_service, "fetch_one", fake_fetch_one)

    def forbidden_update(*_args, **_kwargs):
        captured["updated"] = True
        raise AssertionError("cross-user subscription must not be updated")

    monkeypatch.setattr(web_push_service, "_update_subscription", forbidden_update)

    with pytest.raises(ValueError, match="cannot be registered"):
        web_push_service.save_push_subscription(
            "user-b",
            {
                "endpoint": endpoint,
                "expirationTime": None,
                "keys": {"p256dh": "new-key", "auth": "new-auth"},
            },
        )

    assert "user_id" in str(captured["sql"])
    assert captured["updated"] is False


def test_push_subscription_insert_race_cannot_reassign_another_user(monkeypatch) -> None:
    endpoint = "https://push.example.test/subscriptions/race"
    rows = iter(
        [
            None,
            {
                "subscription_id": "subscription-a",
                "user_id": "user-a",
                "endpoint": endpoint,
            },
        ]
    )
    updated = {"value": False}

    monkeypatch.setattr(web_push_service, "init_db", lambda: None)
    monkeypatch.setattr(
        web_push_service,
        "_validate_endpoint",
        lambda raw_endpoint: str(raw_endpoint),
    )
    monkeypatch.setattr(web_push_service, "fetch_one", lambda *_args: next(rows))
    monkeypatch.setattr(web_push_service, "fetch_scalar", lambda *_args: 0)

    def lose_insert_race(*_args, **_kwargs):
        raise web_push_service.IntegrityError(
            "INSERT push_subscriptions",
            {},
            RuntimeError("simulated unique endpoint race"),
        )

    monkeypatch.setattr(web_push_service, "execute", lose_insert_race)

    def forbidden_update(*_args, **_kwargs):
        updated["value"] = True
        raise AssertionError("race must not reassign another user's subscription")

    monkeypatch.setattr(web_push_service, "_update_subscription", forbidden_update)

    with pytest.raises(ValueError, match="cannot be registered"):
        web_push_service.save_push_subscription(
            "user-b",
            {
                "endpoint": endpoint,
                "expirationTime": None,
                "keys": {"p256dh": "new-key", "auth": "new-auth"},
            },
        )

    assert updated["value"] is False


def test_provider_failures_do_not_log_exception_secrets(monkeypatch, capsys) -> None:
    secret = "sentinel-provider-api-key"

    class ExplodingClient:
        def get(self, *_args, **_kwargs):
            raise RuntimeError(f"request failed client_id={secret}")

    unsplash = UnsplashService()
    unsplash.access_key = secret
    unsplash._client = ExplodingClient()
    assert unsplash.search_photos("test") == []

    monkeypatch.setattr(
        destination_module,
        "get_llm",
        lambda: (_ for _ in ()).throw(RuntimeError(f"llm key={secret}")),
    )
    destination_module.DestinationRecommenderAgent()

    agent = destination_module.DestinationRecommenderAgent.__new__(
        destination_module.DestinationRecommenderAgent
    )
    monkeypatch.setattr(
        destination_module,
        "get_amap_service",
        lambda: (_ for _ in ()).throw(RuntimeError(f"amap key={secret}")),
    )
    assert agent._search_city_highlights("测试城市", []) == []
    assert agent._weather_summary("测试城市") is None

    monkeypatch.setattr(
        travel_agent_graph,
        "get_travel_plan_data_service",
        lambda: (_ for _ in ()).throw(RuntimeError(f"db dsn={secret}")),
    )
    travel_agent_graph._finish_node({})

    output = capsys.readouterr().out
    assert secret not in output
    assert "RuntimeError" in output
