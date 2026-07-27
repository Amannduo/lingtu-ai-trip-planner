"""S4c regression tests: the machine-block write switch.

Default stays ON (dual channel — zero behavior change until ops flips it
after observing the token path). With the switch OFF, form_patch free
text carries only the user's own words, and the session semantics still
reach the planner structurally via the signed token. Machine-block
*reading* stays supported either way (S4d removal is separately gated).
"""

from __future__ import annotations

import pytest

from app.agents.destination_recommender_agent import DestinationRecommenderAgent
from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.config import get_settings
from app.models.schemas import FieldBinding, SemanticTripContract, TripRequest
from app.services.contract_token_service import (
    issue_contract_token,
    verify_contract_token,
)
from app.services.semantic_contract_service import build_generation_contract


def _agent() -> DestinationRecommenderAgent:
    return DestinationRecommenderAgent.__new__(DestinationRecommenderAgent)


def _contract() -> SemanticTripContract:
    contract = SemanticTripContract(
        pace=FieldBinding(
            value="轻松", source="user_explicit", confidence="high"
        ),
        travelers=FieldBinding(
            value=2, source="user_explicit", confidence="high"
        ),
    )
    contract.raw_text = "带爸妈周末出去走走，轻松一点"
    contract.refresh_pending_fields()
    return contract


def _build_free_text() -> str:
    return _agent()._build_structured_free_text(
        contract=_contract(),
        city="晋中",
        reason="短途可达",
        origin_note=None,
        transport_note=None,
        highlights=["平遥古城"],
        early_hint=None,
        is_friday_early=False,
    )


def test_switch_defaults_to_writing_the_machine_block() -> None:
    assert get_settings().recommendation_machine_block_write_enabled is True
    text = _build_free_text()
    assert "【目的地】" in text
    assert "【约束】" in text


def test_switch_off_keeps_only_user_words(monkeypatch) -> None:
    monkeypatch.setattr(
        get_settings(), "recommendation_machine_block_write_enabled", False
    )
    text = _build_free_text()
    assert "【" not in text
    assert text == "带爸妈周末出去走走，轻松一点"


def test_switch_off_semantics_survive_via_token(monkeypatch) -> None:
    """With the block off, the token alone must carry decided semantics
    end to end: recommend → token → entry merge → planner pacing."""
    monkeypatch.setattr(
        get_settings(), "recommendation_machine_block_write_enabled", False
    )
    free_text = _build_free_text()
    token = issue_contract_token(_contract(), subject="anon")
    assert token
    session_contract = verify_contract_token(token, subject="anon")
    assert session_contract is not None

    request = TripRequest(
        city="晋中",
        origin_city="太原",
        start_date="2030-08-02",
        end_date="2030-08-03",
        travel_days=2,
        travelers=2,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
        free_text_input=free_text,
        semantic_risks_acknowledged=True,
    )
    attached, _ = build_generation_contract(
        request, session_contract=session_contract
    )

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    assert planner._needs_gentle_pacing(attached) is True
