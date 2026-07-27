"""Shared deterministic day-duration accounting for planning and quality."""

from __future__ import annotations

from dataclasses import dataclass

from ..models.schemas import DayPlan, TripRequest
from .destination_feasibility_service import get_destination_feasibility_service
from .trip_pacing_contract import prefers_gentle_pacing


INTERCITY_LEG_MINUTES = 240
HOTEL_TRANSFER_MINUTES = 60


@dataclass(frozen=True)
class DayScheduleBreakdown:
    visit_minutes: float
    route_minutes: float
    meal_and_rest_minutes: int
    hotel_transfer_minutes: int
    outbound_intercity_minutes: int
    return_intercity_minutes: int
    total_minutes: float
    overload_limit: int
    impossible_limit: int

    @property
    def intercity_minutes(self) -> int:
        return self.outbound_intercity_minutes + self.return_intercity_minutes


def calculate_day_schedule(
    request: TripRequest,
    day: DayPlan,
    day_index: int,
    total_days: int,
) -> DayScheduleBreakdown:
    """Count each activity once, including both legs on a one-day round trip."""
    attractions = day.attractions or []
    expected_routes = max(0, len(attractions) - 1)
    visit_minutes = float(
        sum(max(0, int(item.visit_duration or 0)) for item in attractions)
    )
    route_minutes = sum(
        max(0, int(route.duration or 0)) / 60
        for route in (day.routes or [])[:expected_routes]
    )

    feasibility = get_destination_feasibility_service()
    cross_city = bool(
        request.origin_city
        and feasibility.normalize_city(request.origin_city)
        != feasibility.normalize_city(request.city)
    )
    outbound = INTERCITY_LEG_MINUTES if cross_city and day_index == 0 else 0
    return_leg = (
        INTERCITY_LEG_MINUTES
        if cross_city and day_index == max(0, total_days - 1)
        else 0
    )
    relaxed = prefers_gentle_pacing(request)
    meal_and_rest = 150 if relaxed else 120
    hotel_transfer = (
        HOTEL_TRANSFER_MINUTES
        if day.hotel is not None and bool(attractions)
        else 0
    )
    total = (
        visit_minutes
        + route_minutes
        + meal_and_rest
        + hotel_transfer
        + outbound
        + return_leg
    )
    return DayScheduleBreakdown(
        visit_minutes=visit_minutes,
        route_minutes=route_minutes,
        meal_and_rest_minutes=meal_and_rest,
        hotel_transfer_minutes=hotel_transfer,
        outbound_intercity_minutes=outbound,
        return_intercity_minutes=return_leg,
        total_minutes=total,
        overload_limit=480 if relaxed else 600,
        impossible_limit=660 if relaxed else 840,
    )
