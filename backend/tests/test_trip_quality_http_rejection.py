"""HTTP mapping for TripPlanQualityRejectedError on POST /api/trip/plan."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.auth import get_optional_current_user
from app.models.schemas import (
    DayPlan,
    TripPlan,
    TripPlanQualityIssue,
    TripPlanQualityResult,
)
from app.services.auth_service import AuthenticatedUser
from app.services.trip_generation_errors import TripPlanQualityRejectedError


def _auth_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="user-quality-http",
        username="quality_http",
        email="quality@example.com",
        role="user",
    )


def _failed_quality(*, with_suggestion: bool = True, empty_issues: bool = False):
    if empty_issues:
        return TripPlanQualityResult(
            status="failed",
            score=0,
            publishable=False,
            issues=[],
        )
    issue = TripPlanQualityIssue(
        code="DAY_COUNT_MISMATCH",
        severity="error",
        path="days",
        message="生成计划天数与请求天数不一致",
        suggestion="请重新生成或确认出行天数" if with_suggestion else "",
    )
    return TripPlanQualityResult(
        status="failed",
        score=40,
        publishable=False,
        issues=[issue],
    )


def _ok_plan() -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        overall_suggestions="正常",
        days=[
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="d1",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            ),
            DayPlan(
                date="2030-08-03",
                day_index=1,
                description="d2",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            ),
        ],
        quality=TripPlanQualityResult(status="passed", score=90, publishable=True),
    )


def _payload() -> dict:
    return {
        "origin_city": "上海",
        "city": "杭州",
        "start_date": "2030-08-02",
        "end_date": "2030-08-03",
        "travel_days": 2,
        "travelers": 2,
        "transportation": "公共交通",
        "accommodation": "经济型酒店",
        "preferences": [],
        "free_text_input": "",
    }


@pytest.fixture
def client_and_mocks(monkeypatch):
    save = MagicMock(return_value="P-SAVE")
    email = MagicMock(
        return_value={
            "requested": False,
            "sent": False,
            "dry_run": True,
            "to": None,
            "message": "",
        }
    )
    notify = MagicMock()

    monkeypatch.setattr(
        "app.api.routes.trip.get_travel_plan_data_service",
        lambda: SimpleNamespace(save_trip_plan=save),
    )
    monkeypatch.setattr("app.api.routes.trip.deliver_trip_plan_email", email)
    monkeypatch.setattr("app.api.routes.trip.notify_trip_plan_ready", notify)

    app.dependency_overrides[get_optional_current_user] = _auth_user
    try:
        with TestClient(app) as client:
            yield client, save, email, notify
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)


def test_quality_rejection_returns_422_with_structured_issues(
    client_and_mocks, monkeypatch
) -> None:
    client, save, email, notify = client_and_mocks
    quality = _failed_quality(with_suggestion=True)
    plan = _ok_plan()
    plan.quality = quality

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            raise TripPlanQualityRejectedError(quality=quality, plan=plan)

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )

    response = client.post("/api/trip/plan", json=_payload())
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"] == "生成的行程未通过质量检查"
    assert isinstance(detail["issues"], list)
    assert detail["issues"][0]["code"] == "DAY_COUNT_MISMATCH"
    assert detail["issues"][0]["severity"] == "error"
    assert detail["issues"][0]["message"] == "生成计划天数与请求天数不一致"
    assert detail["issues"][0]["suggestion"] == "请重新生成或确认出行天数"
    # Frontend ApiIssue contract shape
    assert set(detail["issues"][0].keys()) <= {
        "code",
        "severity",
        "message",
        "suggestion",
        "path",
        "fields",
        "conflicts",
        "details",
        "auto_repaired",
    }
    save.assert_not_called()
    email.assert_not_called()
    notify.assert_not_called()


def test_quality_rejection_without_suggestion(client_and_mocks, monkeypatch) -> None:
    client, save, *_ = client_and_mocks
    quality = _failed_quality(with_suggestion=False)

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            raise TripPlanQualityRejectedError(quality=quality, plan=None)

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    response = client.post("/api/trip/plan", json=_payload())
    assert response.status_code == 422
    issue = response.json()["detail"]["issues"][0]
    assert issue["code"] == "DAY_COUNT_MISMATCH"
    assert "suggestion" not in issue or not issue.get("suggestion")
    save.assert_not_called()


def test_quality_rejection_empty_issues_fallback(client_and_mocks, monkeypatch) -> None:
    client, save, *_ = client_and_mocks
    quality = _failed_quality(empty_issues=True)

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            raise TripPlanQualityRejectedError(quality=quality, plan=None)

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    response = client.post("/api/trip/plan", json=_payload())
    assert response.status_code == 422
    issues = response.json()["detail"]["issues"]
    assert issues
    assert issues[0]["code"] == "TRIP_PLAN_QUALITY_REJECTED"
    assert issues[0]["severity"] == "error"
    save.assert_not_called()


def test_unknown_exception_still_500_without_quality_shape(
    client_and_mocks, monkeypatch
) -> None:
    client, save, *_ = client_and_mocks

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            raise RuntimeError("unexpected")

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    response = client.post("/api/trip/plan", json=_payload())
    assert response.status_code == 500
    body = response.json()
    detail = body.get("detail")
    # Existing HEAD behavior may include exception text; must not look like quality 422.
    if isinstance(detail, dict):
        assert "issues" not in detail
    save.assert_not_called()


def test_publishable_plan_still_saved(client_and_mocks, monkeypatch) -> None:
    client, save, email, notify = client_and_mocks
    plan = _ok_plan()

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            return plan

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    response = client.post("/api/trip/plan", json=_payload())
    assert response.status_code == 200
    assert response.json()["success"] is True
    save.assert_called_once()
