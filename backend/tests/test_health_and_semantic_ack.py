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


def _planner_stub() -> SimpleNamespace:
    """Faithful attribute surface of the real MultiAgentTripPlanner."""
    return SimpleNamespace(
        trip_graph=SimpleNamespace(graph_available=True),
        web_guide_agent=SimpleNamespace(
            status=lambda: {"provider": "zhipu", "llm_ready": True}
        ),
    )


def test_trip_health_reports_pipeline_with_current_agent_surface(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: _planner_stub(),
    )
    with TestClient(app) as client:
        response = client.get("/api/trip/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["service"] == "trip-planner"
    assert body["pipeline"]["graph_available"] is True
    assert body["pipeline"]["web_guide"]["provider"] == "zhipu"
    capacity = body["pipeline"]["generation_capacity"]
    assert set(capacity) == {"initial", "held", "available"}


def test_trip_health_failure_returns_503_without_internal_detail(
    monkeypatch,
) -> None:
    def broken_agent():
        raise RuntimeError("secret provider path C:/keys/llm.txt")

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        broken_agent,
    )
    with TestClient(app) as client:
        response = client.get("/api/trip/health")

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail == "服务不可用"
    assert "secret" not in detail


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
