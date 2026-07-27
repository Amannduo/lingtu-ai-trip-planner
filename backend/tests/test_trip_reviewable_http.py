"""HTTP sync endpoint tests for reviewable vs blocked plans."""

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


def _auth_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="user-review-http",
        username="review_http",
        email="review_http@example.com",
        role="user",
    )


def _reviewable_plan() -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        overall_suggestions="建议游玩",
        days=[
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="第1天",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            ),
            DayPlan(
                date="2030-08-03",
                day_index=1,
                description="第2天",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            ),
        ],
        quality=TripPlanQualityResult(
            status="warning",
            score=80,
            publishable=True,
            review_required=True,
            issues=[
                TripPlanQualityIssue(
                    code="BUDGET_MAY_BE_INSUFFICIENT",
                    severity="warning",
                    message="预算预估偏低",
                    suggestion="建议预留备用资金",
                )
            ],
        ),
    )


def _blocked_plan() -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
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
                    message="生成天数与请求不一致",
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
        "end_date": "2030-08-03",
        "travel_days": 2,
        "travelers": 2,
        "transportation": "公共交通",
        "accommodation": "经济型酒店",
        "preferences": [],
        "free_text_input": "",
    }


@pytest.fixture
def http_client(monkeypatch):
    save = MagicMock(return_value="P-SAVE-REVIEW")
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


def test_reviewable_plan_returns_200_and_saves_to_db(http_client, monkeypatch):
    client, save, _email, _notify = http_client
    plan = _reviewable_plan()

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
    res_data = response.json()
    assert res_data["success"] is True
    assert res_data["plan_no"] == "P-SAVE-REVIEW"
    assert res_data["data"]["city"] == "杭州"
    assert res_data["data"]["quality"]["publishable"] is True
    assert res_data["data"]["quality"]["review_required"] is True
    assert len(res_data["data"]["quality"]["issues"]) == 1
    save.assert_called_once()


def test_blocked_plan_returns_422_and_does_not_save(http_client, monkeypatch):
    client, save, _email, _notify = http_client
    plan = _blocked_plan()

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            raise TripPlanQualityRejectedError(quality=plan.quality, plan=plan)

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )

    response = client.post("/api/trip/plan", json=_payload())
    assert response.status_code == 422
    save.assert_not_called()
