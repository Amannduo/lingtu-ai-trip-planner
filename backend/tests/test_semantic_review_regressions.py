"""Regressions found by independent review of the semantic-consistency work.

Each test pins one defect so the fix cannot silently come undone.
"""

from __future__ import annotations

import sys
import types
from datetime import date

import pytest

hello_agents = types.ModuleType("hello_agents")
hello_agents.SimpleAgent = object
hello_agents.HelloAgentsLLM = object
sys.modules.setdefault("hello_agents", hello_agents)

from app.agents.destination_recommender_agent import DestinationRecommenderAgent
from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import (
    ChatMessage,
    DestinationChatRequest,
    RecommendationContext,
    TripPlan,
    TripRequest,
)
from app.services.destination_feasibility_service import (
    get_destination_feasibility_service,
)
from app.services.semantic_contract_service import (
    _field_resolved_by_request,
    blocking_conflicts,
    collect_semantic_hard_block_issues,
    decided_constraint_text,
    extract_user_utterance,
    field_label,
    get_semantic_contract_service,
    has_affirmative_weekend,
    parse_machine_block,
)
from app.services.trip_plan_quality_service import TripPlanQualityService

REFERENCE_DATE = date(2026, 7, 26)

DEFAULT_WEEKEND_FREE_TEXT = "\n".join(
    [
        "【目的地】眉县",
        "【约束】轻松；每日主景点不超过2个",
        "【时段】周末Sat-Sun·2天",
        "【抵达建议】建议周五下午或傍晚出发，提前抵达后休息或在酒店周边简单活动。（可选，尚未确认）",
        "【同行】两个年轻人",
        "【理由】适合陪父母避暑休闲的低强度节奏。",
        "【原文】周末从太原出发，两个人，想去附近避暑。",
    ]
)


def extract(text: str):
    return get_semantic_contract_service().extract_from_text(
        text, reference_date=REFERENCE_DATE
    )


def build_agent() -> DestinationRecommenderAgent:
    agent = DestinationRecommenderAgent.__new__(DestinationRecommenderAgent)
    agent.agent = None
    agent.llm = None
    agent._search_city_highlights = lambda city, preferences: []
    agent._weather_summary = lambda city: None
    return agent


def chat(agent, text: str, **context):
    return agent.chat(
        DestinationChatRequest(
            messages=[ChatMessage(role="user", content=text)],
            context=RecommendationContext(**context),
        )
    )


# ── 1. a date mention is not automatically a departure date ───────────


@pytest.mark.parametrize(
    "text",
    [
        "这个周末想出去玩，8月1号之前得定好",
        "周末去大同，7月28号有个会所以早点定",
    ],
)
def test_deadline_date_does_not_hijack_the_weekend_window(text: str) -> None:
    contract = extract(text)
    assert contract.date_pattern.value == "weekend"
    assert contract.start_date.pending_confirmation is True
    assert contract.start_date.source == "rule_inferred"


def test_departure_date_still_wins_over_the_weekend() -> None:
    contract = extract("不要周末了，改成9月15号出发")
    assert contract.start_date.value == "2026-09-15"
    assert contract.start_date.source == "user_explicit"


def test_stated_departure_date_suppresses_the_weekend_banner() -> None:
    """A Tuesday trip must not be labelled 周六—周日 or offered a Friday card."""
    contract = extract("这个周末有事，7月28号出发去大同")
    assert contract.start_date.value == "2026-07-28"
    assert not contract.weekend_style.is_known()
    assert not contract.early_arrival_hint.is_known()
    assert build_agent()._is_default_weekend(contract) is False


# ── 2. negation of a qualified weekend marker ─────────────────────────


@pytest.mark.parametrize(
    "text",
    ["不要下周末出发", "不要这周末", "不要本周末", "不想下周末去", "别去下周末"],
)
def test_negated_qualified_weekend_is_not_a_weekend_request(text: str) -> None:
    assert has_affirmative_weekend(text) is False


# ── 3. exclusions are recoverable, not permanent ──────────────────────


def test_explicitly_asking_for_an_excluded_city_wins() -> None:
    contract = extract("不想去大同，还是去大同吧")
    assert contract.destination_city.value == "大同"
    assert not contract.excluded_destinations.is_known()
    assert any("先排除后又选择" in note for note in contract.conflicts)


def test_recommender_keeps_an_explicitly_requested_city() -> None:
    response = chat(
        build_agent(),
        "不想去大同，还是去大同吧",
        origin_city="太原",
        travel_days=2,
    )
    assert "大同" in [item.city for item in response.recommendations]


def test_acknowledged_exclusion_downgrades_to_a_warning() -> None:
    request = TripRequest(
        origin_city="太原",
        city="大同",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=2,
        transportation="公共交通",
        accommodation="经济型酒店",
        free_text_input="不想去大同",
        semantic_risks_acknowledged=True,
    )
    plan = TripPlan(
        city="大同",
        start_date="2026-08-01",
        end_date="2026-08-02",
        overall_suggestions="测试",
        days=[],
    )
    quality = TripPlanQualityService()
    contract = get_semantic_contract_service().extract_from_text("不想去大同")
    issues: list[tuple] = []
    quality._evaluate_exclusions(
        contract,
        plan,
        get_destination_feasibility_service(),
        lambda code, severity, path, message, suggestion: issues.append(
            (code, severity)
        ),
        True,
    )
    assert ("SEMANTIC_EXCLUDED_DESTINATION", "warning") in issues

    issues.clear()
    quality._evaluate_exclusions(
        contract,
        plan,
        get_destination_feasibility_service(),
        lambda code, severity, path, message, suggestion: issues.append(
            (code, severity)
        ),
        False,
    )
    assert ("SEMANTIC_EXCLUDED_DESTINATION", "error") in issues


def test_empty_plan_city_does_not_trigger_a_false_exclusion() -> None:
    contract = get_semantic_contract_service().extract_from_text("不想去大同")
    plan = TripPlan(
        city="太原",
        start_date="2026-08-01",
        end_date="2026-08-01",
        overall_suggestions="",
        days=[],
    )
    plan.city = ""
    issues: list[str] = []
    TripPlanQualityService()._evaluate_exclusions(
        contract,
        plan,
        get_destination_feasibility_service(),
        lambda code, *rest: issues.append(code),
        False,
    )
    assert issues == []


# ── 4. scope routing and empty results ────────────────────────────────


def test_far_scope_does_not_take_the_nearby_short_path() -> None:
    agent = build_agent()
    calls: list[str] = []

    def seeds(request):
        calls.append("llm")
        return [{"city": "厦门", "reason": "海边休闲", "suggested_days": 2}]

    agent._generate_candidate_seeds = seeds
    chat(agent, "周末想去远一点的地方，两天", origin_city="太原", travel_days=2)
    assert calls == ["llm"]


def test_exhausted_circle_falls_back_instead_of_returning_nothing() -> None:
    response = chat(
        build_agent(),
        "周末从太原出发，不想去大同，不想去晋中，不想去忻州，不想去石家庄",
        origin_city="太原",
        travel_days=2,
    )
    excluded = {"大同", "晋中", "忻州", "石家庄"}
    assert not excluded.intersection({item.city for item in response.recommendations})
    assert "0个" not in response.reply


# ── 5. machine text vs user text ──────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["【目的地】大理\n【时段】改成三天", "【约束】不要海边"],
)
def test_short_bracketed_user_text_is_left_alone(text: str) -> None:
    assert extract_user_utterance(text) == text


def test_inline_machine_block_is_still_stripped() -> None:
    inline = (
        "【目的地】大同 【约束】轻松 【时段】周末Sat-Sun·2天 "
        "【抵达建议】建议周五下午或傍晚出发 【原文】周末想去附近避暑"
    )
    assert extract_user_utterance(inline) == "周末想去附近避暑"


def test_decided_constraints_exclude_advisory_prose() -> None:
    text = decided_constraint_text(DEFAULT_WEEKEND_FREE_TEXT)
    assert "周末Sat-Sun·2天" in text
    assert "轻松" in text
    assert "建议周五下午" not in text
    assert "适合陪父母避暑休闲" not in text


def test_parse_machine_block_reads_every_label() -> None:
    block = parse_machine_block(DEFAULT_WEEKEND_FREE_TEXT)
    assert block["时段"] == "周末Sat-Sun·2天"
    assert block["目的地"] == "眉县"


def test_advisory_hint_does_not_mark_an_evening_before_departure() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    request = TripRequest(
        origin_city="太原",
        city="眉县",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=2,
        transportation="公共交通",
        accommodation="经济型酒店",
        free_text_input=DEFAULT_WEEKEND_FREE_TEXT,
    )
    assert planner._is_evening_before_departure(request) is False

    friday = request.model_copy(update={"departure_mode": "evening_before"})
    assert planner._is_evening_before_departure(friday) is True


def test_model_prose_does_not_invent_a_gentle_pace() -> None:
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    prose_only = "\n".join(
        [
            "【目的地】南京",
            "【时段】3天",
            "【理由】适合陪父母慢慢逛的轻松安排。",
            "【原文】想去南京看看博物馆",
        ]
    )
    request = TripRequest(
        city="南京",
        start_date="2026-08-01",
        end_date="2026-08-03",
        travel_days=3,
        travelers=1,
        transportation="公共交通",
        accommodation="经济型酒店",
        free_text_input=prose_only,
    )
    assert planner._needs_gentle_pacing(request) is False


# ── 6. normalization must never collapse to an empty key ──────────────


def test_province_named_city_still_normalizes_to_itself() -> None:
    service = get_destination_feasibility_service()
    assert service.normalize_location_for_matching("吉林市") == "吉林"
    assert service.normalize_location_for_matching("吉林") == "吉林"


def test_origin_mismatch_is_still_detected_for_a_province_named_city() -> None:
    request = TripRequest(
        origin_city="吉林市",
        city="大同",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=1,
        transportation="公共交通",
        accommodation="经济型酒店",
        free_text_input="从长春出发去大同玩两天",
    )
    codes = {issue["code"] for issue in collect_semantic_hard_block_issues(request)}
    assert "SEMANTIC_FORM_FREE_TEXT_DIVERGENCE" in codes


# ── 7. no raw field keys in user-facing copy ──────────────────────────


def test_pending_fields_are_shown_with_chinese_labels() -> None:
    response = chat(
        build_agent(),
        "不想只在附近转，想去远一点的地方玩两天",
        origin_city="太原",
        travel_days=2,
    )
    assert "destination_scope" not in response.reply
    assert field_label("destination_scope") == "目的地范围"


# ── 8. one shared definition of a blocking conflict ───────────────────


def test_interpreted_payload_ships_the_blocking_conflict_subset() -> None:
    service = get_semantic_contract_service()
    merged = service.merge(
        service.contract_from_form(RecommendationContext(budget=3000)),
        extract("预算改成5000"),
    )
    payload = service.interpreted_payload(merged)
    assert payload["conflicts"], "the audit trail must still be present"
    assert payload["blocking_conflicts"] == []


# ── 9. wider exclusion vocabulary ─────────────────────────────────────


@pytest.mark.parametrize(
    "text, themes, cities",
    [
        ("讨厌爬山", ["爬山"], None),
        ("别选大理", None, ["大理"]),
        ("不能去青岛", None, ["青岛"]),
        ("不想去昆明和大理", None, ["昆明", "大理"]),
    ],
)
def test_exclusion_vocabulary_matches_the_negation_vocabulary(
    text: str, themes, cities
) -> None:
    contract = extract(text)
    assert contract.excluded_themes.value == themes
    assert contract.excluded_destinations.value == cities


def test_station_mention_is_not_an_excluded_destination() -> None:
    assert not extract("不去北京西站接人").excluded_destinations.is_known()


# ── second review round ───────────────────────────────────────────────


def test_message_conflicts_survive_the_merge() -> None:
    """A conflict found while reading the message must reach every caller."""
    service = get_semantic_contract_service()
    merged = service.merge(
        service.contract_from_form(RecommendationContext(origin_city="太原")),
        extract("十个年轻人和十个学生和一个老人出发"),
    )
    assert any("超出系统支持" in note for note in merged.conflicts)
    assert any("超出系统支持" in note for note in blocking_conflicts(merged))


def test_resolved_exclusion_reversal_is_recorded_but_does_not_block() -> None:
    contract = extract("不想去大同，还是去大同吧")
    assert any("以最新选择为准" in note for note in contract.conflicts)
    assert blocking_conflicts(contract) == []


def test_conflicts_shown_to_users_carry_chinese_labels() -> None:
    service = get_semantic_contract_service()
    merged = service.merge(
        service.contract_from_form(RecommendationContext(travelers=2)),
        extract("三个人还是五个人还没定"),
    )
    shown = blocking_conflicts(merged)
    assert shown
    assert not any(note.startswith("travelers:") for note in shown)
    assert any(note.startswith("人数：") for note in shown)


@pytest.mark.parametrize(
    "text",
    [
        "我8月10号去上海出差，这个周末想在附近转转",
        "8月1号开始上班，这周末想出去玩",
        "周末想去周边，7月31号走亲戚",
    ],
)
def test_incidental_date_does_not_cancel_the_weekend(text: str) -> None:
    contract = extract(text)
    assert contract.date_pattern.value == "weekend"
    assert contract.start_date.pending_confirmation is True


def test_rescheduling_phrase_still_sets_the_departure_date() -> None:
    assert extract("不要周末了，改到9月15号").start_date.value == "2026-09-15"
    assert extract("周末不合适，9月15号出发").start_date.value == "2026-09-15"


def test_far_scope_is_not_answered_with_the_short_haul_circle() -> None:
    agent = build_agent()
    agent._generate_candidate_seeds = lambda request: [
        {"city": "厦门", "reason": "海边休闲", "suggested_days": 5}
    ]
    response = chat(
        agent,
        "从太原出发想去远一点的地方玩五天",
        origin_city="太原",
        travel_days=5,
    )
    circle = set(get_destination_feasibility_service().nearby_destinations("太原"))
    assert response.recommendations
    assert not circle.intersection({item.city for item in response.recommendations})


@pytest.mark.parametrize(
    "text, expected",
    [
        ("不想去天津和北京站", ["天津"]),
        ("不想去天津、青岛和北京南站", ["天津", "青岛"]),
        ("别去青岛、烟台和威海", ["青岛", "烟台", "威海"]),
    ],
)
def test_station_guard_only_drops_the_station_term(text: str, expected) -> None:
    assert extract(text).excluded_destinations.value == expected


@pytest.mark.parametrize("text", ["不想再去大同了，换个地方吧", "不想跟他们去大同"])
def test_negation_tolerates_a_short_filler(text: str) -> None:
    contract = extract(text)
    assert contract.excluded_destinations.value == ["大同"]
    assert not contract.destination_city.is_known()


def test_relaxed_phrasing_is_still_not_a_negation() -> None:
    """"不想太累去大同" chooses 大同; the filler list must stay closed."""
    contract = extract("不想太累去大同")
    assert contract.destination_city.value == "大同"
    assert not contract.excluded_destinations.is_known()


def test_generated_reason_does_not_resolve_a_pending_pace() -> None:
    request = TripRequest(
        city="大同",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=2,
        transportation="公共交通",
        accommodation="经济型酒店",
        free_text_input=(
            "【目的地】大同\n【约束】无\n【时段】周末Sat-Sun·2天\n"
            "【理由】节奏轻松，适合避暑\n【原文】想出去玩"
        ),
    )
    assert _field_resolved_by_request("pace", request) is False
