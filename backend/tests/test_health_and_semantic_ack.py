"""Regression tests for /api/trip/health and semantic-ack unification (P0-5).

Before the fix:

- ``GET /api/trip/health`` read ``agent.attraction_agent`` /
  ``weather_agent`` / ``hotel_agent`` / ``planner_agent`` — attributes that
  no longer exist on ``MultiAgentTripPlanner`` — so the endpoint always
  raised AttributeError and returned 503 with the raw exception text.
- the quality service's semantic-contract check re-implemented the
  acknowledgment test as an inline magic-string match, ignoring the
  structured ``semantic_risks_acknowledged`` boolean the schema documents.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import get_settings
from app.services.request_rate_limit_service import (
    reset_request_rate_limit_service_for_tests,
)
from app.models.schemas import (
    DayPlan,
    FieldBinding,
    SemanticTripContract,
    TripPlan,
    TripRequest,
)
from app.services.semantic_contract_service import (
    USER_CONTRACT_ACK_MARKER,
    user_acknowledged_contract_risks,
)
from app.services.trip_plan_quality_service import TripPlanQualityService


def test_trip_health_returns_a_minimal_liveness_body() -> None:
    """Public health is liveness only — no provider, no live capacity."""
    reset_request_rate_limit_service_for_tests()
    with TestClient(app) as client:
        response = client.get("/api/trip/health")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "service", "version", "planner_initialized"}
    assert body["status"] == "healthy"
    assert body["service"] == "trip-planner"
    assert isinstance(body["planner_initialized"], bool)
    reset_request_rate_limit_service_for_tests()


def test_trip_health_discloses_no_internal_detail() -> None:
    reset_request_rate_limit_service_for_tests()
    with TestClient(app) as client:
        serialized = client.get("/api/trip/health").text

    # Search provider, pipeline shape and live slot counts must stay private.
    for secret in (
        "zhipu",
        "provider",
        "graph_available",
        "web_guide",
        "generation_capacity",
        "available",
        "held",
    ):
        assert secret not in serialized, f"health body leaked {secret!r}"
    reset_request_rate_limit_service_for_tests()


def test_trip_health_never_constructs_the_planner(monkeypatch) -> None:
    """A public probe must not trigger the expensive first-call init."""
    calls: list[str] = []

    def exploding_agent():
        calls.append("constructed")
        raise AssertionError("health must not construct the planner")

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent", exploding_agent
    )
    reset_request_rate_limit_service_for_tests()
    with TestClient(app) as client:
        response = client.get("/api/trip/health")

    assert response.status_code == 200
    assert calls == []
    reset_request_rate_limit_service_for_tests()


def test_trip_health_is_rate_limited() -> None:
    """Anonymous floods get 429 + Retry-After instead of unbounded work."""
    reset_request_rate_limit_service_for_tests()
    limit = max(1, int(getattr(get_settings(), "health_rate_limit", 30) or 30))
    with TestClient(app) as client:
        codes = [
            client.get("/api/trip/health").status_code for _ in range(limit)
        ]
        throttled = client.get("/api/trip/health")

    assert codes == [200] * limit
    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) >= 1
    reset_request_rate_limit_service_for_tests()


def _request_with_pending_origin(**overrides) -> TripRequest:
    contract = SemanticTripContract(
        origin_city=FieldBinding(
            value="上海",
            source="rule_inferred",
            confidence="low",
            pending_confirmation=True,
            evidence="从上海出发（推断）",
        )
    )
    contract.refresh_pending_fields()
    payload = {
        "city": "杭州",
        "origin_city": "上海",
        "start_date": "2030-08-02",
        "end_date": "2030-08-02",
        "travel_days": 1,
        "travelers": 1,
        "transportation": "公共交通",
        "accommodation": "经济型酒店",
        "preferences": [],
        "semantic_contract": contract,
    }
    payload.update(overrides)
    return TripRequest(**payload)


def _minimal_plan() -> TripPlan:
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
    )


def _collect_semantic_issues(request: TripRequest) -> list[dict]:
    issues: list[dict] = []

    def add(code, severity, path, message, suggestion="", **_kwargs):
        issues.append(
            {
                "code": code,
                "severity": severity,
                "path": path,
                "message": message,
                "suggestion": suggestion,
            }
        )

    TripPlanQualityService()._evaluate_semantic_contract(
        request, _minimal_plan(), add
    )
    return issues


def test_quality_gate_ack_prefers_structured_boolean_field() -> None:
    """semantic_risks_acknowledged=True must count as acknowledged even
    without the legacy free-text marker."""
    request = _request_with_pending_origin(
        semantic_risks_acknowledged=True,
        free_text_input="",
    )
    issues = _collect_semantic_issues(request)

    pending = next(
        i for i in issues if i["code"] == "SEMANTIC_PENDING_FIELDS"
    )
    assert "用户已在提交时确认" in pending["message"]
    assert "已记录用户确认" in pending["suggestion"]


def test_quality_gate_ack_keeps_legacy_marker_compat() -> None:
    """The old magic-string channel must keep working for older clients."""
    request = _request_with_pending_origin(
        semantic_risks_acknowledged=False,
        free_text_input=f"想去杭州 {USER_CONTRACT_ACK_MARKER}",
    )
    issues = _collect_semantic_issues(request)

    pending = next(
        i for i in issues if i["code"] == "SEMANTIC_PENDING_FIELDS"
    )
    assert "用户已在提交时确认" in pending["message"]


def test_user_acknowledged_contract_risks_channels() -> None:
    assert user_acknowledged_contract_risks(
        _request_with_pending_origin(semantic_risks_acknowledged=True)
    )
    assert user_acknowledged_contract_risks(
        _request_with_pending_origin(
            free_text_input=f"随便 {USER_CONTRACT_ACK_MARKER}"
        )
    )
    assert not user_acknowledged_contract_risks(_request_with_pending_origin())
