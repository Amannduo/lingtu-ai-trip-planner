"""Regression tests for job-path notifications and SSE error structure (P0-3).

Before the fix the job worker hardcoded ``email_delivery=None`` and never
called ``notify_trip_plan_ready`` — the "email me when done" and desktop
push toggles were dead on the primary (SSE) generation path.  These tests
pin:

1. the job worker delivers email when requested and pushes after save;
2. notification failures never fail the job or lose the plan result;
3. needs_review plans skip email/push exactly like the sync path;
4. SSE error events carry structured issues (message + suggestion) so the
   frontend can render actionable feedback.
"""

from __future__ import annotations

import json
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
        user_id="user-notify",
        username="notify",
        email="notify@example.com",
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
            quality_status="publishable",
        ),
    )


def _payload(**overrides) -> dict:
    payload = {
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
    payload.update(overrides)
    return payload


def _sse_events(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.split("\n\n"):
        data_lines = [
            line[len("data: "):]
            for line in block.splitlines()
            if line.startswith("data: ")
        ]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


@pytest.fixture
def job_client(monkeypatch):
    service = TripGenerationJobService(ttl_seconds=60, max_workers=2)
    save = MagicMock(return_value="P-NOTIFY-1")
    email = MagicMock(
        return_value={
            "requested": True,
            "sent": True,
            "dry_run": False,
            "blocked": False,
            "to": "notify@example.com",
            "message": "邮件已发送",
        }
    )
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


def _use_planner(monkeypatch, planner) -> None:
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: planner,
    )


def test_job_worker_delivers_email_and_push_when_requested(
    job_client, monkeypatch
) -> None:
    client, _service, save, email, notify = job_client
    plan = _ok_plan()
    _use_planner(
        monkeypatch,
        SimpleNamespace(plan_trip=lambda _r, progress_callback=None, **_k: plan),
    )

    created = client.post(
        "/api/trip/plan-jobs",
        json=_payload(
            email_on_completion=True,
            delivery_email="notify@example.com",
        ),
    )
    assert created.status_code == 200
    streamed = client.get(created.json()["stream_url"])
    assert "event: result" in streamed.text

    result = next(
        e for e in _sse_events(streamed.text) if e.get("type") == "result"
    )
    delivery = result["data"]["email_delivery"]
    assert delivery is not None
    assert delivery["sent"] is True
    assert delivery["to"] == "notify@example.com"

    save.assert_called_once()
    email.assert_called_once()
    assert email.call_args.kwargs.get("user_id") == "user-notify"
    notify.assert_called_once_with("user-notify", "杭州", "P-NOTIFY-1")


def test_notification_failures_never_fail_the_job(job_client, monkeypatch) -> None:
    client, _service, save, email, notify = job_client
    email.side_effect = RuntimeError("smtp exploded")
    notify.side_effect = RuntimeError("push exploded")
    plan = _ok_plan()
    _use_planner(
        monkeypatch,
        SimpleNamespace(plan_trip=lambda _r, progress_callback=None, **_k: plan),
    )

    created = client.post(
        "/api/trip/plan-jobs",
        json=_payload(
            email_on_completion=True,
            delivery_email="notify@example.com",
        ),
    )
    streamed = client.get(created.json()["stream_url"])

    assert "event: result" in streamed.text
    assert "event: error" not in streamed.text
    result = next(
        e for e in _sse_events(streamed.text) if e.get("type") == "result"
    )
    assert result["data"]["plan_no"] == "P-NOTIFY-1"
    delivery = result["data"]["email_delivery"]
    assert delivery["sent"] is False
    assert "邮件服务暂时不可用" in delivery["message"]
    save.assert_called_once()


def test_needs_review_plan_skips_email_and_push(job_client, monkeypatch) -> None:
    client, _service, save, email, notify = job_client
    plan = _ok_plan()
    plan.quality = TripPlanQualityResult(
        status="warning",
        score=60,
        publishable=False,
        quality_status="needs_review",
    )
    _use_planner(
        monkeypatch,
        SimpleNamespace(plan_trip=lambda _r, progress_callback=None, **_k: plan),
    )

    created = client.post(
        "/api/trip/plan-jobs",
        json=_payload(
            email_on_completion=True,
            delivery_email="notify@example.com",
        ),
    )
    streamed = client.get(created.json()["stream_url"])

    assert "event: result" in streamed.text
    result = next(
        e for e in _sse_events(streamed.text) if e.get("type") == "result"
    )
    assert result["data"]["needs_review"] is True
    assert result["data"]["plan_no"] is None
    save.assert_not_called()
    email.assert_not_called()
    notify.assert_not_called()


def test_sse_quality_error_event_carries_structured_issues(
    job_client, monkeypatch
) -> None:
    client, _service, _save, _email, _notify = job_client
    quality = TripPlanQualityResult(
        status="failed",
        score=40,
        publishable=False,
        quality_status="blocked",
        issues=[
            TripPlanQualityIssue(
                code="EMPTY_DAY",
                severity="error",
                path="days[0]",
                message="第1天没有任何可执行安排。",
                suggestion="减少行程天数或更换目的地后重新生成。",
            )
        ],
    )

    def rejecting_plan_trip(_request, progress_callback=None, **_kwargs):
        raise TripPlanQualityRejectedError(quality=quality, plan=None)

    _use_planner(monkeypatch, SimpleNamespace(plan_trip=rejecting_plan_trip))

    created = client.post("/api/trip/plan-jobs", json=_payload())
    streamed = client.get(created.json()["stream_url"])

    assert "event: error" in streamed.text
    error = next(
        e for e in _sse_events(streamed.text) if e.get("type") == "error"
    )
    assert error["error_type"] == "quality_rejected"
    issues = error["issues"]
    assert issues and issues[0]["code"] == "EMPTY_DAY"
    assert issues[0]["severity"] == "error"
    assert issues[0]["suggestion"].startswith("减少行程天数")
