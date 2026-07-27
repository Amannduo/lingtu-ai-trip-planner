from __future__ import annotations

import smtplib
import uuid

from fastapi.testclient import TestClient

from app.api.main import app
from app.config import get_settings
from app.models.schemas import TripPlan, TripPlanQualityResult
from app.services.database_service import execute
from app.services.trip_email_service import deliver_trip_plan_email, render_trip_plan_text
from app.tools.send_email_tool import send_email


def _delete_user(user_id: str) -> None:
    execute("DELETE FROM user_profiles WHERE user_id = :user_id", {"user_id": user_id})
    execute("DELETE FROM travel_plans WHERE user_id = :user_id", {"user_id": user_id})
    execute("DELETE FROM users WHERE user_id = :user_id", {"user_id": user_id})


def test_email_registration_login_and_update() -> None:
    suffix = uuid.uuid4().hex[:10]
    username = f"user_{suffix}"
    email = f"{suffix}@example.com"
    updated_email = f"updated_{suffix}@example.com"
    user_id = ""

    try:
        with TestClient(app) as client:
            registered = client.post(
                "/api/auth/register",
                json={
                    "username": username,
                    "email": email,
                    "password": "Passw0rd123",
                },
            )
            assert registered.status_code == 201
            user = registered.json()["user"]
            user_id = user["user_id"]
            assert user["email"] == email
            assert client.get("/api/auth/me").status_code == 200

            cookie_name = get_settings().auth_cookie_name
            old_token = client.cookies.get(cookie_name)
            assert old_token
            assert client.post("/api/auth/logout").status_code == 200
            # Logout revokes every access token for this user (all sessions).
            client.cookies.set(cookie_name, old_token)
            assert client.get("/api/auth/me").status_code == 401
            client.cookies.delete(cookie_name)

            logged_in = client.post(
                "/api/auth/login",
                json={"username": email, "password": "Passw0rd123"},
            )
            assert logged_in.status_code == 200
            assert logged_in.json()["user"]["user_id"] == user_id

            updated = client.patch("/api/auth/me", json={"email": updated_email})
            assert updated.status_code == 200
            assert updated.json()["user"]["email"] == updated_email
    finally:
        if user_id:
            _delete_user(user_id)


def test_trip_email_dry_run(monkeypatch) -> None:
    monkeypatch.setenv("SEND_REAL_EMAILS", "false")
    plan = TripPlan(
        city="杭州",
        start_date="2026-08-01",
        end_date="2026-08-01",
        days=[],
        overall_suggestions="出发前确认天气和开放时间。",
    )

    body = render_trip_plan_text(plan, "P-TEST")
    assert "杭州" in body
    assert "P-TEST" in body

    result = deliver_trip_plan_email("traveler@example.com", plan, "P-TEST")
    assert result["sent"] is False
    assert result["dry_run"] is True
    assert result["blocked"] is False
    assert result["to"] == "traveler@example.com"


def test_plan_route_uses_bound_email(monkeypatch) -> None:
    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            from app.models.schemas import TripPlanQualityResult

            return TripPlan(
                city="苏州",
                start_date="2026-09-01",
                end_date="2026-09-01",
                days=[],
                overall_suggestions="按预约时间抵达景点。",
                quality=TripPlanQualityResult(
                    status="passed",
                    score=90,
                    publishable=True,
                    review_required=False,
                ),
            )

    monkeypatch.setenv("SEND_REAL_EMAILS", "false")
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )

    suffix = uuid.uuid4().hex[:10]
    email = f"trip_{suffix}@example.com"
    user_id = ""

    try:
        with TestClient(app) as client:
            registered = client.post(
                "/api/auth/register",
                json={
                    "username": f"trip_{suffix}",
                    "email": email,
                    "password": "Passw0rd123",
                },
            )
            assert registered.status_code == 201
            user_id = registered.json()["user"]["user_id"]

            response = client.post(
                "/api/trip/plan",
                json={
                    "city": "苏州",
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-01",
                    "travel_days": 1,
                    "travelers": 1,
                    "transportation": "公共交通",
                    "accommodation": "舒适型酒店",
                    "preferences": ["历史文化"],
                    "email_on_completion": True,
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["plan_no"]
            assert payload["email_delivery"]["dry_run"] is True
            assert payload["email_delivery"]["blocked"] is False
            assert payload["email_delivery"]["to"] == email

            history = client.get("/api/trip/history")
            assert history.status_code == 200
            history_payload = history.json()
            assert any(
                trip["plan_no"] == payload["plan_no"]
                for trip in history_payload["trips"]
            )

            detail = client.get(f"/api/trip/history/{payload['plan_no']}")
            assert detail.status_code == 200
            assert detail.json()["plan_no"] == payload["plan_no"]
            assert detail.json()["data"]["city"] == "苏州"
    finally:
        if user_id:
            _delete_user(user_id)


def test_smtp_authentication_failure_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "email_quota_enabled", False)

    class RejectingSMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def login(self, _username, _password):
            raise smtplib.SMTPAuthenticationError(535, b"authentication rejected")

        def send_message(self, _message):
            raise AssertionError("send_message must not run after failed login")

    monkeypatch.setenv("SEND_REAL_EMAILS", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.qq.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USERNAME", "sender@qq.com")
    monkeypatch.setenv("SMTP_PASSWORD", "local-test-authorization-code")
    monkeypatch.setenv("SMTP_FROM", "sender@qq.com")
    monkeypatch.setenv("SMTP_SSL", "true")
    monkeypatch.setattr(
        "app.tools.send_email_tool.smtplib.SMTP_SSL",
        RejectingSMTP,
    )

    result = send_email("recipient@example.com", "SMTP failure test", "body")
    assert result["sent"] is False
    assert result["dry_run"] is False
    assert result["to"] == "recipient@example.com"
    assert "SMTP 认证失败" in result["message"]
    assert "local-test-authorization-code" not in result["message"]


def test_admin_role_forgery_is_rejected_and_username_login_works() -> None:
    suffix = uuid.uuid4().hex[:10]
    username = f"role_{suffix}"
    password = "Passw0rd123"
    user_id = ""

    try:
        with TestClient(app) as client:
            forged = client.post(
                "/api/auth/register",
                json={
                    "username": username,
                    "password": password,
                    "role": "admin",
                },
            )
            assert forged.status_code == 400
            assert client.get("/api/auth/me").status_code == 401

            registered = client.post(
                "/api/auth/register",
                json={
                    "username": username,
                    "password": password,
                    "role": "user",
                },
            )
            assert registered.status_code == 201
            user = registered.json()["user"]
            user_id = user["user_id"]
            assert user["role"] == "user"

            logged_out = client.post("/api/auth/logout")
            assert logged_out.status_code == 200
            assert client.get("/api/auth/me").status_code == 401

            logged_in = client.post(
                "/api/auth/login",
                json={
                    "username": username,
                    "password": password,
                },
            )
            assert logged_in.status_code == 200
            assert logged_in.json()["user"]["role"] == "user"
            current = client.get("/api/auth/me")
            assert current.status_code == 200
            assert current.json()["user"]["role"] == "user"
    finally:
        if user_id:
            _delete_user(user_id)


def test_invalid_smtp_config_is_reported_without_raising(monkeypatch) -> None:
    monkeypatch.setenv("SEND_REAL_EMAILS", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.qq.com")
    monkeypatch.setenv("SMTP_PORT", "not-a-port")
    monkeypatch.setenv("SMTP_USERNAME", "sender@qq.com")
    monkeypatch.setenv("SMTP_PASSWORD", "local-test-authorization-code")

    result = send_email("recipient@example.com", "config test", "body")
    assert result["sent"] is False
    assert result["dry_run"] is False
    assert "SMTP 配置无效" in result["message"]
    assert "local-test-authorization-code" not in result["message"]


def test_unexpected_email_failure_does_not_fail_saved_trip(monkeypatch) -> None:
    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            from app.models.schemas import TripPlanQualityResult

            return TripPlan(
                city="南京",
                start_date="2026-10-01",
                end_date="2026-10-01",
                days=[],
                overall_suggestions="按预约时间抵达景点。",
                quality=TripPlanQualityResult(
                    status="passed",
                    score=90,
                    publishable=True,
                    review_required=False,
                ),
            )

    def fail_delivery(*_args, **_kwargs):
        raise RuntimeError("simulated unexpected mail failure")

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    monkeypatch.setattr(
        "app.api.routes.trip.deliver_trip_plan_email",
        fail_delivery,
    )

    suffix = uuid.uuid4().hex[:10]
    email = f"mail_failure_{suffix}@example.com"
    user_id = ""

    try:
        with TestClient(app) as client:
            registered = client.post(
                "/api/auth/register",
                json={
                    "username": f"mail_failure_{suffix}",
                    "email": email,
                    "password": "Passw0rd123",
                },
            )
            assert registered.status_code == 201
            user_id = registered.json()["user"]["user_id"]

            response = client.post(
                "/api/trip/plan",
                json={
                    "city": "南京",
                    "start_date": "2026-10-01",
                    "end_date": "2026-10-01",
                    "travel_days": 1,
                    "travelers": 1,
                    "transportation": "公共交通",
                    "accommodation": "舒适型酒店",
                    "preferences": ["历史文化"],
                    "email_on_completion": True,
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["success"] is True
            assert payload["plan_no"]
            assert payload["email_delivery"] == {
                "requested": True,
                "sent": False,
                "dry_run": False,
                "blocked": False,
                "to": email,
                "message": "邮件服务暂时不可用，行程已正常保存。",
            }

            detail = client.get(f"/api/trip/history/{payload['plan_no']}")
            assert detail.status_code == 200
            assert detail.json()["data"]["city"] == "南京"
    finally:
        if user_id:
            _delete_user(user_id)


def test_agent_email_receives_authenticated_quota_context(monkeypatch) -> None:
    captured = {}

    def fake_send_email(to_email, subject, body, **kwargs):
        captured.update(
            {
                "to": to_email,
                "subject": subject,
                "body": body,
                **kwargs,
            }
        )
        return {
            "sent": True,
            "dry_run": False,
            "blocked": False,
            "message": "邮件发送成功。",
            "to": to_email,
        }

    monkeypatch.setattr(
        "app.agents.graph.travel_agent_graph.send_email",
        fake_send_email,
    )

    suffix = uuid.uuid4().hex[:10]
    email = f"agent_mail_{suffix}@example.com"
    user_id = ""
    try:
        with TestClient(app) as client:
            registered = client.post(
                "/api/auth/register",
                json={
                    "username": f"agent_mail_{suffix}",
                    "email": email,
                    "password": "Passw0rd123",
                },
            )
            assert registered.status_code == 201
            user_id = registered.json()["user"]["user_id"]

            response = client.post(
                "/api/agent/chat",
                json={
                    "message": "请把我的旅行画像报告发送到我的邮箱"
                },
            )
            assert response.status_code == 200
            assert response.json()["extra"]["email"]["sent"] is True
            assert captured["to"] == email
            assert captured["user_id"] == user_id
            assert captured["client_ip"] == "testclient"
    finally:
        if user_id:
            execute(
                "DELETE FROM audit_logs WHERE user_id = :user_id",
                {"user_id": user_id},
            )
            execute(
                "DELETE FROM query_logs WHERE user_id = :user_id",
                {"user_id": user_id},
            )
            _delete_user(user_id)


def test_real_email_quotas_are_atomic_for_user_and_ip(monkeypatch) -> None:
    class AcceptingSMTP:
        sent_messages = []

        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def login(self, _username, _password):
            return None

        def send_message(self, message):
            self.sent_messages.append(message)

    settings = get_settings()
    monkeypatch.setattr(settings, "email_quota_enabled", True)
    monkeypatch.setattr(
        settings,
        "auth_secret_key",
        "test-email-quota-secret-key-with-at-least-32-characters",
    )
    monkeypatch.setattr(settings, "email_user_daily_limit", 2)
    monkeypatch.setattr(settings, "email_ip_hourly_limit", 2)
    monkeypatch.setenv("SEND_REAL_EMAILS", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "unit-test-password")
    monkeypatch.setenv("SMTP_FROM", "sender@example.test")
    monkeypatch.setenv("SMTP_SSL", "true")
    monkeypatch.setattr(
        "app.tools.send_email_tool.smtplib.SMTP_SSL",
        AcceptingSMTP,
    )

    execute("DELETE FROM email_send_quotas")
    try:
        assert send_email(
            "one@example.test",
            "quota",
            "body",
            user_id="user-one",
            client_ip="203.0.113.10",
        )["sent"]
        assert send_email(
            "two@example.test",
            "quota",
            "body",
            user_id="user-one",
            client_ip="203.0.113.11",
        )["sent"]

        user_blocked = send_email(
            "three@example.test",
            "quota",
            "body",
            user_id="user-one",
            client_ip="203.0.113.12",
        )
        assert user_blocked["blocked"] is True
        assert user_blocked["quota_scope"] == "user"

        assert send_email(
            "four@example.test",
            "quota",
            "body",
            user_id="user-two",
            client_ip="203.0.113.10",
        )["sent"]

        ip_blocked = send_email(
            "five@example.test",
            "quota",
            "body",
            user_id="user-three",
            client_ip="203.0.113.10",
        )
        assert ip_blocked["blocked"] is True
        assert ip_blocked["quota_scope"] == "ip"

        assert send_email(
            "six@example.test",
            "quota",
            "body",
            user_id="user-three",
            client_ip="203.0.113.12",
        )["sent"]
        assert send_email(
            "seven@example.test",
            "quota",
            "body",
            user_id="user-three",
            client_ip="203.0.113.13",
        )["sent"]
        assert len(AcceptingSMTP.sent_messages) == 5
    finally:
        execute("DELETE FROM email_send_quotas")
