"""S4a regression tests: contract-first reads with machine-block fallback.

The recommender→planner handoff moves to structured channels in stages.
S4a pins the dual-read behavior: when the server-built contract carries a
decided value, it is authoritative; the legacy 【约束】 machine block in
free text remains a working fallback until the token handoff replaces it.
"""

from __future__ import annotations

import pytest

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import FieldBinding, SemanticTripContract, TripRequest


def _planner() -> MultiAgentTripPlanner:
    return MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)


def _request(
    free_text: str = "",
    preferences: list[str] | None = None,
    pace: str | None = None,
) -> TripRequest:
    contract = None
    if pace is not None:
        contract = SemanticTripContract(
            pace=FieldBinding(
                value=pace,
                source="user_explicit",
                confidence="high",
                evidence="推荐会话已决定",
            )
        )
    return TripRequest(
        city="杭州",
        origin_city="上海",
        start_date="2030-08-02",
        end_date="2030-08-03",
        travel_days=2,
        travelers=2,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=preferences or [],
        free_text_input=free_text,
        semantic_contract=contract,
    )


def test_gentle_pacing_reads_contract_without_any_text_marker() -> None:
    """A decided contract pace must work with zero keywords in free text."""
    request = _request(free_text="就按之前聊的来", pace="轻松")
    assert _planner()._needs_gentle_pacing(request) is True


def test_decided_compact_pace_overrides_keyword_noise() -> None:
    """An explicit decided pace beats stray keywords in preferences."""
    request = _request(preferences=["轻松"], pace="紧凑")
    assert _planner()._needs_gentle_pacing(request) is False


def test_machine_block_fallback_still_works_without_contract() -> None:
    """Compat channel: the legacy 【约束】 block keeps working (S4c gate)."""
    request = _request(free_text="【约束】轻松")
    assert _planner()._needs_gentle_pacing(request) is True


def test_plain_keyword_fallback_still_works_without_contract() -> None:
    request = _request(free_text="带爸妈一起，不想太累")
    assert _planner()._needs_gentle_pacing(request) is True


def test_unknown_pace_falls_back_to_text_channel() -> None:
    """A contract without a pace binding must not mask the text channel."""
    request = _request(free_text="轻松一点")
    request = request.model_copy(
        update={"semantic_contract": SemanticTripContract()}
    )
    assert _planner()._needs_gentle_pacing(request) is True
