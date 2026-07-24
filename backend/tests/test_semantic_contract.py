"""Semantic trip contract: provenance, merge conflicts, pending confirmation."""

from __future__ import annotations

import sys
import types

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
)
from app.services.semantic_contract_service import (
    SemanticContractService,
    bind,
    get_semantic_contract_service,
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


def test_field_bindings_carry_explicit_inferred_and_unknown() -> None:
    service = SemanticContractService()
    contract = service.extract_from_text(
        "这个周末两个人从上海出去透透气，预算3000，不想太累"
    )

    assert contract.origin_city.source == "user_explicit"
    assert contract.origin_city.value == "上海"
    assert contract.travelers.source == "user_explicit"
    assert contract.travelers.value == 2
    assert contract.budget.source == "user_explicit"
    assert contract.budget.value == 3000
    assert contract.pace.source == "user_explicit"
    assert contract.pace.value == "轻松"
    assert contract.travel_days.source == "rule_inferred"
    assert contract.travel_days.value == 2
    assert contract.start_date.source == "rule_inferred"
    assert contract.start_date.pending_confirmation is True
    assert contract.destination_city.source == "unknown"
    assert "start_date" in contract.pending_fields


def test_latest_user_explicit_overrides_form_defaults() -> None:
    """最新高置信用户明示应覆盖表单旧值（避免默认交通等锁死对话）。"""
    service = get_semantic_contract_service()
    form = service.contract_from_form(
        RecommendationContext(
            origin_city="宝鸡扶风",
            start_date="2026-08-01",
            end_date="2026-08-02",
            travel_days=2,
            travelers=3,
            budget=3000,
            transportation="公共交通",
        )
    )
    incoming = service.extract_from_text(
        "这个周末从上海出发，两个人，预算5000，自驾"
    )
    merged = service.merge(form, incoming)

    assert merged.origin_city.value == "上海"
    assert merged.origin_city.source == "user_explicit"
    assert merged.travelers.value == 2
    assert merged.budget.value == 5000
    assert merged.transportation.value == "自驾"
    # 未在消息中改写的已确认日期仍保留
    assert merged.start_date.value == "2026-08-01"
    assert merged.end_date.value == "2026-08-02"
    assert merged.travel_days.value == 2
    assert merged.conflicts


def test_rule_inferred_does_not_override_form_confirmed_travelers() -> None:
    service = get_semantic_contract_service()
    form = service.contract_from_form(RecommendationContext(travelers=2))
    incoming = service.extract_from_text("周末跟父母去避暑")
    merged = service.merge(form, incoming)

    assert merged.travelers.value == 2
    assert merged.travelers.source == "form_confirmed"
    assert merged.travelers.pending_confirmation is True
    assert merged.conflicts


def test_vague_inputs_stay_pending_or_ask() -> None:
    agent = build_agent()

    vague = agent.chat(request_for("想出去玩"))
    assert vague.needs_more_info is True
    assert vague.recommendations == []

    thin = agent.chat(request_for("预算不太多"))
    assert thin.needs_more_info is True

    friends = agent.chat(
        request_for("和朋友一起玩，想轻松一点", origin_city="上海", travel_days=2)
    )
    assert friends.needs_more_info is False
    assert friends.semantic_contract is not None
    assert friends.semantic_contract.travelers.source == "unknown"
    # 人数未知不得默认成 1
    if friends.recommendations:
        assert friends.recommendations[0].form_patch.travelers is None
        # 估算预算不得写进用户预算字段
        assert friends.recommendations[0].form_patch.budget is None
        assert friends.recommendations[0].estimated_budget


def test_apply_values_exclude_pending_weekend_dates() -> None:
    service = SemanticContractService()
    contract = service.extract_from_text("这个周末两个人从上海出发，预算3000")
    apply = service.apply_values(contract)

    assert apply["origin_city"] == "上海"
    assert apply["travelers"] == 2
    assert apply["budget"] == 3000
    assert "start_date" not in apply
    assert "end_date" not in apply
    assert apply.get("travel_days") == 2


def test_kinship_conflict_keeps_party_signal() -> None:
    service = SemanticContractService()
    contract = service.extract_from_text("周末跟父母两个人去避暑")

    assert contract.travelers.value == 2
    assert contract.travel_party.is_known()
    assert "父母" in str(contract.travel_party.value)
    assert contract.conflicts or contract.travel_party.pending_confirmation


def test_gentle_pace_recommendations_do_not_rewrite_to_moderate() -> None:
    agent = build_agent()
    response = agent.chat(
        request_for("这个周末两个人从上海出去透透气，预算3000，不想太累")
    )
    assert response.needs_more_info is False
    paces = [item.pace for item in response.recommendations]
    assert paces
    assert all(pace in {"轻松", "舒缓"} for pace in paces)


def test_partial_origin_preference_recommends_without_inventing_budget() -> None:
    agent = build_agent()
    response = agent.chat(request_for("从宝鸡出发，想看看自然风景", origin_city="宝鸡"))
    assert response.needs_more_info is False
    assert response.recommendations
    for item in response.recommendations:
        assert item.form_patch.budget is None
        assert item.estimated_budget is not None
        assert item.form_patch.travelers is None


def test_bind_apply_safe_rules() -> None:
    assert bind("上海", "user_explicit", "high").is_apply_safe()
    assert not bind("2026-07-25", "rule_inferred", "medium", pending=True).is_apply_safe()
    assert bind(2, "rule_inferred", "high").is_apply_safe()
    assert not bind(None, "unknown").is_apply_safe()


def test_budget_not_inferred_from_people_or_days_quantifiers() -> None:
    service = SemanticContractService()
    people = service.extract_from_text("最多3人一起玩")
    assert people.budget.source == "unknown"
    assert people.travelers.value == 3

    days = service.extract_from_text("大约2天出行")
    assert days.budget.source == "unknown"
    assert days.travel_days.value == 2

    money = service.extract_from_text("预算大约3000")
    assert money.budget.value == 3000
    yuan = service.extract_from_text("控制在5000元左右")
    assert yuan.budget.value == 5000
    ticket = service.extract_from_text("门票50元，想去杭州玩两天")
    assert ticket.budget.source == "unknown"
    # Destination is written into the contract (shared city_mention), not agent-only.
    assert ticket.destination_city.value == "杭州"
    assert ticket.destination_city.source == "user_explicit"


def test_negated_destination_is_not_extracted() -> None:
    agent = build_agent()
    for text in ("不想去昆明太远", "不去昆明", "别去昆明", "没想去昆明"):
        assert agent._mentioned_destination(text, None) is None
    assert agent._mentioned_destination("想去昆明", None) == "昆明"
    assert agent._mentioned_destination("准备去昆明玩", None) == "昆明"


def test_form_patch_does_not_invent_default_preferences() -> None:
    agent = build_agent()
    context = RecommendationContext(origin_city="上海", travel_days=2)
    contract = agent._build_semantic_contract("从上海出发玩两天", context)
    item = agent._build_recommendation(
        {"city": "杭州", "reason": "测试", "preferences": ["美食"]},
        context,
        contract,
    )
    assert item is not None
    assert item.form_patch.preferences == []
    assert "美食" in item.suggested_preferences


def test_pace_or_budget_only_needs_more_info() -> None:
    agent = build_agent()
    pace_only = agent.chat(request_for("特种兵"))
    assert pace_only.needs_more_info is True
    assert pace_only.recommendations == []

    weekend_only = agent.chat(request_for("这个周末"))
    assert weekend_only.needs_more_info is True


def test_chinese_budget_amounts() -> None:
    service = SemanticContractService()
    assert service.extract_from_text("预算三千").budget.value == 3000
    assert service.extract_from_text("预算三万").budget.value == 30000
    assert service.extract_from_text("预算一万二").budget.value == 12000
    assert service.extract_from_text("预算1.5万").budget.value == 15000


def test_attach_contract_to_trip_request() -> None:
    """Pure contract attach — quality gate covered in planning/quality commits."""
    from app.models.schemas import TripRequest
    from app.services.semantic_contract_service import attach_contract_to_trip_request

    request = TripRequest(
        origin_city="宝鸡扶风",
        city="眉县",
        destination_source="recommendation",
        start_date="2030-08-01",
        end_date="2030-08-02",
        travel_days=2,
        travelers=3,
        budget=3000,
        transportation="自驾",
        accommodation="经济型酒店",
        preferences=["自然风光"],
        free_text_input="跟父母去避暑，不想太累，预算三千",
    )
    attached = attach_contract_to_trip_request(request)
    assert attached.semantic_contract is not None
    assert attached.semantic_contract.origin_city.value == "宝鸡扶风"
    assert attached.semantic_contract.destination_city.value == "眉县"
    assert attached.semantic_contract.pace.value == "轻松"
    assert attached.semantic_contract.travel_party.is_known()


def test_hard_block_issues_without_ack_and_with_ack() -> None:
    """Service-level hard-block issues; HTTP 422 wiring covered with trip routes."""
    from app.models.schemas import TripRequest
    from app.services.semantic_contract_service import (
        USER_CONTRACT_ACK_MARKER,
        collect_semantic_hard_block_issues,
    )

    request = TripRequest(
        origin_city="宝鸡扶风",
        city="眉县",
        destination_source="manual",
        start_date="2030-08-01",
        end_date="2030-08-02",
        travel_days=2,
        travelers=1,
        budget=3000,
        transportation="自驾",
        accommodation="经济型酒店",
        preferences=["自然风光"],
        free_text_input="周末跟父母去避暑，不想太累",
    )
    issues = collect_semantic_hard_block_issues(request)
    assert issues, "expected hard-block issues without acknowledgment"
    codes = {item["code"] for item in issues}
    assert "SEMANTIC_CONTRACT_CONFLICT_BLOCK" in codes or "SEMANTIC_CONTRACT_PENDING" in codes

    acked_flag = request.model_copy(update={"semantic_risks_acknowledged": True})
    assert collect_semantic_hard_block_issues(acked_flag) == []

    acked_text = request.model_copy(
        update={
            "free_text_input": (
                f"{request.free_text_input} {USER_CONTRACT_ACK_MARKER}"
            )
        }
    )
    assert collect_semantic_hard_block_issues(acked_text) == []


def test_hard_block_form_free_text_divergence_on_budget_and_origin() -> None:
    from app.models.schemas import TripRequest
    from app.services.semantic_contract_service import collect_semantic_hard_block_issues

    budget = TripRequest(
        origin_city="上海",
        city="杭州",
        destination_source="manual",
        start_date="2030-08-01",
        end_date="2030-08-02",
        travel_days=2,
        travelers=2,
        budget=1000,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=["休闲"],
        free_text_input="从上海出发想去杭州玩两天，预算5000",
    )
    codes = {item["code"] for item in collect_semantic_hard_block_issues(budget)}
    assert "SEMANTIC_FORM_FREE_TEXT_DIVERGENCE" in codes

    origin = TripRequest(
        origin_city="上海",
        city="杭州",
        destination_source="manual",
        start_date="2030-08-01",
        end_date="2030-08-02",
        travel_days=2,
        travelers=2,
        budget=3000,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=["休闲"],
        free_text_input="从宝鸡出发想去杭州玩两天，预算3000",
    )
    codes2 = {item["code"] for item in collect_semantic_hard_block_issues(origin)}
    assert "SEMANTIC_FORM_FREE_TEXT_DIVERGENCE" in codes2

    aligned = budget.model_copy(update={"budget": 5000})
    assert collect_semantic_hard_block_issues(aligned) == []


def test_hard_block_skips_when_form_resolves_pending_weekend_dates() -> None:
    from app.models.schemas import TripRequest
    from app.services.semantic_contract_service import collect_semantic_hard_block_issues

    # Weekend dates are pending in free text, but form already has concrete dates.
    request = TripRequest(
        origin_city="上海",
        city="杭州",
        destination_source="manual",
        start_date="2030-08-01",
        end_date="2030-08-02",
        travel_days=2,
        travelers=2,
        budget=3000,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=["休闲"],
        free_text_input="这个周末两个人从上海出去透透气，预算3000，不想太累",
    )
    assert collect_semantic_hard_block_issues(request) == []


def test_adversarial_holes_closed() -> None:
    """CI-hardened version of scripts/probe_contract_bugs.py."""
    service = SemanticContractService()
    agent = build_agent()

    people = service.extract_from_text("最多3人一起玩")
    assert people.budget.source == "unknown"
    assert people.travelers.value == 3

    ticket = service.extract_from_text("门票50元，酒店200元一晚")
    assert ticket.budget.source == "unknown"

    for text in ("不想去昆明太远", "不去昆明", "别去昆明"):
        assert agent._mentioned_destination(text, None) is None
    assert agent._mentioned_destination("想去昆明", None) == "昆明"

    form = service.contract_from_form(
        RecommendationContext(transportation="公共交通", origin_city="上海")
    )
    merged = service.merge(form, service.extract_from_text("我们自驾去玩两天"))
    assert merged.transportation.value == "自驾"
    assert merged.transportation.source == "user_explicit"

    form2 = service.contract_from_form(RecommendationContext(origin_city="宝鸡"))
    merged2 = service.merge(form2, service.extract_from_text("改成从上海出发"))
    assert merged2.origin_city.value == "上海"

    form3 = service.contract_from_form(RecommendationContext(travelers=2))
    kin = service.merge(form3, service.extract_from_text("跟父母去避暑"))
    assert kin.travelers.value == 2
    assert kin.travelers.source == "form_confirmed"
    assert kin.travelers.pending_confirmation is True

    ctx = RecommendationContext(origin_city="上海", travel_days=2)
    contract = agent._build_semantic_contract("从上海出发玩两天", ctx)
    item = agent._build_recommendation({"city": "杭州", "reason": "x"}, ctx, contract)
    assert item is not None
    assert item.form_patch.preferences == []
    assert item.form_patch.budget is None

    pace_only = agent.chat(request_for("特种兵"))
    assert pace_only.needs_more_info is True
