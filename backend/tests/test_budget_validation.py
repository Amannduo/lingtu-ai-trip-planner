from __future__ import annotations

import os
import threading
from types import SimpleNamespace

from app.models.schemas import (
    Attraction,
    Budget,
    DayPlan,
    Hotel,
    Location,
    Meal,
    TripPlan,
    TripRequest,
)
from app.services.transport_budget_service import QuoteResult, TransportBudgetService
from app.services.trip_plan_quality_service import TripPlanQualityService


def _request() -> TripRequest:
    return TripRequest(
        origin_city="西安",
        city="成都",
        start_date="2030-01-01",
        end_date="2030-01-02",
        travel_days=2,
        travelers=2,
        transportation="公共交通",
        intercity_transportation="高铁",
        accommodation="经济型酒店",
        preferences=[],
    )


def _plan() -> TripPlan:
    days = []
    for index, value in enumerate(("2030-01-01", "2030-01-02")):
        days.append(
            DayPlan(
                date=value,
                day_index=index,
                description="城市漫游",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[
                    Attraction(
                        name=f"测试博物馆{index}",
                        address="成都市",
                        location=Location(
                            longitude=104.06 + index * 0.01,
                            latitude=30.67,
                        ),
                        visit_duration=120,
                        description="测试",
                        category="博物馆",
                        poi_id=f"poi-{index}",
                        coordinate_source="amap_poi",
                    )
                ],
                meals=[
                    Meal(type="breakfast", name="早餐"),
                    Meal(type="lunch", name="午餐"),
                    Meal(type="dinner", name="晚餐"),
                ],
            )
        )
    return TripPlan(
        city="成都",
        start_date="2030-01-01",
        end_date="2030-01-02",
        days=days,
        overall_suggestions="测试",
    )


def test_high_speed_request_selects_d_train_instead_of_cheapest_k_train() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    data = {
        "data": {
            "itemList": [
                {
                    "price": "128.50",
                    "journeys": [{"segments": [{"marketingTransportNo": "K291", "marketingTransportName": "普快"}]}],
                },
                {
                    "price": "211.00",
                    "journeys": [{"segments": [{"marketingTransportNo": "D973", "marketingTransportName": "动车"}]}],
                },
            ]
        }
    }

    selected = service._pick_ticket_item(data, "train", "高铁")

    assert selected is not None
    segments = service._ticket_segments(selected)
    assert segments[0]["marketingTransportNo"] == "D973"


def test_low_price_hostel_quote_is_rejected_for_economy_hotel() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    service.flyai_enabled = True
    service._run_flyai = lambda _args: {
        "data": {
            "itemList": [
                {"name": "逆时光青年旅舍", "price": "25", "star": "经济型"}
            ]
        }
    }

    quote = service._estimate_hotel(_request(), _plan(), 1, 1)

    assert quote.source == "heuristic_hotel"
    assert quote.unit_price == 220
    assert quote.total_price == 220


def test_quality_gate_rejects_low_hotel_price_and_train_mode_mismatch() -> None:
    request = _request()
    plan = _plan()
    plan.budget = Budget(
        total_hotels=25,
        total_transportation=256,
        total=281,
        hotel_nights=1,
        hotel_rooms=1,
        hotel_unit_price=25,
        intercity_transportation=256,
        transport_unit_price=256,
        transport_reference="西安->成都: 普快 K291 硬座",
    )

    quality = TripPlanQualityService().evaluate(request, plan)
    codes = {issue.code for issue in quality.issues}

    assert "HOTEL_PRICE_IMPLAUSIBLY_LOW" in codes
    assert "TRANSPORT_MODE_MISMATCH" in codes
    assert quality.score < 100

def test_hotel_and_intercity_quotes_execute_concurrently() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    barrier = threading.Barrier(2)
    thread_ids = set()

    def hotel_quote(*_args):
        thread_ids.add(threading.get_ident())
        barrier.wait(timeout=1)
        return QuoteResult(unit_price=220, total_price=220, source="heuristic_hotel")

    def transport_quote(*_args):
        thread_ids.add(threading.get_ident())
        barrier.wait(timeout=1)
        return QuoteResult(unit_price=600, total_price=1200, source="heuristic_transport")

    service._estimate_hotel = hotel_quote
    service._estimate_intercity_transport = transport_quote

    budget = service.estimate_budget(_request(), _plan())

    assert len(thread_ids) == 2
    assert budget.total_hotels == 220
    assert budget.intercity_transportation == 1200

def test_high_speed_filter_rejects_k_train_even_when_name_claims_high_speed() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    disguised_k = {
        "price": "128.50",
        "journeys": [
            {
                "segments": [
                    {
                        "marketingTransportNo": "K291",
                        "marketingTransportName": "高铁",
                    }
                ]
            }
        ],
    }
    mixed_transfer = {
        "price": "180.00",
        "journeys": [
            {
                "segments": [
                    {"marketingTransportNo": "D100", "marketingTransportName": "动车"},
                    {"marketingTransportNo": "Z20", "marketingTransportName": "直达"},
                ]
            }
        ],
    }
    valid = {
        "price": "211.00",
        "journeys": [
            {
                "segments": [
                    {"marketingTransportNo": "D973", "marketingTransportName": "动车"}
                ]
            }
        ],
    }
    data = {"data": {"itemList": [disguised_k, mixed_transfer, valid]}}

    selected = service._pick_ticket_item(data, "train", "高铁")

    assert selected is valid


def test_hotel_selector_skips_hostel_and_uses_next_compatible_room() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    service.flyai_enabled = True
    service._run_flyai = lambda _args: {
        "data": {
            "itemList": [
                {"name": "便宜青年旅舍床位", "price": "25", "star": "经济型"},
                {"name": "安心经济型酒店", "price": "168", "star": "经济型"},
            ]
        }
    }

    quote = service._estimate_hotel(_request(), _plan(), 1, 1)

    assert quote.source == "flyai_hotel"
    assert quote.unit_price == 168
    assert quote.total_price == 168
    assert "安心经济型酒店" in (quote.reference or "")


def test_explicit_high_speed_mode_never_falls_through_to_flight() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    calls = []

    def quote(_request, ticket_type):
        calls.append(ticket_type)
        return QuoteResult()

    service._estimate_roundtrip_ticket = quote

    result = service._estimate_intercity_transport(_request())

    assert calls == ["train"]
    assert result.source == "heuristic_transport"
    assert result.unit_price == 600
    assert result.total_price == 1200
    assert "高铁/动车" in (result.reference or "")
    assert "非实时班次" in (result.reference or "")


def test_budget_quality_checks_traveler_multipliers_and_breakdowns() -> None:
    request = _request()
    plan = _plan()
    for day in plan.days:
        for meal in day.meals:
            meal.estimated_cost = 20
        day.attractions[0].ticket_price = 50
    plan.budget = Budget(
        total_attractions=100,
        total_hotels=220,
        total_meals=120,
        total_transportation=200,
        total=640,
        hotel_nights=1,
        hotel_rooms=1,
        hotel_unit_price=220,
        intercity_transportation=200,
        local_transportation=50,
        transport_unit_price=200,
        transport_reference="西安->成都: D973; 成都->西安: D974",
    )

    quality = TripPlanQualityService().evaluate(request, plan)
    codes = {issue.code for issue in quality.issues}

    assert "BUDGET_MEAL_MULTIPLIER" in codes
    assert "BUDGET_TRANSPORT_BREAKDOWN_MISMATCH" in codes
    assert "BUDGET_TRANSPORT_MULTIPLIER" in codes
    assert quality.score < 100


def test_flyai_subprocess_receives_only_allowlisted_environment(monkeypatch) -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    service.flyai_enabled = True
    service.flyai_command = ["fake-flyai"]
    service.settings = SimpleNamespace(
        flyai_api_key="flyai-test-key",
        flyai_timeout=1,
    )
    monkeypatch.setenv("ZHIPU_SEARCH_API_KEY", "sentinel-zhipu-secret")
    monkeypatch.setenv("LLM_API_KEY", "sentinel-llm-secret")
    captured = {}

    class Result:
        returncode = 0
        stdout = '{"data": {"itemList": []}}'

    def fake_run(_command, **kwargs):
        captured.update(kwargs)
        assert os.path.isdir(kwargs["cwd"])
        return Result()

    monkeypatch.setattr(
        "app.services.transport_budget_service.subprocess.run",
        fake_run,
    )

    result = service._run_flyai(["search-train"])

    assert result == {"data": {"itemList": []}}
    assert captured["env"]["FLYAI_API_KEY"] == "flyai-test-key"
    assert "ZHIPU_SEARCH_API_KEY" not in captured["env"]
    assert "LLM_API_KEY" not in captured["env"]
    assert "sentinel-zhipu-secret" not in repr(captured)
    assert "sentinel-llm-secret" not in repr(captured)


def test_missing_flyai_key_disables_cli_even_if_legacy_flag_is_true(monkeypatch) -> None:
    settings = SimpleNamespace(
        flyai_enabled=True,
        flyai_api_key="",
        flyai_cli_command="npx --yes @fly-ai/flyai-cli",
        flyai_timeout=1,
    )
    monkeypatch.setattr(
        "app.services.transport_budget_service.get_settings",
        lambda: settings,
    )

    service = TransportBudgetService()

    assert service.flyai_enabled is False

def test_zero_ticket_prices_are_transparent_and_prevent_perfect_score() -> None:
    request = _request()
    plan = _plan()
    service = TransportBudgetService.__new__(TransportBudgetService)
    service._estimate_hotel = lambda *_args: QuoteResult(
        unit_price=220,
        total_price=220,
        source="heuristic_hotel",
    )
    service._estimate_intercity_transport = lambda *_args: QuoteResult(
        unit_price=600,
        total_price=1200,
        reference="高铁/动车兜底估算（非实时班次）",
        source="heuristic_transport",
    )

    plan.budget = service.estimate_budget(request, plan)
    quality = TripPlanQualityService().evaluate(request, plan)
    ticket_issue = next(
        issue
        for issue in quality.issues
        if issue.code == "TICKET_PRICE_UNAVAILABLE"
    )

    notes = " ".join(plan.budget.budget_notes)
    assert "已知费用合计" in notes
    assert "未知票价未计入已知费用" in notes
    assert "不代表景点免费" in notes
    assert "门票价格待核实" in ticket_issue.message
    assert quality.score <= 96

def test_legacy_flyai_package_is_pinned_without_rewriting_custom_commands(
    monkeypatch,
) -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    monkeypatch.setattr(
        "app.services.transport_budget_service.shutil.which",
        lambda _command: None,
    )

    legacy = service._split_command("npx --yes @fly-ai/flyai-cli")
    pinned = service._split_command("npx --yes @fly-ai/flyai-cli@1.0.16")
    custom = service._split_command("custom-budget-cli --json")

    assert legacy == ["npx", "--yes", "@fly-ai/flyai-cli@1.0.16"]
    assert pinned == legacy
    assert custom == ["custom-budget-cli", "--json"]

def test_integer_budget_mismatches_cannot_hide_inside_twenty_yuan_tolerance() -> None:
    request = _request()
    plan = _plan()
    for day in plan.days:
        for meal in day.meals:
            meal.estimated_cost = 20
        day.attractions[0].ticket_price = 50
    plan.budget = Budget(
        total_attractions=180,
        total_meals=220,
        total=420,
    )

    quality = TripPlanQualityService().evaluate(request, plan)
    codes = {issue.code for issue in quality.issues}

    assert "BUDGET_SUM_MISMATCH" in codes
    assert "BUDGET_MEAL_MULTIPLIER" in codes
    assert "BUDGET_TICKET_MULTIPLIER" in codes
    assert quality.score < 100


def test_single_leg_high_speed_and_heuristic_quotes_are_not_fully_verified() -> None:
    request = _request()
    plan = _plan()
    plan.budget = Budget(
        total_hotels=220,
        total_meals=480,
        total_transportation=500,
        total=1200,
        hotel_nights=1,
        hotel_rooms=1,
        hotel_unit_price=220,
        intercity_transportation=400,
        local_transportation=100,
        transport_unit_price=200,
        transport_reference="西安->成都: D973 二等座",
    )

    single_leg = TripPlanQualityService().evaluate(request, plan)
    assert any(
        issue.code == "TRANSPORT_MODE_UNVERIFIED"
        for issue in single_leg.issues
    )

    fallback_plan = plan.model_copy(deep=True)
    fallback_plan.budget.budget_source = "城际交通兜底估算"
    fallback_plan.budget.hotel_reference = "经济型酒店 参考单晚 220 元"
    fallback_plan.budget.transport_reference = "飞机兜底估算（非实时班次）"
    fallback_request = request.model_copy(
        update={"intercity_transportation": "飞机"}
    )
    fallback = TripPlanQualityService().evaluate(fallback_request, fallback_plan)
    fallback_codes = {issue.code for issue in fallback.issues}

    assert "HOTEL_PRICE_UNVERIFIED" in fallback_codes
    assert "TRANSPORT_MODE_UNVERIFIED" in fallback_codes
    assert fallback.score < 100



def _flyai_train_item(
    origin_station: str,
    destination_station: str,
    departure: str,
    *,
    price: str = "211.00",
    train_no: str = "D100",
) -> dict:
    return {
        "price": price,
        "journeys": [
            {
                "segments": [
                    {
                        "departureStationName": origin_station,
                        "arrivalStationName": destination_station,
                        "departureDateTime": departure,
                        "marketingTransportNo": train_no,
                        "marketingTransportName": "动车",
                        "seatClassName": "二等座",
                    }
                ]
            }
        ],
    }


def test_ticket_selector_rejects_wrong_direction_date_and_stations() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    invalid_items = [
        _flyai_train_item("成都东站", "西安北站", "2030-01-01 08:00"),
        _flyai_train_item("西安北站", "成都东站", "2030-01-03 08:00"),
        _flyai_train_item("北京西站", "成都东站", "2030-01-01 08:00"),
    ]

    for item in invalid_items:
        selected = service._pick_ticket_item(
            {"data": {"itemList": [item]}},
            "train",
            "高铁",
            origin_city="西安",
            destination_city="成都",
            departure_date="2030-01-01",
        )
        assert selected is None


def test_masked_flyai_ticket_prices_are_rejected_as_unverified() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    service.flyai_enabled = True
    responses = iter(
        [
            {
                "data": {
                    "itemList": [
                        _flyai_train_item(
                            "西安北站",
                            "成都东站",
                            "2030-01-01 08:00",
                            price="2xx",
                            train_no="D100",
                        )
                    ]
                }
            },
            {
                "data": {
                    "itemList": [
                        _flyai_train_item(
                            "成都东站",
                            "西安北站",
                            "2030-01-02 09:00",
                            price="3xx",
                            train_no="D101",
                        )
                    ]
                }
            },
        ]
    )
    service._run_flyai = lambda _arguments: next(responses)

    quote = service._estimate_roundtrip_ticket(_request(), "train")

    assert quote.total_price == 0
    assert quote.source == ""
    assert quote.reference is None


def test_valid_flyai_ticket_reference_keeps_actual_stations_and_dates() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    service.flyai_enabled = True
    responses = iter(
        [
            {
                "data": {
                    "itemList": [
                        _flyai_train_item(
                            "西安北站",
                            "成都东站",
                            "2030-01-01 08:00",
                            price="211.00",
                            train_no="D100",
                        )
                    ]
                }
            },
            {
                "data": {
                    "itemList": [
                        _flyai_train_item(
                            "成都东站",
                            "西安北站",
                            "2030-01-02 09:00",
                            price="263.00",
                            train_no="D101",
                        )
                    ]
                }
            },
        ]
    )
    service._run_flyai = lambda _arguments: next(responses)

    quote = service._estimate_roundtrip_ticket(_request(), "train")

    assert quote.source == "flyai_train"
    assert quote.unit_price == 474
    assert quote.total_price == 948
    assert quote.reference is not None
    assert "西安北站->成都东站" in quote.reference
    assert "2030-01-01 08:00" in quote.reference
    assert "成都东站->西安北站" in quote.reference
    assert "2030-01-02 09:00" in quote.reference


def test_hotel_quote_for_different_hotel_is_not_applied_to_itinerary() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    service.flyai_enabled = True
    service._run_flyai = lambda _arguments: {
        "data": {
            "itemList": [
                {"name": "报价酒店B", "price": "168", "star": "经济型"}
            ]
        }
    }
    plan = _plan()
    plan.days[0].hotel = Hotel(
        name="行程酒店A",
        address="成都市中心酒店路1号",
        location=Location(longitude=104.07, latitude=30.67),
        estimated_cost=220,
        poi_id="hotel-a",
    )

    quote = service._estimate_hotel(_request(), plan, 1, 1)

    assert quote.source == "map_hotel_estimate"
    assert quote.unit_price == 220
    assert "行程酒店A" in (quote.reference or "")
    assert "报价酒店B" not in (quote.reference or "")

def test_alternative_masked_prices_are_skipped_before_hotel_selection() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    masked = {"name": "安心经济型酒店", "price": "2**", "star": "经济型"}
    valid = {"name": "安心经济型酒店", "price": "168", "star": "经济型"}

    selected = service._pick_hotel_item(
        {"data": {"itemList": [masked, valid]}},
        "经济型酒店",
        expected_names=["安心经济型酒店"],
    )

    assert service._parse_price("2??") == (0, True)
    assert service._parse_price("2＊＊") == (0, True)
    assert selected is valid


def test_hotel_switch_uses_weighted_nightly_prices_without_single_quote() -> None:
    request = _request().model_copy(
        update={
            "end_date": "2030-01-04",
            "travel_days": 4,
        }
    )
    plan = _plan().model_copy(
        update={"end_date": "2030-01-04"},
        deep=True,
    )
    for index, value in enumerate(("2030-01-03", "2030-01-04"), start=2):
        day = plan.days[0].model_copy(deep=True)
        day.date = value
        day.day_index = index
        plan.days.append(day)
    hotel_a = Hotel(
        name="酒店A",
        address="成都市A路",
        location=Location(longitude=104.07, latitude=30.67),
        estimated_cost=200,
        poi_id="hotel-a",
    )
    hotel_b = Hotel(
        name="酒店B",
        address="成都市B路",
        location=Location(longitude=104.08, latitude=30.67),
        estimated_cost=500,
        poi_id="hotel-b",
    )
    plan.days[0].hotel = hotel_a
    plan.days[1].hotel = hotel_a.model_copy(deep=True)
    plan.days[2].hotel = hotel_b
    service = TransportBudgetService.__new__(TransportBudgetService)
    service.flyai_enabled = True

    def unexpected_flyai_call(_arguments):
        raise AssertionError("hotel-switch itinerary must not use one FlyAI quote")

    service._run_flyai = unexpected_flyai_call

    quote = service._estimate_hotel(request, plan, 3, 1)

    assert quote.source == "map_hotel_estimate"
    assert quote.unit_price == 300
    assert quote.total_price == 900
    assert "酒店A、酒店B" in (quote.reference or "")


def test_transport_reference_requires_actual_leg_station_details() -> None:
    service = TripPlanQualityService()
    valid = (
        "西安->成都 [2030-01-01]: "
        "西安北站->成都东站 2030-01-01 08:00 动车 D100; "
        "成都->西安 [2030-01-02]: "
        "成都东站->西安北站 2030-01-02 09:00 动车 D101"
    )
    spoofed = (
        "西安->成都 [2030-01-01]: "
        "北京南站->上海虹桥站 2030-01-01 08:00 动车 D100; "
        "成都->西安 [2030-01-02]: "
        "上海虹桥站->北京南站 2030-01-02 09:00 动车 D101"
    )

    assert service._transport_reference_matches(_request(), valid) is True
    assert service._transport_reference_matches(_request(), spoofed) is False

def test_ticket_field_priority_is_independent_of_json_key_order() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    mapping = {
        "departureTime": "08:00",
        "departureDateTime": "2030-01-01 08:00",
    }

    actual = service._ticket_field_text(
        (mapping,),
        ("departureDateTime", "departureTime"),
    )

    assert actual == "2030-01-01 08:00"


def test_journey_level_ticket_evidence_is_validated_and_displayed() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    item = {
        "price": "211.00",
        "journeys": [
            {
                "departureCityName": "西安",
                "arrivalCityName": "成都",
                "departureDate": "2030-01-01",
                "segments": [
                    {
                        "marketingTransportNo": "D100",
                        "marketingTransportName": "动车",
                        "seatClassName": "二等座",
                    }
                ],
            }
        ],
    }

    assert service._ticket_item_matches_request(
        item,
        "西安",
        "成都",
        "2030-01-01",
    ) is True
    description = service._describe_ticket_item(item, "train")
    assert "西安->成都" in description
    assert "2030-01-01" in description
