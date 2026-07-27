"""Semantic preservation: range, exclusions, negation and machine-text isolation.

Each test here locks a defect that lost user meaning between the utterance and
the generated plan.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models.schemas import RecommendationContext, TripRequest
from app.services.destination_feasibility_service import (
    get_destination_feasibility_service,
)
from app.services.semantic_contract_service import (
    collect_semantic_hard_block_issues,
    extract_user_utterance,
    get_semantic_contract_service,
    has_affirmative_weekend,
    user_intent_text,
)

# A Sunday, so weekend inference lands on a stable next Saturday.
REFERENCE_DATE = date(2026, 7, 26)

MACHINE_BLOCK = "\n".join(
    [
        "【目的地】眉县",
        "【约束】轻松；每日主景点不超过2个",
        "【时段】周末Sat-Sun·2天",
        "【抵达建议】建议周五下午或傍晚出发，提前抵达后休息，周六再开始完整游玩。（可选，尚未确认）",
        "【同行】两个年轻人",
        "【原文】周末从太原出发，两个年轻人，预算3000，想去附近避暑。",
    ]
)


def extract(text: str):
    return get_semantic_contract_service().extract_from_text(
        text, reference_date=REFERENCE_DATE
    )


def weekend_trip_request(free_text: str) -> TripRequest:
    return TripRequest(
        origin_city="太原",
        city="眉县",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=2,
        budget=3000,
        transportation="公共交通",
        accommodation="经济型酒店",
        free_text_input=free_text,
    )


# ── machine-authored free text must not become user intent ────────────


def test_extract_user_utterance_keeps_only_user_words() -> None:
    assert extract_user_utterance(MACHINE_BLOCK) == (
        "周末从太原出发，两个年轻人，预算3000，想去附近避暑。"
    )


def test_extract_user_utterance_passes_through_plain_text() -> None:
    plain = "周末想去附近避暑"
    assert extract_user_utterance(plain) == plain


def test_extract_user_utterance_keeps_user_edits_below_the_block() -> None:
    text = f"{MACHINE_BLOCK}\n另外我对海鲜过敏"
    assert "另外我对海鲜过敏" in extract_user_utterance(text)
    assert "抵达建议" not in extract_user_utterance(text)


def test_advisory_friday_hint_does_not_expand_the_weekend() -> None:
    """The system's own "建议周五下午出发" must not read as a user decision."""
    contract = extract(user_intent_text(MACHINE_BLOCK))
    assert contract.travel_days.value == 2
    assert not contract.weekend_style.is_known() or (
        contract.weekend_style.value == "sat_sun"
    )
    assert not contract.departure_mode.is_known()


def test_default_weekend_card_submission_is_not_hard_blocked() -> None:
    """Picking the default weekend card and submitting must not 422."""
    assert collect_semantic_hard_block_issues(weekend_trip_request(MACHINE_BLOCK)) == []


def test_user_typed_friday_departure_still_expands_to_three_days() -> None:
    contract = extract("这个周末想出去，周五下午就出发。")
    assert contract.travel_days.value == 3
    assert contract.weekend_style.value == "fri_sun_optional"
    assert contract.departure_mode.value == "evening_before"


# ── negation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("这个周末想出去避暑", True),
        ("下周末去哪好", True),
        ("不要周末了", False),
        ("不想周末出门", False),
    ],
)
def test_weekend_intent_respects_negation(text: str, expected: bool) -> None:
    assert has_affirmative_weekend(text) is expected


def test_cancelling_the_weekend_switches_to_the_stated_date() -> None:
    contract = extract("不要周末了，改成9月15号出发")
    assert contract.start_date.value == "2026-09-15"
    assert contract.start_date.source == "user_explicit"
    assert contract.date_pattern.value == "explicit"
    assert not contract.weekend_style.is_known()
    assert not contract.early_arrival_hint.is_known()


def test_explicit_date_outranks_weekend_inference_in_one_message() -> None:
    contract = extract("这个周末有空，不过9月15号出发更合适")
    assert contract.start_date.value == "2026-09-15"


def test_negated_theme_does_not_become_a_preference() -> None:
    contract = extract("不要海边，想去看博物馆")
    assert contract.preferences.value == ["历史文化"]
    assert contract.excluded_themes.value == ["海边"]


def test_relaxed_phrasing_is_not_read_as_a_negation() -> None:
    """"不想太累" means relaxed pace, not "no 休闲"."""
    contract = extract("想去附近走走，不想太累")
    assert "休闲" in (contract.preferences.value or [])
    assert contract.pace.value == "轻松"


# ── range and exclusions ──────────────────────────────────────────────


def test_nearby_range_is_captured() -> None:
    contract = extract("从山西太原出发想去附近的城市避个暑。")
    assert contract.destination_scope.value == "nearby"
    assert contract.destination_scope.source == "user_explicit"


def test_far_range_is_captured() -> None:
    assert extract("想去远一点的地方玩三天").destination_scope.value == "far"


def test_contradictory_range_stays_pending_with_a_conflict() -> None:
    contract = extract("想去附近，又想去远一点的地方")
    assert contract.destination_scope.pending_confirmation is True
    assert contract.conflicts


def test_excluded_destination_is_recorded() -> None:
    contract = extract("从昆明附近选，但不想去昆明。")
    assert contract.excluded_destinations.value == ["昆明"]


def test_exclusions_survive_a_later_message() -> None:
    service = get_semantic_contract_service()
    first = extract("想去附近避暑，不要海边")
    second = extract("预算改成5000")
    merged = service.merge(first, second)
    assert merged.excluded_themes.value == ["海边"]
    assert merged.budget.value == 5000


def test_exclusions_accumulate_across_messages() -> None:
    service = get_semantic_contract_service()
    merged = service.merge(extract("不要海边"), extract("也不想去博物馆"))
    assert set(merged.excluded_themes.value) == {"海边", "博物馆"}


# ── origin normalization ──────────────────────────────────────────────


def test_province_prefixed_origin_matches_the_short_trip_graph() -> None:
    service = get_destination_feasibility_service()
    assert service.nearby_destinations("山西太原") == service.nearby_destinations("太原")
    assert service.nearby_destinations("太原")


def test_province_prefixed_utterance_does_not_diverge_from_the_form() -> None:
    request = TripRequest(
        origin_city="太原",
        city="大同",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=2,
        budget=3000,
        transportation="公共交通",
        accommodation="经济型酒店",
        free_text_input="周末从山西太原出发，想去附近的城市避个暑，两个年轻人，预算3000。",
    )
    assert collect_semantic_hard_block_issues(request) == []


def test_a_real_origin_divergence_is_still_blocked() -> None:
    request = TripRequest(
        origin_city="太原",
        city="大同",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=2,
        budget=3000,
        transportation="公共交通",
        accommodation="经济型酒店",
        free_text_input="周末从上海出发，想去附近的城市避个暑。",
    )
    codes = {issue["code"] for issue in collect_semantic_hard_block_issues(request)}
    assert "SEMANTIC_FORM_FREE_TEXT_DIVERGENCE" in codes


# ── the spec's canonical utterance ────────────────────────────────────


def test_canonical_taiyuan_weekend_request_is_fully_understood() -> None:
    contract = extract("周末从山西太原出发，想去附近的城市避个暑，两个年轻人，预算3000。")
    assert contract.origin_city.value == "山西太原"
    assert contract.travelers.value == 2
    assert contract.travelers.pending_confirmation is False
    assert contract.travel_party.value == "两个年轻人"
    assert contract.budget.value == 3000
    assert contract.travel_days.value == 2
    assert contract.destination_scope.value == "nearby"
    assert contract.pace.value == "轻松"
    # Dates stay inferred-and-pending: never presented as user-confirmed.
    assert contract.start_date.pending_confirmation is True
    assert contract.end_date.pending_confirmation is True
    assert "start_date" in contract.pending_fields


def test_form_values_do_not_resurrect_a_dropped_weekend() -> None:
    """A non-weekend follow-up must not inherit the previous weekend banner."""
    service = get_semantic_contract_service()
    form = RecommendationContext(
        origin_city="太原", travel_days=2, start_date="2026-08-01", end_date="2026-08-02"
    )
    merged = service.merge(
        service.contract_from_form(form), extract("不要周末了，9月15号出发")
    )
    assert not merged.weekend_style.is_known()
    assert merged.date_pattern.value == "explicit"
