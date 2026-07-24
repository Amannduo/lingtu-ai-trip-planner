from datetime import date
import sys
import types

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
from app.services.destination_feasibility_service import DestinationFeasibilityService


def build_agent() -> DestinationRecommenderAgent:
    agent = DestinationRecommenderAgent.__new__(DestinationRecommenderAgent)
    agent.agent = None
    agent._search_city_highlights = lambda city, preferences: []
    agent._weather_summary = lambda city: None
    return agent


def request_for(text: str, **context) -> DestinationChatRequest:
    return DestinationChatRequest(
        messages=[ChatMessage(role="user", content=text)],
        context=RecommendationContext(**context),
    )


def test_infers_weekend_origin_people_budget_and_pace() -> None:
    agent = build_agent()

    intent = agent._infer_trip_intent(
        "这个周末两个人从上海出去透透气，预算3000，不想太累",
        RecommendationContext(),
    )

    assert intent["origin_city"] == "上海"
    assert intent["travelers"] == 2
    assert intent["travel_days"] == 2
    assert intent["budget"] == 3000
    assert intent["pace"] == "轻松"
    assert "休闲" in intent["preferences"]
    assert date.fromisoformat(intent["start_date"]).weekday() == 5
    assert date.fromisoformat(intent["end_date"]).weekday() == 6


def test_infers_parents_party_summer_escape_and_precise_origin() -> None:
    agent = build_agent()
    original = "周末想从宝鸡扶风出发，跟父母去避暑玩两天"

    # 未确认人数时不传 travelers（与前端 travelersConfirmed 一致）
    intent = agent._infer_trip_intent(original, RecommendationContext())

    assert intent["origin_city"] == "宝鸡扶风"
    assert intent["travelers"] == 3
    assert intent["travel_party"] == "你和父母"
    assert intent["travel_days"] == 2
    assert intent["pace"] == "轻松"
    assert {"自然风光", "休闲"}.issubset(set(intent["preferences"]))
    assert date.fromisoformat(intent["start_date"]).weekday() == 5
    assert date.fromisoformat(intent["end_date"]).weekday() == 6


def test_original_parents_request_overrides_default_one_person_and_uses_baoji_circle() -> None:
    agent = build_agent()

    response = agent.chat(
        request_for(
            "周末想从宝鸡扶风出发，跟父母去避暑玩两天",
            # 未确认人数不传，避免把表单默认 1 人误升格为 form_confirmed
            transportation="公共交通",
            accommodation="经济型酒店",
        )
    )

    assert "从宝鸡扶风出发" in response.reply
    assert "3人（你和父母）" in response.reply
    assert "偏好自然风光、休闲" in response.reply
    assert "轻松节奏" in response.reply
    cities = [item.city for item in response.recommendations if item.schedule_option != "friday_early"]
    assert cities == ["眉县", "麟游县", "太白县"]
    assert all(item.form_patch.travelers == 3 for item in response.recommendations if item.schedule_option != "friday_early")
    assert all(item.form_patch.origin_city == "宝鸡扶风" for item in response.recommendations if item.schedule_option != "friday_early")
    # 用户明确轻松时，方案节奏不得突破轻松带（不含可选周五卡）
    assert [item.pace for item in response.recommendations if item.schedule_option != "friday_early"] == ["轻松", "舒缓", "轻松"]
    assert any(item.schedule_option == "friday_early" for item in response.recommendations)


def test_form_confirmed_travelers_not_silently_overwritten_by_kinship() -> None:
    agent = build_agent()
    # 规则推断“跟父母→3人”不得覆盖表单已确认人数
    intent = agent._infer_trip_intent(
        "周末想从宝鸡扶风出发，跟父母去避暑玩两天",
        RecommendationContext(travelers=1),
    )
    assert intent["travelers"] == 1
    contract = agent._build_semantic_contract(
        "周末想从宝鸡扶风出发，跟父母去避暑玩两天",
        RecommendationContext(travelers=1),
    )
    assert contract.conflicts
    assert contract.travelers.pending_confirmation is True
    assert contract.travelers.source == "form_confirmed"


def test_explicit_people_count_takes_priority_over_kinship_inference() -> None:
    agent = build_agent()

    intent = agent._infer_trip_intent(
        "周末跟父母两个人去避暑",
        RecommendationContext(),
    )

    assert intent["travelers"] == 2
    # 同行关系保留为待确认信号，不得因人数优先被静默丢弃
    assert "父母" in str(intent.get("travel_party") or "")


def test_fufeng_inherits_baoji_short_trip_feasibility_circle() -> None:
    service = DestinationFeasibilityService()

    assert service.normalize_city("宝鸡市扶风县") == "宝鸡"
    assert service.nearby_destinations("宝鸡扶风") == [
        "眉县", "麟游县", "太白县", "凤县", "天水", "西安"
    ]
    assessment = service.assess("宝鸡扶风", "麟游县", 2)
    assert assessment.allowed is True
    assert assessment.severity == "info"
    assert "短途可达范围" in assessment.reason

    rejected = service.assess("宝鸡扶风", "汉中", 2)
    assert rejected.allowed is False
    assert rejected.severity == "error"


def test_chat_returns_distinct_decision_options_and_form_patch() -> None:
    agent = build_agent()

    response = agent.chat(
        request_for("这个周末两个人从上海出发，想轻松一点，预算3000")
    )

    assert response.needs_more_info is False
    assert response.interpreted_context["origin_city"] == "上海"
    core = [item for item in response.recommendations if item.schedule_option != "friday_early"]
    assert len(core) == 3
    assert [item.decision_label for item in core] == ["最省心", "更松弛", "体验丰富"]
    assert any(item.schedule_option == "friday_early" for item in response.recommendations)
    assert all(item.estimated_budget and item.estimated_budget > 0 for item in core)
    assert all(item.tradeoff for item in core)

    selected = response.recommendations[0].form_patch
    assert selected.origin_city == "上海"
    assert selected.travelers == 2
    assert selected.travel_days == 2
    # 周末具体日期为 rule_inferred + pending，不得当作已确认回填
    assert selected.start_date is None
    assert selected.end_date is None
    assert response.semantic_contract is not None
    assert response.semantic_contract.start_date.pending_confirmation is True
    assert "start_date" in (response.interpreted_context.get("pending_fields") or [])


def test_explicit_destination_is_respected_by_fallback() -> None:
    agent = build_agent()

    response = agent.chat(request_for("从上海出发，想去北京玩3天"))

    assert response.recommendations[0].city == "北京"
    assert response.recommendations[0].form_patch.travel_days == 3


def test_vague_message_triggers_only_one_key_question() -> None:
    agent = build_agent()

    response = agent.chat(request_for("玩"))

    assert response.needs_more_info is True
    assert response.recommendations == []
    assert "自然放松、城市美食，还是历史文化" in response.reply

def test_llm_execution_uses_fresh_agent_per_request(monkeypatch) -> None:
    created = []

    class FakeSimpleAgent:
        def __init__(self, **_kwargs):
            created.append(self)

        def run(self, _prompt):
            return "[]"

    monkeypatch.setattr(
        "app.agents.destination_recommender_agent.SimpleAgent",
        FakeSimpleAgent,
    )
    agent = build_agent()
    agent.llm = object()
    request = request_for(
        "这个周末想从上海出发，预算3000，喜欢自然和美食",
        origin_city="上海",
        travel_days=2,
        travelers=2,
        budget=3000,
        preferences=["自然风光", "美食"],
    )

    agent._generate_candidate_seeds(request)
    agent._generate_candidate_seeds(request)

    assert len(created) == 2
    assert created[0] is not created[1]


def test_baoji_weekend_rejects_far_llm_candidates_and_fills_nearby(monkeypatch) -> None:
    agent = build_agent()
    monkeypatch.setattr(
        agent,
        "_generate_candidate_seeds",
        lambda _request: [
            {"city": "新疆", "reason": "远途", "suggested_days": 7},
            {"city": "昆明", "reason": "远途", "suggested_days": 5},
            {"city": "乌鲁木齐", "reason": "远途", "suggested_days": 6},
        ],
    )

    response = agent.chat(
        request_for(
            "这个周末从宝鸡出发，想轻松一点",
            origin_city="宝鸡",
            travel_days=2,
        )
    )

    cities = [
        item.city
        for item in response.recommendations
        if item.schedule_option != "friday_early"
    ]
    assert cities == ["天水", "汉中", "西安"]
    assert not {"新疆", "昆明", "乌鲁木齐"}.intersection(cities)
    for item in response.recommendations:
        patch = item.form_patch
        if item.schedule_option == "friday_early":
            assert patch.travel_days == 3
            assert patch.start_date and patch.end_date
            continue
        assert patch.travel_days == 2
        assert patch.destination_source == "recommendation"
        # 周末具体日期 pending，不得当作已确认写入 form_patch
        assert patch.start_date is None
        assert patch.end_date is None
    assert response.semantic_contract is not None
    assert response.semantic_contract.start_date.pending_confirmation is True


def test_explicit_far_weekend_destination_is_preserved_with_warning() -> None:
    agent = build_agent()

    response = agent.chat(request_for("这个周末从宝鸡出发，想去昆明"))

    assert response.recommendations[0].city == "昆明"
    patch = response.recommendations[0].form_patch
    assert patch.travel_days == 2
    assert patch.destination_source == "manual"
    assert "增加天数" in patch.free_text_input


def test_confirmed_dates_override_model_suggested_days() -> None:
    agent = build_agent()
    context = RecommendationContext(
        origin_city="宝鸡",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=2,
        transportation="公共交通",
        accommodation="经济型酒店",
    )

    contract = agent._build_semantic_contract("周末从宝鸡出发", context)
    item = agent._build_recommendation(
        {"city": "西安", "suggested_days": 7},
        context,
        contract,
    )

    assert item is not None
    assert item.suggested_days == 2
    assert item.form_patch.travel_days == 2
    assert item.form_patch.start_date == "2026-08-01"
    assert item.form_patch.end_date == "2026-08-02"
    TripRequest(
        **item.form_patch.model_dump(exclude={"free_text_input"}),
        free_text_input=item.form_patch.free_text_input,
        intercity_transportation="自动选择",
    )


def test_recommendation_context_normalizes_day_count_from_dates() -> None:
    context = RecommendationContext(
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=7,
    )

    assert context.travel_days == 2


def test_unknown_trip_length_is_not_misclassified_as_one_day(monkeypatch) -> None:
    agent = build_agent()
    monkeypatch.setattr(
        agent,
        "_generate_candidate_seeds",
        lambda _request: [
            {"city": "昆明", "reason": "自然"},
            {"city": "乌鲁木齐", "reason": "风景"},
            {"city": "大理", "reason": "休闲"},
        ],
    )

    response = agent.chat(request_for("从宝鸡出发，想看看自然风景", origin_city="宝鸡"))

    assert [item.city for item in response.recommendations] == ["昆明", "乌鲁木齐", "大理"]


def test_merged_context_revalidates_dates_against_inferred_days() -> None:
    agent = build_agent()
    context = RecommendationContext(
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
    )

    merged = agent._merge_inferred_context(context, {"travel_days": 7})

    assert merged.travel_days == 2
