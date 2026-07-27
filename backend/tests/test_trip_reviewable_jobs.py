"""Async plan-jobs / SSE contract tests for reviewable vs blocked plans."""

from __future__ import annotations

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
        user_id="user-review-jobs",
        username="review_jobs",
        email="review_jobs@example.com",
        role="user",
    )


def _reviewable_plan() -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-02",
        overall_suggestions="建议游玩",
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
            status="warning",
            score=80,
            publishable=True,
            review_required=True,
            issues=[
                TripPlanQualityIssue(
                    code="DYNAMIC_DATA_UNVERIFIED",
                    severity="warning",
                    message="动态数据未实时联网复核",
                    suggestion="出发前再次确认",
                )
            ],
        ),
    )


def _blocked_plan() -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-02",
        overall_suggestions="不合格",
        days=[],
        quality=TripPlanQualityResult(
            status="failed",
            score=0,
            publishable=False,
            review_required=True,
            issues=[
                TripPlanQualityIssue(
                    code="DAY_COUNT_MISMATCH",
                    severity="error",
                    message="生成计划天数与请求天数不一致",
                    suggestion="重新生成",
                )
            ],
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
def review_job_client(monkeypatch):
    service = TripGenerationJobService(ttl_seconds=60, max_workers=2)
    save = MagicMock(return_value="P-JOB-REVIEW-1")
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


def test_reviewable_plan_job_completes_and_emits_result(review_job_client, monkeypatch):
    client, service, save, _email, _notify = review_job_client
    plan = _reviewable_plan()

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            return plan

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )

    created = client.post("/api/trip/plan-jobs", json=_payload())
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    streamed = client.get(f"/api/trip/plan-jobs/{job_id}/events")
    assert streamed.status_code == 200
    assert "event: result" in streamed.text
    assert "event: error" not in streamed.text
    assert "DYNAMIC_DATA_UNVERIFIED" in streamed.text
    save.assert_called_once()
    assert service._jobs[job_id].status == "completed"


def test_blocked_plan_job_fails_and_emits_error(review_job_client, monkeypatch):
    client, service, save, _email, _notify = review_job_client
    plan = _blocked_plan()

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            raise TripPlanQualityRejectedError(quality=plan.quality, plan=plan)

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )

    created = client.post("/api/trip/plan-jobs", json=_payload())
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    streamed = client.get(f"/api/trip/plan-jobs/{job_id}/events")
    assert streamed.status_code == 200
    assert "event: error" in streamed.text
    assert "event: result" not in streamed.text
    assert "DAY_COUNT_MISMATCH" in streamed.text
    save.assert_not_called()
    assert service._jobs[job_id].status == "failed"
