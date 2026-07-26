"""Recommender behaviour: range constraints, exclusions and short-path routing."""

from __future__ import annotations

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
)
from app.services.destination_feasibility_service import (
    get_destination_feasibility_service,
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


def cities(response) -> list[str]:
    return [item.city for item in response.recommendations]


def test_nearby_request_stays_inside_the_short_trip_circle() -> None:
    """"附近的城市" must not be answered with a cross-country destination."""
    response = chat(
        build_agent(),
        "周末从山西太原出发，想去附近的城市避个暑，两个年轻人，预算3000。",
        origin_city="太原",
        travel_days=2,
    )
    nearby = {
        get_destination_feasibility_service().normalize_location_for_matching(city)
        for city in get_destination_feasibility_service().nearby_destinations("太原")
    }
    assert response.recommendations
    for item in response.recommendations:
        normalized = get_destination_feasibility_service().normalize_location_for_matching(
            item.city
        )
        assert normalized in nearby, f"{item.city} is outside the short-trip circle"


def test_excluded_city_never_appears_in_recommendations() -> None:
    response = chat(
        build_agent(),
        "从太原出发想去附近避暑，但不想去大同",
        origin_city="太原",
        travel_days=2,
    )
    assert response.recommendations
    assert "大同" not in cities(response)


def test_exclusion_is_carried_into_the_planner_handoff() -> None:
    response = chat(
        build_agent(),
        "从太原出发想去附近避暑，不要海边",
        origin_city="太原",
        travel_days=2,
    )
    free_text = response.recommendations[0].form_patch.free_text_input
    assert "【排除】" in free_text
    assert "海边" in free_text
    assert "【范围】仅短途/周边可达" in free_text


def test_short_path_skips_the_model_for_a_decided_nearby_weekend() -> None:
    """A resolvable weekend request must not spend an LLM round-trip."""
    agent = build_agent()
    calls: list[str] = []
    agent._generate_candidate_seeds = lambda request: calls.append("llm") or []
    response = chat(
        agent,
        "周末从太原出发想去附近避暑，两个人，预算3000",
        origin_city="太原",
        travel_days=2,
    )
    assert calls == []
    assert response.recommendations


def test_open_ended_request_still_uses_the_model_path() -> None:
    agent = build_agent()
    calls: list[str] = []

    def seeds(request):
        calls.append("llm")
        return [{"city": "南京", "reason": "文化密度高", "suggested_days": 3}]

    agent._generate_candidate_seeds = seeds
    chat(agent, "想找个有历史文化的地方玩三天", travel_days=3)
    assert calls == ["llm"]


def test_friday_card_is_not_counted_as_a_destination_direction() -> None:
    response = chat(
        build_agent(),
        "这个周末从太原出去避暑",
        origin_city="太原",
        travel_days=2,
    )
    friday = [
        item
        for item in response.recommendations
        if item.schedule_option == "friday_early"
    ]
    assert len(friday) == 1
    assert "三个方向" in response.reply


def test_each_city_is_looked_up_once_per_turn() -> None:
    """The Friday card must not re-search the city it is built from."""
    agent = build_agent()
    poi_calls: list[str] = []
    weather_calls: list[str] = []
    agent._search_city_highlights = lambda city, prefs: poi_calls.append(city) or []
    agent._weather_summary = lambda city: weather_calls.append(city) or None

    response = chat(agent, "这个周末从太原出去避暑", origin_city="太原", travel_days=2)

    assert any(
        item.schedule_option == "friday_early" for item in response.recommendations
    )
    assert len(poi_calls) == len(set(poi_calls))
    assert len(weather_calls) == len(set(weather_calls))


def test_audit_only_overwrite_is_not_reported_as_a_conflict() -> None:
    """"山西太原" vs form "太原" is the same origin, not a conflict banner."""
    response = chat(
        build_agent(),
        "周末从山西太原出发，想去附近的城市避个暑，两个年轻人，预算3000。",
        origin_city="太原",
        travel_days=2,
    )
    assert "需求冲突" not in response.reply
