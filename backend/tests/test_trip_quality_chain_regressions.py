from __future__ import annotations

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import (
    Attraction,
    DayPlan,
    Location,
    Meal,
    RouteSegment,
    TripPlan,
    TripRequest,
)
from app.services.destination_feasibility_service import poi_destination_status
from app.services.transport_budget_service import QuoteResult, TransportBudgetService
from app.services.trip_schedule_service import calculate_day_schedule


def _request(*, days: int = 2) -> TripRequest:
    return TripRequest(
        origin_city="山西太原",
        city="忻州",
        start_date="2030-07-20",
        end_date="2030-07-21" if days == 2 else "2030-07-20",
        travel_days=days,
        travelers=2,
        budget=3000,
        transportation="公共交通",
        intercity_transportation="高铁",
        accommodation="经济型酒店",
        preferences=[],
        free_text_input="周末从山西太原出发，想去附近的城市避个暑，两个年轻人，预算3000。",
    )


def _attraction(name: str, minutes: int = 120) -> Attraction:
    return Attraction(
        name=name,
        address="测试地址",
        location=Location(longitude=113.5, latitude=39.0),
        visit_duration=minutes,
        description="测试",
        category="景点",
        poi_id=f"poi-{name}",
        coordinate_source="amap_poi",
    )


def _day(index: int, attractions: list[Attraction]) -> DayPlan:
    return DayPlan(
        date=f"2030-07-{20 + index:02d}",
        day_index=index,
        description="测试行程",
        transportation="公共交通",
        accommodation="经济型酒店",
        attractions=attractions,
        meals=[
            Meal(type="breakfast", name="早餐", estimated_cost=20),
            Meal(type="lunch", name="午餐", estimated_cost=30),
            Meal(type="dinner", name="晚餐", estimated_cost=40),
        ],
    )


def test_county_adcode_matches_its_prefecture_parent_without_name_exception() -> None:
    assert poi_destination_status(
        destination_city="忻州",
        adname="五台县",
        adcode="140922",
        address="台怀镇杨柏峪村某风景名胜区",
    ) == "matched"


def test_structured_cross_prefecture_poi_is_still_rejected() -> None:
    assert poi_destination_status(
        destination_city="忻州",
        cityname="太原市",
        citycode="0351",
        adname="迎泽区",
        adcode="140106",
        address="山西省太原市迎泽区",
    ) == "mismatched"
    assert poi_destination_status(
        destination_city="忻州",
        adcode="140106",
        address="迎泽区测试路",
    ) == "mismatched"


def test_unknown_county_address_is_not_misclassified_as_cross_city() -> None:
    assert poi_destination_status(
        destination_city="忻州",
        address="台怀镇杨柏峪村某风景名胜区",
    ) == "unknown"


def test_two_day_gentle_intercity_time_is_counted_once_per_leg() -> None:
    request = _request()
    days = [_day(0, [_attraction("甲")]), _day(1, [_attraction("乙")])]

    first = calculate_day_schedule(request, days[0], 0, 2)
    last = calculate_day_schedule(request, days[1], 1, 2)

    assert first.outbound_intercity_minutes == 240
    assert first.return_intercity_minutes == 0
    assert last.outbound_intercity_minutes == 0
    assert last.return_intercity_minutes == 240
    assert first.intercity_minutes + last.intercity_minutes == 480
    assert first.meal_and_rest_minutes == 150


def test_one_day_roundtrip_counts_two_distinct_legs_not_duplicate_reserves() -> None:
    request = _request(days=1)
    day = _day(0, [_attraction("甲")])

    schedule = calculate_day_schedule(request, day, 0, 1)

    assert schedule.outbound_intercity_minutes == 240
    assert schedule.return_intercity_minutes == 240
    assert schedule.intercity_minutes == 480


def test_route_aware_planner_trims_blocking_two_day_schedule() -> None:
    request = _request()
    day0 = _day(0, [_attraction("甲", 120)])
    day1 = _day(1, [_attraction("乙", 180), _attraction("丙", 180)])
    day1.routes = [
        RouteSegment(
            from_name="乙",
            to_name="丙",
            route_type="driving",
            duration=3600,
        )
    ]
    plan = TripPlan(
        city="忻州",
        start_date=request.start_date,
        end_date=request.end_date,
        days=[day0, day1],
        overall_suggestions="测试",
    )
    before = calculate_day_schedule(request, day1, 1, 2)
    assert before.total_minutes > before.impossible_limit

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner._trim_impossible_day_schedules(request, plan)

    after = calculate_day_schedule(request, plan.days[1], 1, 2)
    assert len(plan.days[1].attractions) == 1
    assert plan.days[1].routes == []
    assert after.total_minutes <= after.impossible_limit
    assert "自动精简" in plan.overall_suggestions


def test_unknown_ticket_is_pending_and_not_confirmed_free_or_zero_cost() -> None:
    request = _request()
    unknown = _attraction("待核实景点")
    confirmed_free = _attraction("确认免费景点")
    confirmed_free.ticket_price_status = "free"
    paid = _attraction("已核价景点")
    paid.ticket_price = 50
    paid.ticket_price_status = "verified"
    plan = TripPlan(
        city="忻州",
        start_date=request.start_date,
        end_date=request.end_date,
        days=[_day(0, [unknown, confirmed_free, paid]), _day(1, [_attraction("另一个待核实景点")])],
        overall_suggestions="测试",
    )

    service = TransportBudgetService.__new__(TransportBudgetService)
    service._estimate_hotel = lambda *_args: QuoteResult(total_price=200, unit_price=200)
    service._estimate_intercity_transport = lambda *_args: QuoteResult(
        total_price=300,
        unit_price=150,
    )
    budget = service.estimate_budget(request, plan)

    assert budget.total_attractions == 100
    assert budget.known_total == budget.total
    assert budget.pending_ticket_items == ["待核实景点", "另一个待核实景点"]
    assert "确认免费景点" not in budget.pending_ticket_items
    assert any("未知票价未计入已知费用" in note for note in budget.budget_notes)
