"""Progressive trip plan-jobs + SSE contract tests (no history/ETag/push)."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.auth import get_optional_current_user
from app.api.main import app
from app.models.schemas import (
    DayPlan,
    TripPlan,
    TripPlanQualityIssue,
    TripPlanQualityResult,
)
from app.services.auth_service import AuthenticatedUser
from app.services.trip_generation_errors import TripPlanQualityRejectedError
from app.services.trip_generation_job_service import TripGenerationJobService


def _auth_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="user-jobs",
        username="jobs",
        email="jobs@example.com",
        role="user",
    )


def _ok_plan() -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-02",
        overall_suggestions="正常",
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
        quality=TripPlanQualityResult(
            status="passed",
            score=90,
            publishable=True,
            review_required=False,
        ),
    )


def _payload() -> dict:
    return {
        "origin_city": "上海",
        "city": "杭州",
        "start_date": "2030-08-02",
        "end_date": "2030-08-02",
        "travel_days": 1,
        "travelers": 2,
        "transportation": "公共交通",
        "accommodation": "经济型酒店",
        "preferences": [],
        "free_text_input": "",
    }


@pytest.fixture
def job_client(monkeypatch):
    service = TripGenerationJobService(ttl_seconds=60, max_workers=2)
    save = MagicMock(return_value="P-JOB-1")
    email = MagicMock()
    notify = MagicMock()

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_generation_job_service",
        lambda: service,
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_travel_plan_data_service",
        lambda: SimpleNamespace(save_trip_plan=save),
    )
    monkeypatch.setattr("app.api.routes.trip.deliver_trip_plan_email", email)
    monkeypatch.setattr("app.api.routes.trip.notify_trip_plan_ready", notify)

    app.dependency_overrides[get_optional_current_user] = _auth_user
    try:
        with TestClient(app, base_url="https://testserver") as client:
            yield client, service, save, email, notify
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)
        service.shutdown()


def test_create_job_streams_progress_and_result(job_client, monkeypatch) -> None:
    client, service, save, email, notify = job_client
    plan = _ok_plan()

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            if progress_callback:
                progress_callback(
                    stage="ground",
                    progress=40,
                    message="正在核验景点信息",
                    detail="grounding",
                    meta={"ok": True},
                )
            return plan

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )

    created = client.post("/api/trip/plan-jobs", json=_payload())
    assert created.status_code == 200
    body = created.json()
    assert body["success"] is True
    assert len(body["job_id"]) == 32
    assert body["stream_url"] == f"/api/trip/plan-jobs/{body['job_id']}/events"
    assert "httponly" in created.headers["set-cookie"].lower()

    streamed = client.get(body["stream_url"])
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert "event: stage" in streamed.text
    assert "event: result" in streamed.text
    assert "正在核验景点信息" in streamed.text
    save.assert_called_once()
    # Email was not requested in the payload, but a saved plan must push.
    email.assert_not_called()
    notify.assert_called_once_with("user-jobs", "杭州", "P-JOB-1")


def test_needs_review_plan_streams_result_without_auto_save(
    job_client, monkeypatch
) -> None:
    """Mid-score non-blocking plans must reach the client without persistence."""
    client, _service, save, email, notify = job_client
    plan = _ok_plan()
    plan.quality = TripPlanQualityResult(
        status="warning",
        score=60,
        publishable=False,
        quality_status="needs_review",
    )

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            if progress_callback:
                progress_callback(stage="ground", progress=50, message="ground")
            return plan

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    created = client.post("/api/trip/plan-jobs", json=_payload())
    assert created.status_code == 200
    streamed = client.get(created.json()["stream_url"])
    assert "event: result" in streamed.text
    assert "event: error" not in streamed.text
    assert '"needs_review":true' in streamed.text.replace(" ", "")
    save.assert_not_called()
    email.assert_not_called()
    notify.assert_not_called()


def test_reviewable_warning_plan_streams_result_and_saves(
    job_client, monkeypatch
) -> None:
    """Reviewable warnings remain deliverable and may persist for logged-in users."""
    client, _service, save, email, notify = job_client
    plan = _ok_plan()
    plan.quality = TripPlanQualityResult(
        status="warning",
        score=88,
        publishable=True,
        review_required=True,
    )

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            if progress_callback:
                progress_callback(stage="ground", progress=50, message="ground")
            return plan

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    created = client.post("/api/trip/plan-jobs", json=_payload())
    assert created.status_code == 200
    streamed = client.get(created.json()["stream_url"])
    assert "event: result" in streamed.text
    assert "event: error" not in streamed.text
    assert "需要你确认" in streamed.text or "保存行程" in streamed.text
    save.assert_called_once()
    email.assert_not_called()
    notify.assert_not_called()


def test_quality_rejection_fails_job_without_save(job_client, monkeypatch) -> None:
    client, _service, save, email, notify = job_client
    quality = TripPlanQualityResult(
        status="failed",
        score=20,
        publishable=False,
        issues=[
            TripPlanQualityIssue(
                code="DAY_COUNT_MISMATCH",
                severity="error",
                message="生成计划天数与请求天数不一致",
                suggestion="请重新生成或确认出行天数",
            )
        ],
    )

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            raise TripPlanQualityRejectedError(quality=quality, plan=None)

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    created = client.post("/api/trip/plan-jobs", json=_payload())
    assert created.status_code == 200
    streamed = client.get(created.json()["stream_url"])
    assert "event: error" in streamed.text
    assert "event: result" not in streamed.text
    assert "DAY_COUNT_MISMATCH" in streamed.text
    assert "quality_rejected" in streamed.text
    save.assert_not_called()
    email.assert_not_called()
    notify.assert_not_called()


def test_unknown_error_is_safe(job_client, monkeypatch) -> None:
    client, *_ = job_client

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            raise RuntimeError("unexpected secret token path")

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    created = client.post("/api/trip/plan-jobs", json=_payload())
    streamed = client.get(created.json()["stream_url"])
    assert "event: error" in streamed.text
    assert "unexpected secret" not in streamed.text
    assert "generation_failed" in streamed.text


def test_cancel_prevents_late_result(job_client, monkeypatch) -> None:
    client, service, save, *_ = job_client
    started = threading.Event()
    release = threading.Event()

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            started.set()
            release.wait(timeout=2)
            if progress_callback:
                progress_callback.raise_if_cancelled()
            return _ok_plan()

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    created = client.post("/api/trip/plan-jobs", json=_payload())
    assert created.status_code == 200
    assert started.wait(timeout=1)
    job_id = created.json()["job_id"]
    cancelled = client.post(f"/api/trip/plan-jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    release.set()
    streamed = client.get(created.json()["stream_url"])
    assert "event: result" not in streamed.text
    assert "event: error" in streamed.text
    save.assert_not_called()
    job = service._jobs[job_id]
    with job.condition:
        job.condition.wait_for(lambda: job.worker_finished, timeout=2)
    assert job.status in {"cancelled", "failed"}


def test_save_failure_does_not_complete(job_client, monkeypatch) -> None:
    client, service, save, *_ = job_client
    save.side_effect = RuntimeError("db down")

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            return _ok_plan()

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    created = client.post("/api/trip/plan-jobs", json=_payload())
    streamed = client.get(created.json()["stream_url"])
    assert "event: result" not in streamed.text
    assert "event: error" in streamed.text
    job = service._jobs[created.json()["job_id"]]
    assert job.status == "failed"
