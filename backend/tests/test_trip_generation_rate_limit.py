"""Process-local trip-generation rate limit isolation tests."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import get_optional_current_user
from app.api.main import app
from app.models.schemas import DayPlan, TripPlan, TripPlanQualityResult
from app.services.auth_service import AuthenticatedUser
from app.services.request_rate_limit_service import (
    RequestRateLimitService,
    create_request_rate_limit_service,
    get_request_rate_limit_service,
)
from app.services.trip_generation_job_service import (
    TripGenerationCapacityError,
    TripGenerationJobService,
)


def _user(user_id: str, username: str | None = None) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        username=username or user_id,
        email=f"{user_id}@example.com",
        role="user",
    )


def _payload(**overrides) -> dict:
    base = {
        "origin_city": "上海",
        "city": "杭州",
        "start_date": "2030-08-02",
        "end_date": "2030-08-02",
        "travel_days": 1,
        "travelers": 1,
        "transportation": "公共交通",
        "accommodation": "经济型酒店",
        "preferences": [],
        "free_text_input": "",
    }
    base.update(overrides)
    return base


def _ok_plan() -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-02",
        overall_suggestions="ok",
        days=[
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="d1",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            )
        ],
        quality=TripPlanQualityResult(status="passed", score=90, publishable=True),
    )


@pytest.fixture
def fast_planner(monkeypatch):
    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            if progress_callback:
                progress_callback(
                    stage="ground",
                    progress=10,
                    message="ok",
                    detail="",
                    meta={},
                )
            return _ok_plan()

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_travel_plan_data_service",
        lambda: SimpleNamespace(save_trip_plan=MagicMock(return_value="P-1")),
    )


@pytest.fixture
def rate_settings(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.trip.get_settings",
        lambda: SimpleNamespace(
            trip_generation_rate_limit=3,
            trip_generation_rate_window_seconds=60,
            auth_cookie_secure=False,
            trip_generation_max_runtime_seconds=600.0,
        ),
    )


def test_unit_sliding_window_and_cleanup_with_fake_clock() -> None:
    clock = {"t": 0.0}
    service = RequestRateLimitService(max_keys=2, clock=lambda: clock["t"])

    assert service.check("s", "user:a", limit=2, window_seconds=10, now=0) == 0
    assert service.check("s", "user:a", limit=2, window_seconds=10, now=1) == 0
    assert service.check("s", "user:a", limit=2, window_seconds=10, now=2) == 8

    # Window expiry restores quota without real sleep.
    clock["t"] = 20.0
    assert service.check("s", "user:a", limit=2, window_seconds=10, now=20) == 0

    # Bucket pruning under max_keys.
    service.check("s", "user:b", limit=2, window_seconds=10, now=21)
    service.check("s", "user:c", limit=2, window_seconds=10, now=22)
    assert service.key_count() <= 2


def test_unit_429_does_not_inflate_count() -> None:
    service = RequestRateLimitService(clock=lambda: 0.0)
    assert service.check("s", "ip:1", limit=1, window_seconds=60, now=0) == 0
    assert service.check("s", "ip:1", limit=1, window_seconds=60, now=1) == 59
    assert service.check("s", "ip:1", limit=1, window_seconds=60, now=2) == 58
    assert service.current_count("s", "ip:1", window_seconds=60, now=2) == 1


def test_unit_app_instances_are_isolated() -> None:
    a = create_request_rate_limit_service()
    b = create_request_rate_limit_service()
    assert a.check("trip-generation", "user:x", limit=1, window_seconds=60) == 0
    assert a.check("trip-generation", "user:x", limit=1, window_seconds=60) > 0
    # Independent instance still has full quota.
    assert b.check("trip-generation", "user:x", limit=1, window_seconds=60) == 0


def test_unit_concurrent_requests_respect_limit() -> None:
    service = RequestRateLimitService(clock=lambda: 100.0)
    allowed = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal allowed
        retry = service.check("s", "user:c", limit=5, window_seconds=60, now=100.0)
        if retry == 0:
            with lock:
                allowed += 1

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert allowed == 5


def test_same_user_hits_limit(fast_planner, rate_settings, monkeypatch) -> None:
    app.dependency_overrides[get_optional_current_user] = lambda: _user("u-limit")
    try:
        with TestClient(app) as client:
            for _ in range(3):
                assert client.post("/api/trip/plan", json=_payload()).status_code == 200
            blocked = client.post("/api/trip/plan", json=_payload())
            assert blocked.status_code == 429
            assert "Retry-After" in blocked.headers
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)


def test_different_users_are_isolated(fast_planner, rate_settings) -> None:
    with TestClient(app) as client:
        app.dependency_overrides[get_optional_current_user] = lambda: _user("user-a")
        for _ in range(3):
            assert client.post("/api/trip/plan", json=_payload()).status_code == 200
        assert client.post("/api/trip/plan", json=_payload()).status_code == 429

        app.dependency_overrides[get_optional_current_user] = lambda: _user("user-b")
        assert client.post("/api/trip/plan", json=_payload()).status_code == 200
    app.dependency_overrides.pop(get_optional_current_user, None)


def test_same_user_different_client_hosts_share_bucket(
    fast_planner, rate_settings, monkeypatch
) -> None:
    """Auth user identity wins over peer IP."""
    from starlette.requests import Request

    calls: list[str] = []

    original = __import__(
        "app.api.routes.trip", fromlist=["_trip_generation_rate_identity"]
    )._trip_generation_rate_identity

    def tracking_identity(user, request):
        key = original(user, request)
        calls.append(key)
        return key

    monkeypatch.setattr(
        "app.api.routes.trip._trip_generation_rate_identity",
        tracking_identity,
    )
    app.dependency_overrides[get_optional_current_user] = lambda: _user("user-shared")
    try:
        with TestClient(app) as client:
            for _ in range(3):
                assert client.post("/api/trip/plan", json=_payload()).status_code == 200
            assert client.post("/api/trip/plan", json=_payload()).status_code == 429
        assert calls
        assert all(item == "user:user-shared" for item in calls)
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)


def test_different_users_same_ip_do_not_share(fast_planner, rate_settings) -> None:
    with TestClient(app) as client:
        app.dependency_overrides[get_optional_current_user] = lambda: _user("nat-a")
        for _ in range(3):
            assert client.post("/api/trip/plan", json=_payload()).status_code == 200
        app.dependency_overrides[get_optional_current_user] = lambda: _user("nat-b")
        # Same TestClient peer IP, different user → still allowed.
        assert client.post("/api/trip/plan", json=_payload()).status_code == 200
    app.dependency_overrides.pop(get_optional_current_user, None)


def test_anonymous_ip_fallback_shares_bucket(fast_planner, rate_settings) -> None:
    app.dependency_overrides[get_optional_current_user] = lambda: None
    try:
        with TestClient(app) as client:
            for _ in range(3):
                assert client.post("/api/trip/plan", json=_payload()).status_code == 200
            assert client.post("/api/trip/plan", json=_payload()).status_code == 429
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)


def test_plan_and_plan_jobs_share_quota(fast_planner, rate_settings, monkeypatch) -> None:
    job_service = TripGenerationJobService(ttl_seconds=30, max_workers=2)
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_generation_job_service",
        lambda: job_service,
    )
    app.dependency_overrides[get_optional_current_user] = lambda: _user("mix-user")
    try:
        with TestClient(app, base_url="https://testserver") as client:
            assert client.post("/api/trip/plan", json=_payload()).status_code == 200
            assert client.post("/api/trip/plan-jobs", json=_payload()).status_code == 200
            assert client.post("/api/trip/plan", json=_payload()).status_code == 200
            blocked = client.post("/api/trip/plan-jobs", json=_payload())
            assert blocked.status_code == 429
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)
        job_service.shutdown()


def test_sse_and_cancel_do_not_consume_quota(
    fast_planner, rate_settings, monkeypatch
) -> None:
    job_service = TripGenerationJobService(ttl_seconds=30, max_workers=2)
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_generation_job_service",
        lambda: job_service,
    )
    app.dependency_overrides[get_optional_current_user] = lambda: _user("sse-user")
    try:
        with TestClient(app, base_url="https://testserver") as client:
            created = client.post("/api/trip/plan-jobs", json=_payload())
            assert created.status_code == 200
            stream_url = created.json()["stream_url"]
            job_id = created.json()["job_id"]
            for _ in range(5):
                assert client.get(stream_url).status_code == 200
                assert client.post(f"/api/trip/plan-jobs/{job_id}/cancel").status_code == 200
            # Only one create consumed; two more creates still allowed (limit=3).
            assert client.post("/api/trip/plan", json=_payload()).status_code == 200
            assert client.post("/api/trip/plan", json=_payload()).status_code == 200
            assert client.post("/api/trip/plan", json=_payload()).status_code == 429
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)
        job_service.shutdown()


def test_schema_422_does_not_consume_quota(fast_planner, rate_settings) -> None:
    app.dependency_overrides[get_optional_current_user] = lambda: _user("schema-user")
    try:
        with TestClient(app) as client:
            for _ in range(5):
                bad = client.post(
                    "/api/trip/plan",
                    json={"city": "杭州"},  # missing required fields
                )
                assert bad.status_code == 422
            # Full quota still available.
            for _ in range(3):
                assert client.post("/api/trip/plan", json=_payload()).status_code == 200
            assert client.post("/api/trip/plan", json=_payload()).status_code == 429
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)


def test_testclient_lifespan_isolates_limiter_between_contexts(
    fast_planner, rate_settings
) -> None:
    app.dependency_overrides[get_optional_current_user] = lambda: _user("iso-user")
    try:
        with TestClient(app) as client:
            for _ in range(3):
                assert client.post("/api/trip/plan", json=_payload()).status_code == 200
            assert client.post("/api/trip/plan", json=_payload()).status_code == 429

        # New lifespan → new app.state limiter → fresh quota.
        with TestClient(app) as client:
            assert client.post("/api/trip/plan", json=_payload()).status_code == 200
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)


def test_rate_limit_independent_of_generation_capacity(
    fast_planner, rate_settings, monkeypatch
) -> None:
    """Queue capacity 429 is separate from frequency quota counters."""
    job_service = TripGenerationJobService(ttl_seconds=30, max_workers=2)

    def start_busy(*_args, **_kwargs):
        raise TripGenerationCapacityError("generation queue is full")

    monkeypatch.setattr(job_service, "start", start_busy)
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_generation_job_service",
        lambda: job_service,
    )
    app.dependency_overrides[get_optional_current_user] = lambda: _user("cap-user")
    try:
        with TestClient(app, base_url="https://testserver") as client:
            first = client.post("/api/trip/plan-jobs", json=_payload())
            assert first.status_code == 429
            assert "规划任务较多" in first.json()["detail"]
            # Frequency token was already consumed before capacity check.
            assert client.post("/api/trip/plan", json=_payload()).status_code == 200
            assert client.post("/api/trip/plan", json=_payload()).status_code == 200
            rate_blocked = client.post("/api/trip/plan", json=_payload())
            assert rate_blocked.status_code == 429
            assert "过于频繁" in rate_blocked.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)
        job_service.shutdown()


def test_quality_http_suite_does_not_pollute_across_users(
    fast_planner, rate_settings
) -> None:
    """Regression: many plan POSTs from different fixtures must not all 429."""
    with TestClient(app) as client:
        for idx in range(8):
            app.dependency_overrides[get_optional_current_user] = lambda i=idx: _user(
                f"pollute-{i}"
            )
            response = client.post("/api/trip/plan", json=_payload())
            assert response.status_code == 200, response.text
    app.dependency_overrides.pop(get_optional_current_user, None)


def test_get_request_rate_limit_service_prefers_app_state() -> None:
    custom = create_request_rate_limit_service()
    mini = FastAPI()
    mini.state.request_rate_limiter = custom

    class _Req:
        app = mini

    resolved = get_request_rate_limit_service(_Req())
    assert resolved is custom
