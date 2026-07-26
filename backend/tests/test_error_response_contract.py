"""S5 regression tests: compatible unified error response shape.

Phase-1 contract (additive, non-breaking):

- Request-validation errors (Pydantic) keep FastAPI's default
  ``{"detail": [{loc, msg, type}, ...]}`` payload byte-for-byte in spirit,
  and ADD top-level ``message`` + structured ``issues`` so every error
  class exposes the same ``{message, issues[]}`` vocabulary.
- The semantic hard-block and quality-rejection 422 shapes
  (``{"detail": {message, issues}}``) and the SSE error events
  (``{message, issues, error_type}``) are already structured and must not
  change in this phase.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _plan_payload(**overrides) -> dict:
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


def test_validation_error_keeps_default_detail_and_adds_issues(client) -> None:
    response = client.post(
        "/api/trip/plan", json=_plan_payload(travelers=0, city="")
    )
    assert response.status_code == 422
    body = response.json()

    # Legacy consumers: FastAPI default detail list is preserved.
    assert isinstance(body["detail"], list)
    first = body["detail"][0]
    assert "loc" in first and "msg" in first and "type" in first

    # New consumers: unified top-level vocabulary.
    assert isinstance(body["message"], str) and body["message"]
    issues = body["issues"]
    assert isinstance(issues, list) and issues
    for issue in issues:
        assert issue["code"] == "REQUEST_VALIDATION"
        assert issue["severity"] == "error"
        assert isinstance(issue["path"], str) and issue["path"]
        assert isinstance(issue["message"], str) and issue["message"]
    paths = {issue["path"] for issue in issues}
    assert any("travelers" in p for p in paths)
    assert any("city" in p for p in paths)


def test_validation_error_shape_is_global_across_routers(client) -> None:
    response = client.post("/api/recommend/chat", json={"messages": "oops"})
    assert response.status_code == 422
    body = response.json()
    assert isinstance(body["detail"], list)
    assert isinstance(body["message"], str) and body["message"]
    assert body["issues"] and body["issues"][0]["code"] == "REQUEST_VALIDATION"


def test_validation_issues_are_bounded(client) -> None:
    """A pathological body must not mirror unbounded content back."""
    response = client.post(
        "/api/trip/plan",
        json={"preferences": [{"bad": i} for i in range(50)]},
    )
    assert response.status_code == 422
    body = response.json()
    assert len(body["issues"]) <= 20


def test_semantic_hard_block_detail_shape_unchanged(client) -> None:
    """The pre-existing structured 422 (detail = {message, issues}) stays."""
    response = client.post(
        "/api/trip/plan",
        json=_plan_payload(
            origin_city="上海",
            free_text_input="从北京出发想去杭州",
        ),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert "message" in detail and "issues" in detail
    assert any(
        str(issue.get("code", "")).startswith("SEMANTIC_")
        for issue in detail["issues"]
    )
