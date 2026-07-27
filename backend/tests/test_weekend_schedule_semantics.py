"""Weekend sat_sun default + optional Friday early departure semantics."""

from __future__ import annotations

import sys
import types
from datetime import date, timedelta

import pytest

hello_agents = types.ModuleType("hello_agents")
hello_agents.SimpleAgent = object
hello_agents.HelloAgentsLLM = object
sys.modules.setdefault("hello_agents", hello_agents)

from app.agents.destination_recommender_agent import DestinationRecommenderAgent
from app.models.schemas import (
    ChatMessage,
    DestinationChatRequest,
    RecommendationContext,
    TripRequest,
)
from app.services.semantic_contract_service import (
    EARLY_ARRIVAL_HINT_DEFAULT,
    SemanticContractService,
)


def build_agent() -> DestinationRecommenderAgent:
    agent = DestinationRecommenderAgent.__new__(DestinationRecommenderAgent)
    agent.agent = None
    agent.llm = None
    agent._search_city_highlights = lambda city, preferences: []
    agent._weather_summary = lambda city: None
    return agent


def request_for(text: str, **context) -> DestinationChatRequest:
    return DestinationChatRequest(
        messages=[ChatMessage(role="user", content=text)],
        context=RecommendationContext(**context),
    )


def test_weekend_default_is_sat_sun_two_days_with_hint() -> None:
    service = SemanticContractService()
    contract = service.extract_from_text(
        "这个周末两个人从上海出去，不想太累",
        reference_date=date(2026, 7, 24),
    )
    assert contract.date_pattern.value == "weekend"
    assert contract.weekend_style.value == "sat_sun"
    assert contract.travel_days.value == 2
    assert contract.pace.value == "轻松"
    assert contract.start_date.pending_confirmation is True
    assert contract.end_date.pending_confirmation is True
    assert "周五" in str(contract.early_arrival_hint.value)
    assert not contract.departure_mode.is_known()


def test_weekend_recommendation_cards_and_friday_option() -> None:
    agent = build_agent()
    response = agent.chat(
        request_for("这个周末两个人从上海出去，不想太累", origin_city="上海")
    )
    assert response.needs_more_info is False
    assert response.recommendations
    assert response.semantic_contract is not None
    assert response.semantic_contract.travel_days.value == 2

    defaults = [
        item
        for item in response.recommendations
        if item.schedule_option != "friday_early"
    ]
    assert defaults
    for item in defaults:
        assert item.suggested_days == 2
        assert item.form_patch.travel_days in (2, None)
        # Must not auto-fill concrete 3-day Fri-Sun for default cards
        if item.form_patch.start_date and item.form_patch.end_date:
            span = (
                date.fromisoformat(item.form_patch.end_date)
                - date.fromisoformat(item.form_patch.start_date)
            ).days + 1
            assert span == 2
        assert item.early_arrival_hint
        assert "周五" in item.early_arrival_hint
        assert item.schedule_summary is None or "2 天" in (item.schedule_summary or "")

    friday = next(
        (item for item in response.recommendations if item.schedule_option == "friday_early"),
        None,
    )
    assert friday is not None
    assert friday.decision_label == "周五提前出发"
    assert friday.suggested_days == 3
    assert friday.form_patch.travel_days == 3
    assert friday.form_patch.departure_mode == "evening_before"
    assert friday.form_patch.start_date and friday.form_patch.end_date
    span = (
        date.fromisoformat(friday.form_patch.end_date)
        - date.fromisoformat(friday.form_patch.start_date)
    ).days + 1
    assert span == 3
    assert date.fromisoformat(friday.form_patch.start_date).weekday() == 4  # Friday


def test_explicit_friday_afternoon_is_three_days() -> None:
    service = SemanticContractService()
    contract = service.extract_from_text(
        "周五下午从宝鸡出发，周末去西安玩",
        reference_date=date(2026, 7, 24),
    )
    assert contract.departure_mode.value == "evening_before"
    assert contract.departure_mode.source == "user_explicit"
    assert contract.weekend_style.value == "fri_sun_optional"
    assert contract.travel_days.value == 3
    # 出发时段 user_explicit ≠ 具体年月日已确认：无日历日期时日期仍 pending。
    assert contract.start_date.is_known()
    assert contract.start_date.source == "rule_inferred"
    assert contract.start_date.pending_confirmation is True
    assert contract.end_date.pending_confirmation is True
    assert date.fromisoformat(str(contract.start_date.value)).weekday() == 4
    assert "start_date" in contract.pending_fields
    assert contract.origin_city.value == "宝鸡"
    assert contract.origin_city.source == "user_explicit"
    assert contract.destination_city.value == "西安"
    assert contract.destination_city.source == "user_explicit"


def test_parents_gentle_checklist_strips_intensity() -> None:
    agent = build_agent()
    response = agent.chat(
        request_for("跟父母出去玩，安排轻松一点", origin_city="上海", travel_days=2)
    )
    assert response.recommendations
    for item in response.recommendations:
        blob = f"{item.reason}{item.tradeoff}{item.pace}"
        assert "特种兵" not in blob
        assert "暴走" not in blob
        assert "高强度" not in blob
        if item.schedule_option != "friday_early":
            assert "主景点" in item.tradeoff or "主景点" in item.reason or item.pace in {
                "轻松",
                "舒缓",
            }


def test_legacy_request_without_weekend_fields_still_valid() -> None:
    request = TripRequest(
        city="杭州",
        start_date="2030-08-10",
        end_date="2030-08-12",
        travel_days=3,
        transportation="公共交通",
        accommodation="经济型酒店",
    )
    assert request.date_pattern is None
    assert request.departure_mode is None
    assert request.weekend_style is None


def test_date_span_mismatch_travel_days_hard_rejects() -> None:
    """周六到周日 + travel_days=3 必须拒绝，不能静默改天数。"""
    with pytest.raises(Exception) as exc_info:
        TripRequest(
            city="杭州",
            start_date="2030-08-02",  # Saturday
            end_date="2030-08-03",  # Sunday → 2 days
            travel_days=3,
            transportation="公共交通",
            accommodation="经济型酒店",
        )
    assert "2" in str(exc_info.value) or "天数" in str(exc_info.value)


def test_legacy_recommendation_without_schedule_fields_deserializes() -> None:
    from app.models.schemas import DestinationRecommendation, RecommendationFormPatch

    item = DestinationRecommendation(
        city="苏州",
        reason="适合周末",
        decision_label="最省心",
        tradeoff="人流",
        suggested_days=2,
        pace="轻松",
        budget_fit="预计约 ¥2000",
        highlights=["园林"],
        suggested_preferences=["休闲"],
        form_patch=RecommendationFormPatch(
            city="苏州",
            preferences=["休闲"],
            free_text_input="",
        ),
    )
    assert item.schedule_option is None
    assert item.departure_mode is None
    assert item.early_arrival_hint is None
    assert item.form_patch.schedule_option is None


def test_friday_card_uses_first_ranked_city_after_reordering() -> None:
    agent = build_agent()
    # Force a fixed seed order; ranking/filter should place 苏州 first for 上海 2 天.
    response = agent.chat(
        request_for("这个周末从上海出去玩两天", origin_city="上海", travel_days=2)
    )
    defaults = [
        item for item in response.recommendations if item.schedule_option != "friday_early"
    ]
    friday = next(
        item for item in response.recommendations if item.schedule_option == "friday_early"
    )
    assert defaults
    assert friday.city == defaults[0].city
    assert friday.form_patch.city == defaults[0].city
    assert friday.schedule_option == "friday_early"
    assert friday.decision_label == "周五提前出发"


def test_friday_card_safe_with_one_or_two_cities(monkeypatch) -> None:
    agent = build_agent()

    def one_seed(_request):
        return [{"city": "苏州", "reason": "近", "suggested_days": 2}]

    monkeypatch.setattr(agent, "_generate_candidate_seeds", one_seed)
    monkeypatch.setattr(
        agent,
        "_fallback_candidates",
        lambda _request: [{"city": "苏州", "reason": "近", "suggested_days": 2}],
    )
    # Prevent nearby fill from expanding to 3 cities for this unit check.
    monkeypatch.setattr(
        "app.agents.destination_recommender_agent.get_destination_feasibility_service",
        lambda: type(
            "FS",
            (),
            {
                "normalize_city": staticmethod(lambda c: c),
                "nearby_destinations": staticmethod(lambda _o: ["苏州"]),
                "assess": staticmethod(
                    lambda *a, **k: type(
                        "A",
                        (),
                        {
                            "allowed": True,
                            "severity": "info",
                            "reason": "ok",
                            "transport_note": "高铁",
                            "score": 95,
                            "minimum_days": 2,
                        },
                    )()
                ),
            },
        )(),
    )

    response = agent.chat(
        request_for("这个周末从上海出去", origin_city="上海", travel_days=2)
    )
    defaults = [
        item for item in response.recommendations if item.schedule_option != "friday_early"
    ]
    fridays = [
        item for item in response.recommendations if item.schedule_option == "friday_early"
    ]
    assert 1 <= len(defaults) <= 3
    assert len(fridays) == 1
    assert fridays[0].city == defaults[0].city
    assert fridays[0].city  # non-empty destination


def test_no_friday_card_when_recommendations_empty(monkeypatch) -> None:
    agent = build_agent()
    monkeypatch.setattr(agent, "_generate_candidate_seeds", lambda _r: [])
    monkeypatch.setattr(agent, "_fallback_candidates", lambda _r: [])
    monkeypatch.setattr(
        agent,
        "_filter_and_rank_candidates",
        lambda seeds, context, explicit_city="", intent_text="", contract=None: [],
    )
    response = agent.chat(
        request_for("这个周末两个人出去玩", origin_city="上海", travel_days=2)
    )
    assert response.recommendations == []
    assert not any(
        getattr(item, "schedule_option", None) == "friday_early"
        for item in response.recommendations
    )


def test_weekend_without_origin_does_not_forge_transport() -> None:
    agent = build_agent()
    response = agent.chat(request_for("周末去成都"))
    assert response.recommendations
    for item in response.recommendations:
        blob = f"{item.reason}{item.tradeoff}{item.origin_note or ''}"
        assert "从出发" not in blob
        # Must not invent origin-specific convenience without origin.
        assert "从上海出发" not in blob
        assert "交通方便" not in blob
        if item.origin_note:
            assert "伪造" not in item.origin_note


def test_next_weekend_stays_two_days_and_not_this_week() -> None:
    service = SemanticContractService()
    ref = date(2026, 7, 24)  # Friday
    contract = service.extract_from_text(
        "下周末从上海出发玩两天",
        reference_date=ref,
    )
    assert contract.date_pattern.value == "weekend"
    assert contract.weekend_style.value == "sat_sun"
    assert contract.travel_days.value == 2
    assert not contract.departure_mode.is_known()
    start = date.fromisoformat(str(contract.start_date.value))
    end = date.fromisoformat(str(contract.end_date.value))
    assert start == date(2026, 8, 1)
    assert end == date(2026, 8, 2)
    assert start.weekday() == 5
    assert end.weekday() == 6


def test_this_weekend_fixed_reference_friday() -> None:
    service = SemanticContractService()
    contract = service.extract_from_text(
        "这个周末从上海出去玩",
        reference_date=date(2026, 7, 24),
    )
    assert contract.start_date.value == "2026-07-25"
    assert contract.end_date.value == "2026-07-26"
    assert contract.travel_days.value == 2


def test_weekend_uses_business_timezone_not_utc_server_date() -> None:
    """UTC 2026-07-24 16:30 → Asia/Shanghai 2026-07-25 (Saturday)."""
    from datetime import datetime, timezone

    from app.services.business_calendar import resolve_business_date

    business_day = resolve_business_date(
        now=datetime(2026, 7, 24, 16, 30, tzinfo=timezone.utc),
        business_timezone="Asia/Shanghai",
    )
    assert business_day == date(2026, 7, 25)

    service = SemanticContractService()
    this_weekend = service.extract_from_text(
        "这个周末从上海出去",
        now=datetime(2026, 7, 24, 16, 30, tzinfo=timezone.utc),
        business_timezone="Asia/Shanghai",
    )
    # Business date is Saturday → “这个周末” is that Saturday–Sunday.
    assert this_weekend.start_date.value == "2026-07-25"
    assert this_weekend.end_date.value == "2026-07-26"

    next_weekend = service.extract_from_text(
        "下周末从上海出去",
        now=datetime(2026, 7, 24, 16, 30, tzinfo=timezone.utc),
        business_timezone="Asia/Shanghai",
    )
    assert next_weekend.start_date.value == "2026-08-01"
    assert next_weekend.end_date.value == "2026-08-02"

    # Contrast: if we wrongly used the UTC calendar date (Friday 24th),
    # “这个周末” would still be 25–26; use a sharper UTC midnight case.
    utc_evening_friday = datetime(2026, 7, 24, 16, 30, tzinfo=timezone.utc)
    # Server-local UTC date alone would be 2026-07-24; business must be 25.
    assert utc_evening_friday.astimezone(timezone.utc).date() == date(2026, 7, 24)
    assert business_day != date(2026, 7, 24)


def test_explicit_destination_written_into_semantic_contract() -> None:
    service = SemanticContractService()
    cases = [
        ("周末去成都", None, "成都"),
        ("周五下午从宝鸡出发，周末去西安玩", "宝鸡", "西安"),
        ("从上海去苏州玩两天", "上海", "苏州"),
        ("想去杭州，不想太累", None, "杭州"),
    ]
    for text, origin, dest in cases:
        contract = service.extract_from_text(text, reference_date=date(2026, 7, 24))
        if origin:
            assert contract.origin_city.value == origin, text
            assert contract.origin_city.source == "user_explicit", text
        assert contract.destination_city.value == dest, text
        assert contract.destination_city.source == "user_explicit", text


def test_switch_from_friday_option_back_to_default_clears_three_day_state() -> None:
    """Mirror frontend useRecommendation: friday → default must not keep 3-day residue."""
    agent = build_agent()
    response = agent.chat(
        request_for("这个周末两个人从上海出去，不想太累", origin_city="上海")
    )
    defaults = [
        item for item in response.recommendations if item.schedule_option != "friday_early"
    ]
    friday = next(
        item for item in response.recommendations if item.schedule_option == "friday_early"
    )
    assert defaults and friday

    # Simulate UI state after adopting friday card
    state = {
        "city": friday.form_patch.city,
        "travel_days": friday.form_patch.travel_days,
        "start_date": friday.form_patch.start_date,
        "end_date": friday.form_patch.end_date,
        "departure_mode": friday.form_patch.departure_mode,
        "weekend_style": friday.form_patch.weekend_style,
        "schedule_option": friday.schedule_option,
        "toast": "已按周五下午出发安排",
    }
    assert state["travel_days"] == 3
    assert state["departure_mode"] == "evening_before"
    assert state["schedule_option"] == "friday_early"

    # Switch to default weekend card (frontend clearFridayExpandedState + default branch)
    default = defaults[0]
    patch = default.form_patch
    # clear Friday expanded residue
    state["departure_mode"] = None
    state["weekend_style"] = "sat_sun"
    state["travel_days"] = 2
    if state["start_date"] and state["end_date"]:
        span = (
            date.fromisoformat(state["end_date"]) - date.fromisoformat(state["start_date"])
        ).days + 1
        if span == 3 and date.fromisoformat(state["start_date"]).weekday() == 4:
            state["start_date"] = None
            state["end_date"] = None
    # apply default patch
    state["city"] = patch.city
    state["schedule_option"] = patch.schedule_option or "default_weekend"
    state["weekend_style"] = patch.weekend_style or "sat_sun"
    state["departure_mode"] = None  # weekend sat_sun clears evening_before
    if patch.start_date and patch.end_date:
        state["start_date"] = patch.start_date
        state["end_date"] = patch.end_date
        state["travel_days"] = (
            date.fromisoformat(patch.end_date) - date.fromisoformat(patch.start_date)
        ).days + 1
    else:
        state["start_date"] = None
        state["end_date"] = None
        state["travel_days"] = 2
    state["toast"] = "已采用默认周六—周日两日"

    assert state["travel_days"] == 2
    assert state["departure_mode"] is None
    assert state["schedule_option"] == "default_weekend"
    assert state["weekend_style"] == "sat_sun"
    assert state["start_date"] is None  # pending weekend, no auto 3-day residue
    assert state["end_date"] is None
    assert "周五" not in state["toast"] or "两日" in state["toast"]
