"""S1 regression tests: the semantic contract is built once per request.

Invariants pinned here (instrumented spy is the primary check):

1. A generation request carrying free text runs the natural-language
   extraction **exactly once** across entry gate, planner and quality gate.
2. A pure-form request (no user-authored free text) runs **zero**
   extractions.
3. The recommender chat runs at most one extraction per conversation turn.
4. ``plan_trip`` never re-extracts when the entry already attached a
   contract; it attaches exactly once as a fallback for direct callers.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents.destination_recommender_agent import DestinationRecommenderAgent
from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.api.auth import get_optional_current_user
from app.api.main import app
from app.models.schemas import (
    ChatMessage,
    DayPlan,
    DestinationChatRequest,
    RecommendationContext,
    TripPlan,
    TripPlanQualityResult,
    TripRequest,
)
from app.services.semantic_contract_service import (
    SemanticContractService,
    attach_contract_to_trip_request,
)


@pytest.fixture
def extraction_calls(monkeypatch):
    """Spy on SemanticContractService.extract_from_text without altering it."""
    calls = {"count": 0, "texts": []}
    original = SemanticContractService.extract_from_text

    def counting(self, text, **kwargs):
        calls["count"] += 1
        calls["texts"].append(text)
        return original(self, text, **kwargs)

    monkeypatch.setattr(SemanticContractService, "extract_from_text", counting)
    return calls


def _publishable_plan() -> TripPlan:
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


def _payload(free_text: str) -> dict:
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
        "free_text_input": free_text,
    }


@pytest.fixture
def plan_client(monkeypatch):
    plan = _publishable_plan()
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: SimpleNamespace(
            plan_trip=lambda _r, progress_callback=None, **_k: plan
        ),
    )
    app.dependency_overrides[get_optional_current_user] = lambda: None
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)


def test_plan_request_with_free_text_extracts_exactly_once(
    plan_client, extraction_calls
) -> None:
    response = plan_client.post(
        "/api/trip/plan", json=_payload("两个人想去杭州走走，节奏轻松一点")
    )
    assert response.status_code == 200
    assert extraction_calls["count"] == 1


def test_plan_request_without_free_text_extracts_zero_times(
    plan_client, extraction_calls
) -> None:
    response = plan_client.post("/api/trip/plan", json=_payload(""))
    assert response.status_code == 200
    assert extraction_calls["count"] == 0


def test_plan_jobs_request_extracts_exactly_once(
    plan_client, extraction_calls
) -> None:
    created = plan_client.post(
        "/api/trip/plan-jobs", json=_payload("两个人想去杭州走走")
    )
    assert created.status_code == 200
    streamed = plan_client.get(created.json()["stream_url"])
    assert "event: result" in streamed.text
    assert extraction_calls["count"] == 1


def test_recommend_chat_extracts_at_most_once_per_turn(
    extraction_calls,
) -> None:
    agent = DestinationRecommenderAgent.__new__(DestinationRecommenderAgent)
    agent.agent = None
    agent._search_city_highlights = lambda city, preferences: []
    agent._weather_summary = lambda city: None

    request = DestinationChatRequest(
        messages=[ChatMessage(role="user", content="太原出发周末想找个近的地方玩")],
        context=RecommendationContext(),
    )
    agent.chat(request)

    assert extraction_calls["count"] <= 1


def _direct_request(free_text: str) -> TripRequest:
    return TripRequest(
        city="杭州",
        origin_city="上海",
        start_date="2030-08-02",
        end_date="2030-08-02",
        travel_days=1,
        travelers=2,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
        free_text_input=free_text,
    )


def _planner_with_stub_graph() -> MultiAgentTripPlanner:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.trip_graph = SimpleNamespace(
        run=lambda request, progress_callback=None: _publishable_plan()
    )
    return planner


def test_plan_trip_reuses_entry_attached_contract(extraction_calls) -> None:
    attached = attach_contract_to_trip_request(
        _direct_request("两个人想去杭州走走")
    )
    assert attached.semantic_contract is not None
    baseline = extraction_calls["count"]

    _planner_with_stub_graph().plan_trip(attached)

    assert extraction_calls["count"] == baseline


def test_plan_trip_attaches_once_for_direct_callers(extraction_calls) -> None:
    request = _direct_request("两个人想去杭州走走")
    assert request.semantic_contract is None

    _planner_with_stub_graph().plan_trip(request)

    assert extraction_calls["count"] == 1


def test_empty_free_text_attach_matches_form_only_contract() -> None:
    """Zero-extraction path must still yield a full form-backed contract."""
    attached = attach_contract_to_trip_request(_direct_request(""))
    contract = attached.semantic_contract
    assert contract is not None
    assert contract.destination_city.value == "杭州"
    assert contract.destination_city.source == "form_confirmed"
    assert contract.origin_city.value == "上海"
    assert contract.travelers.value == 2
