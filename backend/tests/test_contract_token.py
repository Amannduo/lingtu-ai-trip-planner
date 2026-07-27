"""S4b security tests for the signed recommendation-contract token.

The token proves only "this server built that contract"; it is not a form
confirmation.  Every defect must silently degrade to the no-token path.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.services.contract_token_service as token_module
from app.api.auth import get_optional_current_user
from app.api.main import app
from app.models.schemas import (
    DayPlan,
    FieldBinding,
    SemanticTripContract,
    TripPlan,
    TripPlanQualityResult,
)
from app.services.contract_token_service import (
    TOKEN_TTL_SECONDS,
    issue_contract_token,
    verify_contract_token,
)


def _contract() -> SemanticTripContract:
    contract = SemanticTripContract(
        origin_city=FieldBinding(
            value="太原",
            source="user_explicit",
            confidence="high",
            evidence="太原出发（原文片段）",
        ),
        pace=FieldBinding(
            value="轻松",
            source="user_explicit",
            confidence="high",
            evidence="带爸妈慢慢玩",
        ),
        excluded_destinations=FieldBinding(
            value=["大同"],
            source="user_explicit",
            confidence="high",
            evidence="不想去大同",
        ),
    )
    contract.refresh_pending_fields()
    return contract


NOW = 1_800_000_000.0


def test_roundtrip_restores_bindings_and_strips_evidence() -> None:
    token = issue_contract_token(_contract(), subject="anon", now=NOW)
    assert token

    # Evidence (raw user text) must not be visible in the encoded payload.
    body = json.dumps  # noqa: F841 - clarity only
    import base64

    payload = json.loads(
        base64.urlsafe_b64decode(
            token.split(".")[0] + "=" * (-len(token.split(".")[0]) % 4)
        )
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "原文片段" not in serialized
    assert "带爸妈" not in serialized

    restored = verify_contract_token(token, subject="anon", now=NOW + 10)
    assert restored is not None
    assert restored.origin_city.value == "太原"
    assert restored.origin_city.source == "user_explicit"
    assert restored.pace.value == "轻松"
    assert restored.excluded_destinations.value == ["大同"]


def test_tampered_payload_is_rejected() -> None:
    token = issue_contract_token(_contract(), subject="anon", now=NOW)
    body, signature = token.split(".")
    tampered = body[:-2] + ("AA" if body[-2:] != "AA" else "BB")
    assert verify_contract_token(
        f"{tampered}.{signature}", subject="anon", now=NOW + 1
    ) is None


def test_tampered_signature_is_rejected() -> None:
    token = issue_contract_token(_contract(), subject="anon", now=NOW)
    body, signature = token.split(".")
    flipped = signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB")
    assert verify_contract_token(
        f"{body}.{flipped}", subject="anon", now=NOW + 1
    ) is None


def test_expired_token_is_rejected() -> None:
    token = issue_contract_token(_contract(), subject="anon", now=NOW)
    assert verify_contract_token(
        token, subject="anon", now=NOW + TOKEN_TTL_SECONDS + 1
    ) is None


def test_wrong_audience_is_rejected(monkeypatch) -> None:
    token = issue_contract_token(_contract(), subject="anon", now=NOW)
    monkeypatch.setattr(token_module, "TOKEN_AUDIENCE", "another-audience")
    assert verify_contract_token(token, subject="anon", now=NOW + 1) is None


def test_incompatible_version_is_rejected(monkeypatch) -> None:
    token = issue_contract_token(_contract(), subject="anon", now=NOW)
    monkeypatch.setattr(token_module, "TOKEN_VERSION", 2)
    assert verify_contract_token(token, subject="anon", now=NOW + 1) is None


def test_cross_user_replay_is_rejected() -> None:
    token_a = issue_contract_token(_contract(), subject="user:alice", now=NOW)
    assert verify_contract_token(
        token_a, subject="user:bob", now=NOW + 1
    ) is None
    # Anonymous tokens are not redeemable by authenticated users either.
    token_anon = issue_contract_token(_contract(), subject="anon", now=NOW)
    assert verify_contract_token(
        token_anon, subject="user:alice", now=NOW + 1
    ) is None


def test_oversized_payload_is_never_issued() -> None:
    contract = _contract()
    contract.conflicts = ["超长冲突记录" * 200 for _ in range(30)]
    assert issue_contract_token(contract, subject="anon", now=NOW) is None


def test_oversized_incoming_token_is_rejected() -> None:
    assert verify_contract_token(
        "a" * (token_module.MAX_TOKEN_CHARS + 1), subject="anon", now=NOW
    ) is None


def test_unconfigured_key_disables_tokens(monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_secret_key", "short")
    monkeypatch.setattr(
        get_settings(), "recommendation_token_signing_key", ""
    )
    assert issue_contract_token(_contract(), subject="anon", now=NOW) is None
    assert verify_contract_token("abc.def", subject="anon", now=NOW) is None


# ── end-to-end: token contract reaches the generation contract ─────────


def _plan_payload(**overrides) -> dict:
    payload = {
        "origin_city": "太原",
        "city": "晋中",
        "start_date": "2030-08-02",
        "end_date": "2030-08-03",
        "travel_days": 2,
        "travelers": 2,
        "transportation": "公共交通",
        "accommodation": "经济型酒店",
        "preferences": [],
        "free_text_input": "",
        "semantic_risks_acknowledged": True,
    }
    payload.update(overrides)
    return payload


def _publishable_plan() -> TripPlan:
    return TripPlan(
        city="晋中",
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
        quality=TripPlanQualityResult(
            status="passed",
            score=90,
            publishable=True,
            review_required=False,
        ),
    )


def test_token_contract_merges_into_generation_contract(monkeypatch) -> None:
    seen: dict = {}

    def capture_plan_trip(request, progress_callback=None, **_kwargs):
        seen["request"] = request
        return _publishable_plan()

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: SimpleNamespace(plan_trip=capture_plan_trip),
    )
    app.dependency_overrides[get_optional_current_user] = lambda: None
    try:
        token = issue_contract_token(_contract(), subject="anon")
        assert token
        with TestClient(app) as client:
            response = client.post(
                "/api/trip/plan",
                json=_plan_payload(recommendation_token=token),
            )
        assert response.status_code == 200
        contract = seen["request"].semantic_contract
        assert contract is not None
        # The session exclusion arrives structurally — no machine block.
        assert contract.excluded_destinations.value == ["大同"]
        assert contract.pace.value == "轻松"
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)


def test_invalid_token_degrades_to_no_token_path(monkeypatch) -> None:
    seen: dict = {}

    def capture_plan_trip(request, progress_callback=None, **_kwargs):
        seen["request"] = request
        return _publishable_plan()

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: SimpleNamespace(plan_trip=capture_plan_trip),
    )
    app.dependency_overrides[get_optional_current_user] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/trip/plan",
                json=_plan_payload(recommendation_token="broken.token"),
            )
        assert response.status_code == 200
        contract = seen["request"].semantic_contract
        assert contract is not None
        assert not (contract.excluded_destinations.value or [])
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)
