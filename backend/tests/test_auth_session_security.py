"""Auth cookie, token_version revocation, and legacy plan claim boundaries."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from app.api.main import app
from app.api.routes.auth import _set_auth_cookie
from app.config import get_settings
from app.services.auth_service import (
    InvalidTokenError,
    _can_claim_legacy_plans,
    authenticate_user,
    create_access_token,
    get_user_by_id,
    register_user,
    revoke_user_tokens,
    user_from_access_token,
)
from app.services.database_service import execute, fetch_one


def _delete_user(user_id: str) -> None:
    execute("DELETE FROM user_profiles WHERE user_id = :user_id", {"user_id": user_id})
    execute("DELETE FROM travel_plans WHERE user_id = :user_id", {"user_id": user_id})
    execute("DELETE FROM users WHERE user_id = :user_id", {"user_id": user_id})


def _unique_user() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:10]
    return f"sess_{suffix}", f"{suffix}@example.com"


def _http_request(scheme: str = "http") -> Request:
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


def test_login_cookie_is_httponly_samesite_lax_and_path_root() -> None:
    username, email = _unique_user()
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
            user_id = registered.json()["user"]["user_id"]
            set_cookie = registered.headers.get("set-cookie", "").lower()
            assert "httponly" in set_cookie
            assert "samesite=lax" in set_cookie
            assert "path=/" in set_cookie
            # Local TestClient is HTTP; Secure must follow AUTH_COOKIE_SECURE only.
            if get_settings().auth_cookie_secure:
                assert "secure" in set_cookie
            else:
                assert "secure" not in set_cookie
            body = registered.json()
            assert "access_token" not in body
            assert "token_version" not in body.get("user", {})
    finally:
        if user_id:
            _delete_user(user_id)


def test_auth_cookie_secure_when_config_true(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "auth_cookie_secure", True)
    response = Response()
    _set_auth_cookie(response, "signed-test-token", _http_request("http"))
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_auth_cookie_secure_for_https_even_when_config_false(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "auth_cookie_secure", False)
    response = Response()
    _set_auth_cookie(response, "signed-test-token", _http_request("https"))
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_logout_clears_cookie_and_revokes_all_sessions_for_user() -> None:
    username, email = _unique_user()
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
            user_id = registered.json()["user"]["user_id"]
            cookie_name = get_settings().auth_cookie_name
            old_token = client.cookies.get(cookie_name)
            assert old_token

            # Second login simulates another device / session.
            second = client.post(
                "/api/auth/login",
                json={"username": username, "password": "Passw0rd123"},
            )
            assert second.status_code == 200
            second_token = client.cookies.get(cookie_name)
            assert second_token

            logout = client.post("/api/auth/logout")
            assert logout.status_code == 200
            assert "success" in logout.json()
            # Cookie should be cleared for the TestClient jar.
            assert client.cookies.get(cookie_name) in (None, "")

            # Prior cookies must no longer authenticate (user-level revoke).
            client.cookies.set(cookie_name, old_token)
            assert client.get("/api/auth/me").status_code == 401
            client.cookies.set(cookie_name, second_token)
            assert client.get("/api/auth/me").status_code == 401
    finally:
        if user_id:
            _delete_user(user_id)


def test_login_and_logout_cookie_attributes_match(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "auth_cookie_secure", True)
    username, email = _unique_user()
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
            user_id = registered.json()["user"]["user_id"]
            login_cookie = registered.headers.get("set-cookie", "").lower()
            assert "httponly" in login_cookie
            assert "samesite=lax" in login_cookie
            assert "secure" in login_cookie
            assert "path=/" in login_cookie

            logout = client.post("/api/auth/logout")
            assert logout.status_code == 200
            logout_cookie = logout.headers.get("set-cookie", "").lower()
            assert "httponly" in logout_cookie
            assert "samesite=lax" in logout_cookie
            assert "secure" in logout_cookie
            assert "path=/" in logout_cookie
    finally:
        if user_id:
            _delete_user(user_id)


def test_valid_token_authenticates_and_bearer_header_works() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        token = create_access_token(user)
        resolved = user_from_access_token(token)
        assert resolved.user_id == user.user_id
        with TestClient(app) as client:
            response = client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            assert response.json()["user"]["user_id"] == user.user_id
    finally:
        _delete_user(user.user_id)


def test_expired_token_is_rejected() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        settings = get_settings()
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user.user_id,
            "type": "access",
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
            "jti": uuid.uuid4().hex,
            "ver": user.token_version,
        }
        token = jwt.encode(payload, settings.auth_secret_key, algorithm="HS256")
        with pytest.raises(InvalidTokenError):
            user_from_access_token(token)
    finally:
        _delete_user(user.user_id)


def test_invalid_signature_is_rejected() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user.user_id,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "jti": uuid.uuid4().hex,
            "ver": user.token_version,
        }
        token = jwt.encode(payload, "wrong-secret-key-not-the-real-one!!", algorithm="HS256")
        with pytest.raises(InvalidTokenError):
            user_from_access_token(token)
    finally:
        _delete_user(user.user_id)


def test_wrong_algorithm_and_missing_claims_are_rejected() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        settings = get_settings()
        now = datetime.now(timezone.utc)
        # alg=none style forged token is rejected by decode with fixed algorithms.
        none_token = (
            "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0."
            + jwt.utils.base64url_encode(
                (
                    '{"sub":"%s","type":"access","iat":1,"exp":9999999999,'
                    '"jti":"x","ver":0}' % user.user_id
                ).encode()
            ).decode()
            + "."
        )
        with pytest.raises(InvalidTokenError):
            user_from_access_token(none_token)

        missing_ver = {
            "sub": user.user_id,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "jti": uuid.uuid4().hex,
        }
        token = jwt.encode(missing_ver, settings.auth_secret_key, algorithm="HS256")
        with pytest.raises(InvalidTokenError):
            user_from_access_token(token)
    finally:
        _delete_user(user.user_id)


def test_token_version_match_and_mismatch() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        token = create_access_token(user)
        assert user_from_access_token(token).user_id == user.user_id

        revoke_user_tokens(user.user_id)
        with pytest.raises(InvalidTokenError):
            user_from_access_token(token)

        refreshed = get_user_by_id(user.user_id)
        assert refreshed is not None
        assert refreshed.token_version == user.token_version + 1
        new_token = create_access_token(refreshed)
        assert user_from_access_token(new_token).user_id == user.user_id
    finally:
        _delete_user(user.user_id)


def test_client_cannot_forge_token_version_claim() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        settings = get_settings()
        now = datetime.now(timezone.utc)
        forged = {
            "sub": user.user_id,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "jti": uuid.uuid4().hex,
            "ver": user.token_version + 99,
        }
        token = jwt.encode(forged, settings.auth_secret_key, algorithm="HS256")
        with pytest.raises(InvalidTokenError):
            user_from_access_token(token)
    finally:
        _delete_user(user.user_id)


def test_security_event_revoke_invalidates_like_password_change() -> None:
    """Password change API is not exposed; revoke_user_tokens is the shared path."""
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        token = create_access_token(user)
        revoke_user_tokens(user.user_id)
        with pytest.raises(InvalidTokenError):
            user_from_access_token(token)
    finally:
        _delete_user(user.user_id)


def test_disabled_user_token_is_rejected() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        token = create_access_token(user)
        execute(
            "UPDATE users SET is_active = 0 WHERE user_id = :user_id",
            {"user_id": user.user_id},
        )
        with pytest.raises(InvalidTokenError):
            user_from_access_token(token)
        assert get_user_by_id(user.user_id) is None
    finally:
        _delete_user(user.user_id)


def test_deleted_user_token_is_rejected() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    token = create_access_token(user)
    _delete_user(user.user_id)
    with pytest.raises(InvalidTokenError):
        user_from_access_token(token)


def test_concurrent_token_version_updates_are_monotonic() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                revoke_user_tokens(user.user_id)
            except BaseException as exc:  # pragma: no cover - collect for assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        row = fetch_one(
            "SELECT token_version FROM users WHERE user_id = :user_id",
            {"user_id": user.user_id},
        )
        assert row is not None
        assert int(row["token_version"]) == user.token_version + 8
    finally:
        _delete_user(user.user_id)


def test_legacy_token_without_ver_claim_is_rejected() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        settings = get_settings()
        now = datetime.now(timezone.utc)
        legacy = {
            "sub": user.user_id,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "jti": uuid.uuid4().hex,
        }
        token = jwt.encode(legacy, settings.auth_secret_key, algorithm="HS256")
        with pytest.raises(InvalidTokenError):
            user_from_access_token(token)
    finally:
        _delete_user(user.user_id)


def test_login_error_does_not_enumerate_accounts() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        with TestClient(app) as client:
            missing = client.post(
                "/api/auth/login",
                json={"username": "no_such_user_zzz", "password": "Passw0rd123"},
            )
            wrong = client.post(
                "/api/auth/login",
                json={"username": username, "password": "WrongPass999"},
            )
            assert missing.status_code == 401
            assert wrong.status_code == 401
            assert missing.json()["detail"] == wrong.json()["detail"]
            assert "不存在" not in missing.json()["detail"]
            assert "停用" not in wrong.json()["detail"]
    finally:
        _delete_user(user.user_id)


def test_only_bootstrap_admin_can_claim_unowned_legacy_plans() -> None:
    assert _can_claim_legacy_plans(0, "admin") is True
    assert _can_claim_legacy_plans(0, "user") is False
    assert _can_claim_legacy_plans(0, "manager") is False
    assert _can_claim_legacy_plans(1, "admin") is False
    assert _can_claim_legacy_plans(0, "Admin") is True


def test_ordinary_first_user_does_not_claim_u_current_plans() -> None:
    """plan_no alone never transfers ownership; only bootstrap admin may claim."""
    plan_no = f"P-LEGACY-{uuid.uuid4().hex[:8]}"
    execute(
        """INSERT INTO travel_plans
           (plan_no, user_id, user_role, destination, start_date, end_date,
            travel_days, plan_json)
           VALUES (:plan_no, 'u_current', 'user', '杭州', '2026-08-01', '2026-08-01',
                   1, '{}')""",
        {"plan_no": plan_no},
    )
    username, email = _unique_user()
    user_id = ""
    try:
        user = register_user(username, "Passw0rd123", role="user", email=email)
        user_id = user.user_id
        row = fetch_one(
            "SELECT user_id FROM travel_plans WHERE plan_no = :plan_no",
            {"plan_no": plan_no},
        )
        assert row is not None
        assert row["user_id"] == "u_current"
        assert row["user_id"] != user_id
    finally:
        if user_id:
            _delete_user(user_id)
        execute("DELETE FROM travel_plans WHERE plan_no = :plan_no", {"plan_no": plan_no})


def test_bootstrap_admin_claims_u_current_once(monkeypatch) -> None:
    # Isolate against whatever users already exist: exercise claim path via
    # direct service helper by temporarily forcing user_count gate through
    # monkeypatch of the private helper used by register_user.
    plan_no = f"P-ADMIN-{uuid.uuid4().hex[:8]}"
    execute(
        """INSERT INTO travel_plans
           (plan_no, user_id, user_role, destination, start_date, end_date,
            travel_days, plan_json)
           VALUES (:plan_no, 'u_current', 'user', '杭州', '2026-08-01', '2026-08-01',
                   1, '{"city":"杭州","days":[]}')""",
        {"plan_no": plan_no},
    )
    username, email = _unique_user()
    user_id = ""
    try:
        monkeypatch.setattr(
            "app.services.auth_service._can_claim_legacy_plans",
            lambda user_count, role: str(role).lower() == "admin",
        )
        monkeypatch.setattr(get_settings(), "auth_admin_invite_code", "unit-admin-invite")
        user = register_user(
            username,
            "Passw0rd123",
            role="admin",
            invite_code="unit-admin-invite",
            email=email,
        )
        user_id = user.user_id
        row = fetch_one(
            "SELECT user_id, user_role, plan_json FROM travel_plans WHERE plan_no = :plan_no",
            {"plan_no": plan_no},
        )
        assert row is not None
        assert row["user_id"] == user_id
        assert row["user_role"] == "admin"
        # Claim must not rewrite plan content.
        assert "杭州" in str(row["plan_json"])

        # Second admin must not re-claim already owned plans.
        username2, email2 = _unique_user()
        other = register_user(
            username2,
            "Passw0rd123",
            role="admin",
            invite_code="unit-admin-invite",
            email=email2,
        )
        try:
            row2 = fetch_one(
                "SELECT user_id FROM travel_plans WHERE plan_no = :plan_no",
                {"plan_no": plan_no},
            )
            assert row2 is not None
            assert row2["user_id"] == user_id
            assert row2["user_id"] != other.user_id
        finally:
            _delete_user(other.user_id)
    finally:
        if user_id:
            _delete_user(user_id)
        execute("DELETE FROM travel_plans WHERE plan_no = :plan_no", {"plan_no": plan_no})


def test_public_user_payload_omits_token_version() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        payload = user.as_dict()
        assert "token_version" not in payload
        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login",
                json={"username": username, "password": "Passw0rd123"},
            )
            assert login.status_code == 200
            assert "token_version" not in login.json()["user"]
            assert "access_token" not in login.json()
    finally:
        _delete_user(user.user_id)


def test_cors_credentials_do_not_use_wildcard_origin() -> None:
    settings = get_settings()
    origins = settings.get_cors_origins_list()
    assert "*" not in origins
    # Middleware is configured with allow_credentials=True and explicit origins.
    assert origins


def test_authenticate_inactive_user_uses_uniform_message() -> None:
    username, email = _unique_user()
    user = register_user(username, "Passw0rd123", email=email)
    try:
        execute(
            "UPDATE users SET is_active = 0 WHERE user_id = :user_id",
            {"user_id": user.user_id},
        )
        with pytest.raises(Exception) as exc_info:
            authenticate_user(username, "Passw0rd123")
        assert "用户名或密码不正确" in str(exc_info.value)
    finally:
        _delete_user(user.user_id)
