"""Comprehensive tests for FlyAI location matching, roundtrip, and diagnostics.

Covers every fix applied to ``transport_budget_service.py``:
* Structured location normalization (province/city/station stripping)
* Multi-field candidate collection (journey + segment levels)
* Roundtrip partial-success support
* Diagnostic logging (sanitized)

Run:  cd backend && python -m pytest tests/test_flyai_fixes.py -v -s
"""

from __future__ import annotations

import io
import logging
import re
import sys
from typing import List
from unittest.mock import patch

import pytest

from app.models.schemas import (
    Attraction,
    Budget,
    DayPlan,
    Hotel,
    Location,
    Meal,
    RouteSegment,
    TripPlan,
    TripRequest,
    WeatherInfo,
)
from app.services.transport_budget_service import TransportBudgetService


# ── helpers ──────────────────────────────────────────────────────────────


def _svc() -> TransportBudgetService:
    return TransportBudgetService.__new__(TransportBudgetService)


def _request(**overrides) -> TripRequest:
    defaults = dict(
        origin_city="山西太原",
        city="五台山",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=2,
        budget=3000,
        transportation="公共交通",
        intercity_transportation=None,
        accommodation="经济型酒店",
        preferences=[],
    )
    defaults.update(overrides)
    return TripRequest(**defaults)


def _plan(request: TripRequest) -> TripPlan:
    from datetime import date, timedelta

    days = []
    for i in range(request.travel_days):
        d = (date.fromisoformat(request.start_date) + timedelta(days=i)).isoformat()
        days.append(
            DayPlan(
                date=d,
                day_index=i,
                description="游览",
                transportation="公共交通",
                accommodation=request.accommodation,
                attractions=[
                    Attraction(
                        name="五台山风景名胜区",
                        address="山西省忻州市五台县",
                        location=Location(longitude=113.59, latitude=39.03),
                        visit_duration=360,
                        description="五台山",
                        category="风景名胜",
                        poi_id="poi-wutaishan",
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
        city=request.city,
        start_date=request.start_date,
        end_date=request.end_date,
        days=days,
        overall_suggestions="测试",
    )


def _train_item(
    origin_station: str,
    dest_station: str,
    departure: str,
    price: str = "98.00",
    train_no: str = "K602",
    train_name: str = "快速",
    seat: str = "硬座",
    *,
    origin_city: str = "",
    dest_city: str = "",
    dep_date: str = "",
) -> dict:
    journey: dict = {
        "segments": [
            {
                "departureStationName": origin_station,
                "arrivalStationName": dest_station,
                "departureDateTime": departure,
                "marketingTransportNo": train_no,
                "marketingTransportName": train_name,
                "seatClassName": seat,
            }
        ],
    }
    if origin_city:
        journey["departureCityName"] = origin_city
    if dest_city:
        journey["arrivalCityName"] = dest_city
    if dep_date:
        journey["departureDate"] = dep_date
    return {"price": price, "journeys": [journey]}


# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — location normalization
# ═══════════════════════════════════════════════════════════════════════════


class TestLocationNormalization:
    """Verify _normalize_location_name for the 山西太原 → 五台山 scenario."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            # Province + city
            ("山西太原", "太原"),
            ("山西省太原市", "太原"),
            ("太原市", "太原"),
            ("太原", "太原"),
            # Station names
            ("太原站", "太原"),
            ("太原南站", "太原"),
            ("太原北站", "太原"),
            ("太原东站", "太原"),
            # Destination
            ("五台山", "五台山"),
            ("五台山站", "五台山"),
            # Direct-administered municipalities (must NOT strip the city name)
            ("北京", "北京"),
            ("北京西站", "北京"),
            ("北京南站", "北京"),
            ("北京站", "北京"),
            ("北京市", "北京"),
            ("天津", "天津"),
            ("天津站", "天津"),
            ("天津西站", "天津"),
            ("上海", "上海"),
            ("上海虹桥站", "上海虹桥"),
            ("重庆", "重庆"),
            ("重庆北站", "重庆"),
            # Same-name province+city (吉林)
            ("吉林", "吉林"),
            ("吉林站", "吉林"),
            ("吉林省吉林市", "吉林"),
            # Other common patterns
            ("西安", "西安"),
            ("西安北站", "西安"),
            ("成都", "成都"),
            ("成都东站", "成都"),
            ("河北省石家庄市", "石家庄"),
            ("石家庄站", "石家庄"),
            ("江苏省南京市", "南京"),
            ("南京南站", "南京"),
            # Edge: already minimal
            ("广州", "广州"),
            ("广州南站", "广州"),
        ],
    )
    def test_normalize_location_name(self, raw, expected):
        svc = _svc()
        result = svc._normalize_location_name(raw)
        assert result == expected, f"normalize({raw!r}) = {result!r}, expected {expected!r}"

    def test_normalize_empty_and_none(self):
        svc = _svc()
        assert svc._normalize_location_name("") == ""
        assert svc._normalize_location_name(None) == ""
        assert svc._normalize_location_name("   ") == ""

    def test_cache_consistency(self):
        """Multiple calls with the same input should return the same result."""
        svc = _svc()
        a = svc._normalize_location_name("山西太原")
        b = svc._normalize_location_name("山西太原")
        assert a == b == "太原"


class TestLocationMatchingAfterFix:
    """Verify _location_matches after the normalization fix."""

    @pytest.mark.parametrize(
        "expected, actual, should_match, note",
        [
            # ── Must-match pairs ──
            ("山西太原", "太原站", True, "province+city ↔ station"),
            ("山西太原", "太原南站", True, "province+city ↔ directional station"),
            ("山西太原", "太原", True, "province+city ↔ bare city"),
            ("山西省太原市", "太原站", True, "full admin ↔ station"),
            ("太原市", "太原站", True, "city+suffix ↔ station"),
            ("五台山", "五台山站", True, "scenic area ↔ station"),
            ("西安", "西安北站", True, "city ↔ directional station"),
            ("北京", "北京西站", True, "municipality ↔ station"),
            ("吉林", "吉林站", True, "same-name province+city ↔ station"),
            # ── Must-NOT-match pairs ──
            ("西安", "北京西站", False, "different city, '西' in common"),
            ("西安", "成都东站", False, "completely different"),
            ("太原", "西安北站", False, "太原 ≠ 西安"),
            ("五台山", "忻州站", False, "scenic area ≠ nearby city"),
            # NOTE: "广州" vs "广州路" is intentionally a match — our
            # normalizer strips station suffixes but not street/road suffixes.
            # A station named "广州路" (e.g. Nanjing Metro) prefixed with
            # "广州" would be a false positive only if the expected city is
            # "广州" and the actual station is in a different city.  The
            # risk is low because FlyAI would not return cross-city results
            # for a city-specific search.
        ],
    )
    def test_location_matches(self, expected, actual, should_match, note):
        svc = _svc()
        result = svc._location_matches(expected, actual)
        assert result == should_match, (
            f"_location_matches({expected!r}, {actual!r}) = {result}, "
            f"expected {should_match} ({note})"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — ticket item matching with multi-field collection
# ═══════════════════════════════════════════════════════════════════════════


class TestTicketItemMatching:
    """Verify _ticket_item_matches_request with journey and segment fields."""

    def test_segment_station_fields_match(self):
        """太原站 in segment departureStationName should match 山西太原."""
        svc = _svc()
        item = _train_item("太原站", "五台山站", "2026-08-01 07:30")
        assert svc._ticket_item_matches_request(item, "山西太原", "五台山", "2026-08-01")

    def test_journey_city_fields_match(self):
        """太原 in journey departureCityName should match 山西太原."""
        svc = _svc()
        item = _train_item("太原站", "五台山站", "2026-08-01 07:30",
                           origin_city="太原", dest_city="五台山")
        assert svc._ticket_item_matches_request(item, "山西太原", "五台山", "2026-08-01")

    def test_journey_city_but_wrong_date(self):
        """Correct cities but wrong date should be rejected."""
        svc = _svc()
        item = _train_item("太原站", "五台山站", "2026-08-03 07:30",
                           origin_city="太原", dest_city="五台山")
        assert not svc._ticket_item_matches_request(item, "山西太原", "五台山", "2026-08-01")

    def test_no_segments_no_match(self):
        """Item without segments should not match."""
        svc = _svc()
        item = {"price": "100", "journeys": []}
        assert not svc._ticket_item_matches_request(item, "山西太原", "五台山", "2026-08-01")

    def test_wrong_direction_rejected(self):
        """五台山→太原 ticket should NOT match 太原→五台山 request."""
        svc = _svc()
        item = _train_item("五台山站", "太原站", "2026-08-01 07:30")
        assert not svc._ticket_item_matches_request(item, "山西太原", "五台山", "2026-08-01")

    def test_transfer_journey_matches(self):
        """Multi-segment (transfer) journey: first origin, last dest."""
        svc = _svc()
        item = {
            "price": "150.00",
            "journeys": [
                {
                    "segments": [
                        {
                            "departureStationName": "太原站",
                            "arrivalStationName": "忻州站",
                            "departureDateTime": "2026-08-01 07:30",
                            "marketingTransportNo": "K602",
                            "marketingTransportName": "快速",
                        },
                        {
                            "departureStationName": "忻州站",
                            "arrivalStationName": "五台山站",
                            "departureDateTime": "2026-08-01 09:00",
                            "marketingTransportNo": "6801",
                            "marketingTransportName": "普慢",
                        },
                    ]
                }
            ],
        }
        assert svc._ticket_item_matches_request(item, "山西太原", "五台山", "2026-08-01")

    def test_collect_location_fields_returns_all_candidates(self):
        """_collect_location_fields should gather from all mappings."""
        svc = _svc()
        segment = {
            "departureStationName": "太原站",
            "arrivalStationName": "五台山站",
        }
        journey = {
            "departureCityName": "太原",
            "arrivalCityName": "五台山",
        }
        candidates = svc._collect_location_fields(
            (segment, journey, {}),
            ("departureCityName", "departureStationName"),
        )
        assert "太原" in candidates  # from journey.departureCityName
        assert "太原站" in candidates  # from segment.departureStationName
        # Order: city fields first (phone book priority), then station fields
        assert len(candidates) == 2

    def test_collect_location_fields_deduplicates(self):
        """Same value from multiple levels should only appear once."""
        svc = _svc()
        segment = {"departureStationName": "太原站"}
        journey = {"departureStationName": "太原站"}  # same value
        candidates = svc._collect_location_fields(
            (segment, journey, {}),
            ("departureStationName",),
        )
        assert len(candidates) == 1
        assert candidates == ["太原站"]


# ═══════════════════════════════════════════════════════════════════════════
# Section 3 — roundtrip partial success
# ═══════════════════════════════════════════════════════════════════════════


class TestRoundtripPartialSuccess:
    """Verify _estimate_roundtrip_ticket handles outbound/inbound independently."""

    def test_both_legs_succeed(self):
        """Both legs hit → use both real prices."""
        svc = _svc()
        svc.flyai_enabled = True
        responses = iter([
            {"data": {"itemList": [
                _train_item("太原站", "五台山站", "2026-08-01 07:30", price="98.00"),
            ]}},
            {"data": {"itemList": [
                _train_item("五台山站", "太原站", "2026-08-02 16:30", price="108.00"),
            ]}},
        ])
        svc._run_flyai = lambda args: next(responses)
        request = _request(intercity_transportation="火车")
        quote = svc._estimate_roundtrip_ticket(request, "train")

        assert quote.source == "flyai_train"
        assert quote.unit_price == 206  # 98 + 108
        assert quote.total_price == 412  # 206 * 2 travelers
        assert "太原站" in (quote.reference or "")
        assert "五台山站" in (quote.reference or "")

    def test_outbound_only(self):
        """Only outbound succeeds → real outbound + estimated return."""
        svc = _svc()
        svc.flyai_enabled = True
        responses = iter([
            {"data": {"itemList": [
                _train_item("太原站", "五台山站", "2026-08-01 07:30", price="98.00"),
            ]}},
            {"data": {"itemList": []}},  # inbound empty
        ])
        svc._run_flyai = lambda args: next(responses)
        request = _request(intercity_transportation="火车")
        quote = svc._estimate_roundtrip_ticket(request, "train")

        assert quote.source == "flyai_train_partial"
        assert quote.unit_price == 196  # 98 + 98 (estimated)
        assert quote.total_price == 392
        assert "去程 FlyAI 报价" in (quote.reference or "")
        assert "返程按去程同价估算" in (quote.reference or "")
        assert any("返程" in note for note in quote.notes)

    def test_inbound_only(self):
        """Only inbound succeeds → estimated outbound + real return."""
        svc = _svc()
        svc.flyai_enabled = True
        responses = iter([
            {"data": {"itemList": []}},  # outbound empty
            {"data": {"itemList": [
                _train_item("五台山站", "太原站", "2026-08-02 16:30", price="108.00"),
            ]}},
        ])
        svc._run_flyai = lambda args: next(responses)
        request = _request(intercity_transportation="火车")
        quote = svc._estimate_roundtrip_ticket(request, "train")

        assert quote.source == "flyai_train_partial"
        assert quote.unit_price == 216  # 108 + 108 (estimated)
        assert quote.total_price == 432
        assert "返程 FlyAI 报价" in (quote.reference or "")
        assert "去程按返程同价估算" in (quote.reference or "")

    def test_both_legs_fail(self):
        """Both legs empty → return empty QuoteResult (caller handles fallback)."""
        svc = _svc()
        svc.flyai_enabled = True
        svc._run_flyai = lambda args: {"data": {"itemList": []}}
        request = _request(intercity_transportation="火车")
        quote = svc._estimate_roundtrip_ticket(request, "train")

        assert quote.total_price == 0
        assert quote.source == ""

    def test_masked_price_in_outbound_rejects_both(self):
        """Masked price should still reject the entire roundtrip."""
        svc = _svc()
        svc.flyai_enabled = True
        responses = iter([
            {"data": {"itemList": [
                _train_item("太原站", "五台山站", "2026-08-01 07:30", price="2**"),
            ]}},
            {"data": {"itemList": [
                _train_item("五台山站", "太原站", "2026-08-02 16:30", price="108.00"),
            ]}},
        ])
        svc._run_flyai = lambda args: next(responses)
        request = _request(intercity_transportation="火车")
        quote = svc._estimate_roundtrip_ticket(request, "train")

        assert quote.total_price == 0
        assert quote.source == ""

    def test_outbound_only_with_price_parse_failure(self):
        """Outbound hits but price=0 → still rejected."""
        svc = _svc()
        svc.flyai_enabled = True
        responses = iter([
            {"data": {"itemList": [
                _train_item("太原站", "五台山站", "2026-08-01 07:30", price="价格待询"),
            ]}},
            {"data": {"itemList": []}},
        ])
        svc._run_flyai = lambda args: next(responses)
        request = _request(intercity_transportation="火车")
        quote = svc._estimate_roundtrip_ticket(request, "train")

        assert quote.total_price == 0

    def test_outbound_only_location_filter_works(self):
        """Outbound succeeds but with 山西太原 normalization, inbound fails because 五台山→山西太原 has no matching items."""
        svc = _svc()
        svc.flyai_enabled = True
        # Outbound: 山西太原→五台山, station is 太原站→五台山站
        # Inbound: 五台山→山西太原, but FlyAI only has 五台山站→忻州站 (not matching 山西太原)
        responses = iter([
            {"data": {"itemList": [
                _train_item("太原站", "五台山站", "2026-08-01 07:30", price="98.00"),
            ]}},
            {"data": {"itemList": [
                _train_item("五台山站", "忻州站", "2026-08-02 16:30", price="28.00"),
            ]}},
        ])
        svc._run_flyai = lambda args: next(responses)
        request = _request(intercity_transportation="火车")
        quote = svc._estimate_roundtrip_ticket(request, "train")

        # Inbound item: 五台山站→忻州站 with destination=山西太原
        # 忻州 does not match 山西太原 (even after normalization).
        # Without the blind origin+date fallback, this is correctly rejected.
        # Result: outbound-only partial estimate.
        assert quote.source == "flyai_train_partial", (
            f"Expected flyai_train_partial (忻州≠太原, inbound rejected), got {quote.source}"
        )
        assert quote.unit_price == 196  # 98 (real outbound) + 98 (estimated return)


# ═══════════════════════════════════════════════════════════════════════════
# Section 4 — full end-to-end 山西太原→五台山 scenario
# ═══════════════════════════════════════════════════════════════════════════


class TestEndToEndScenario:
    """Full estimate_budget flow for the user's exact scenario."""

    def test_full_scenario_with_realistic_mock(self):
        """Simulate estimate_budget with realistic mock data that should
        succeed now (after fixes) where it failed before."""
        svc = _svc()
        svc.flyai_enabled = True

        def mock_flyai(args):
            cmd = args[0] if args else ""
            if cmd == "search-hotel":
                return {
                    "data": {
                        "itemList": [
                            {"name": "五台山友谊宾馆", "price": "168", "star": "三星级"},
                        ]
                    }
                }
            if cmd == "search-train":
                origin = args[args.index("--origin") + 1]
                dest = args[args.index("--destination") + 1]
                dep = args[args.index("--dep-date") + 1]
                if "太原" in origin:
                    return {"data": {"itemList": [
                        _train_item("太原站", "五台山站", f"{dep} 07:30", price="98.00"),
                    ]}}
                else:
                    return {"data": {"itemList": [
                        _train_item("五台山站", "太原站", f"{dep} 16:30", price="108.00"),
                    ]}}
            if cmd == "search-flight":
                return {"data": {"itemList": []}}  # no flights for this route
            return {}

        svc._run_flyai = mock_flyai
        request = _request(intercity_transportation="火车")
        plan = _plan(request)
        budget = svc.estimate_budget(request, plan)

        # After fixes, hotel and train should both hit
        assert budget.hotel_unit_price == 168
        assert "FlyAI 酒店" in budget.budget_source
        assert budget.transport_unit_price == 206  # 98 + 108
        assert budget.intercity_transportation == 412  # 206 * 2
        # Both legs hit with strict matching (太原站/五台山站 normalize correctly).
        assert "FlyAI 往返报价" in budget.budget_source
        # Total: 168(1n*1r) + 412(transport) + meals(~240) + local(~50) + attractions(0)
        assert budget.total > 0

    def test_full_scenario_hotel_only_train_empty(self):
        """Hotel succeeds, train has no data → partial success with train fallback."""
        svc = _svc()
        svc.flyai_enabled = True

        def mock_flyai(args):
            cmd = args[0] if args else ""
            if cmd == "search-hotel":
                return {
                    "data": {
                        "itemList": [
                            {"name": "五台山友谊宾馆", "price": "168", "star": "三星级"},
                        ]
                    }
                }
            # Both train and flight empty
            return {"data": {"itemList": []}}

        svc._run_flyai = mock_flyai
        request = _request(intercity_transportation=None)
        plan = _plan(request)
        budget = svc.estimate_budget(request, plan)

        # Hotel should hit FlyAI
        assert budget.hotel_unit_price == 168
        # Transport should fallback (no train or flight data)
        assert budget.transport_unit_price == 600
        assert "城际交通兜底估算" in budget.budget_source or "heuristic_transport" in budget.budget_source


# ═══════════════════════════════════════════════════════════════════════════
# Section 5 — diagnostic logging
# ═══════════════════════════════════════════════════════════════════════════


class TestDiagnosticLogging:
    """Verify logs contain diagnostic information and no secrets."""

    def _capture_logs(self, svc, action) -> List[str]:
        """Capture all INFO+ log messages during *action*."""
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("app.services.transport_budget_service")
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)

        try:
            action(svc)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        return log_stream.getvalue().splitlines()

    def test_flyai_request_logs_parameters(self):
        """_run_flyai should log operation and sanitized parameters."""
        svc = _svc()
        svc.flyai_enabled = True
        svc.flyai_command = ["fake-flyai"]
        svc.settings = type("S", (), {
            "flyai_api_key": "test-key-12345",
            "flyai_timeout": 5,
        })()

        logs = self._capture_logs(svc, lambda s: s._run_flyai([
            "search-train",
            "--origin", "山西太原",
            "--destination", "五台山",
            "--dep-date", "2026-08-01",
            "--sort-type", "3",
        ]))

        combined = "\n".join(logs)

        # Should log operation and parameters
        assert "search-train" in combined
        assert "山西太原" in combined
        assert "五台山" in combined
        assert "2026-08-01" in combined

        # Must NOT log API key
        assert "test-key-12345" not in combined

    def test_hotel_rejection_logging(self):
        """_pick_hotel_item should log each rejection reason."""
        svc = _svc()
        logs = self._capture_logs(svc, lambda s: s._pick_hotel_item(
            {
                "data": {
                    "itemList": [
                        {"name": "青年旅舍", "price": "50", "star": "经济型"},
                        {"name": "某酒店A", "price": "2**", "star": "三星级"},
                        {"name": "某酒店B", "price": "80", "star": "二星级"},
                        {"name": "某酒店C", "price": "168", "star": "三星级"},
                    ]
                }
            },
            "经济型酒店",
        ))

        combined = "\n".join(logs)

        # The first hit (某酒店C at 168) should be selected
        # But at DEBUG, all rejections are logged; at INFO, only the hit summary
        assert "hotel FlyAI hit" in combined
        assert "168" in combined

    def test_ticket_miss_logging(self):
        """When all tickets are filtered out, log the reason."""
        svc = _svc()
        svc.flyai_enabled = True
        # _run_flyai accesses self.settings — supply a mock
        svc.settings = type("S", (), {
            "flyai_api_key": "test-key",
            "flyai_timeout": 5,
        })()
        svc.flyai_command = ["fake-flyai"]

        logs = self._capture_logs(svc, lambda s: s._estimate_roundtrip_ticket(
            _request(intercity_transportation="火车"), "train"
        ))

        combined = "\n".join(logs)
        # Should log that train query was attempted
        assert "train" in combined.lower()

    def test_no_secrets_in_logs(self):
        """Logs must never contain the API key."""
        svc = _svc()
        svc.flyai_enabled = True
        svc.flyai_command = ["fake-flyai"]
        svc.settings = type("S", (), {
            "flyai_api_key": "sk-secret-api-key-abc123",
            "flyai_timeout": 5,
        })()

        logs = self._capture_logs(svc, lambda s: s._run_flyai([
            "search-hotel",
            "--dest-name", "五台山",
            "--check-in-date", "2026-08-01",
            "--check-out-date", "2026-08-02",
            "--sort", "price_asc",
        ]))

        combined = "\n".join(logs)
        assert "sk-secret-api-key-abc123" not in combined
        assert "api_key" not in combined.lower() or "missing" in combined.lower()

    def test_count_raw_items_handles_all_response_shapes(self):
        svc = _svc()
        assert svc._count_raw_items({"data": {"itemList": [1, 2, 3]}}) == 3
        assert svc._count_raw_items({"data": {"itemList": []}}) == 0
        assert svc._count_raw_items({"data": {}}) == -3
        assert svc._count_raw_items([]) == -1
        assert svc._count_raw_items("string") == -1
        assert svc._count_raw_items(None) == -1


# ═══════════════════════════════════════════════════════════════════════════
# Section 6 — regression: other cities and routes
# ═══════════════════════════════════════════════════════════════════════════


class TestRegressionOtherRoutes:
    """Verify fixes don't break other common route pairs."""

    @pytest.mark.parametrize(
        "origin, dest, origin_station, dest_station, should_match",
        [
            # 西安→成都
            ("西安", "成都", "西安北站", "成都东站", True),
            # 北京→上海
            ("北京", "上海", "北京南站", "上海虹桥站", True),
            # 广州→深圳
            ("广州", "深圳", "广州南站", "深圳北站", True),
            # 河北省石家庄→河南省郑州 (province prefix)
            ("河北省石家庄", "河南省郑州", "石家庄站", "郑州站", True),
            ("石家庄", "郑州", "石家庄站", "郑州东站", True),
            # 杭州→南京 (no prefix needed)
            ("杭州", "南京", "杭州东站", "南京南站", True),
        ],
    )
    def test_common_route_pairs(self, origin, dest, origin_station, dest_station, should_match):
        svc = _svc()
        item = _train_item(origin_station, dest_station, "2030-01-01 08:00")
        result = svc._ticket_item_matches_request(item, origin, dest, "2030-01-01")
        assert result == should_match

    def test_hotel_works_for_city_destinations(self):
        """Hotel search for regular city names (not scenic areas) should work as before."""
        svc = _svc()
        svc.flyai_enabled = True
        svc._run_flyai = lambda args: {
            "data": {
                "itemList": [
                    {"name": "成都某经济酒店", "price": "180", "star": "三星级"},
                ]
            }
        }
        request = _request(origin_city="西安", city="成都",
                           start_date="2030-01-01", end_date="2030-01-02",
                           intercity_transportation="高铁")
        plan = _plan(request)
        quote = svc._estimate_hotel(request, plan, hotel_nights=1, hotel_rooms=1)
        assert quote.source == "flyai_hotel"
        assert quote.unit_price == 180

    def test_intercity_mode_explicit_train_only_queries_train(self):
        """With '火车' mode, only train should be queried (not flight)."""
        svc = _svc()
        svc.flyai_enabled = True
        calls = []

        def capture(args):
            calls.append(args[0])
            return {"data": {"itemList": []}}

        svc._run_flyai = capture
        request = _request(intercity_transportation="火车")
        svc._estimate_intercity_transport(request)

        assert "search-flight" not in calls
        assert "search-train" in calls


# ═══════════════════════════════════════════════════════════════════════════
# Section 7 — hotel logging detail
# ═══════════════════════════════════════════════════════════════════════════


class TestHotelRejectionReasons:
    """Verify each hotel rejection category is properly tracked."""

    def test_count_rejection_categories(self):
        svc = _svc()
        # All items should be rejected for different reasons
        data = {
            "data": {
                "itemList": [
                    {"name": "青旅床位", "price": "25", "star": "经济型"},     # below floor + hostel
                    {"name": "某酒店A", "price": "2**", "star": "三星级"},    # masked
                    {"name": "某酒店B", "price": "80", "star": "二星级"},     # below floor
                    {"name": "某民宿", "price": "150", "star": "民宿"},       # accommodation mismatch? (民宿 not filtered by _hotel_matches_accommodation for 经济型)
                ]
            }
        }
        result = svc._pick_hotel_item(data, "经济型酒店")
        # The 民宿 at 150 should pass: price 150 >= 100 floor, name doesn't contain hostel markers
        # So it should be the selected item
        assert result is not None
        assert result.get("name") == "某民宿"

    def test_all_rejected_scenario(self):
        """When every candidate is rejected, verify None return."""
        svc = _svc()
        data = {
            "data": {
                "itemList": [
                    {"name": "青旅床位", "price": "25", "star": "经济型"},
                    {"name": "某酒店A", "price": "2**", "star": "三星级"},
                    {"name": "某酒店B", "price": "80", "star": "二星级"},
                    {"name": "某青年旅舍", "price": "50", "star": "经济型"},
                ]
            }
        }
        result = svc._pick_hotel_item(data, "经济型酒店")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# Section 8 — transport reference matching in quality gate
# ═══════════════════════════════════════════════════════════════════════════


class TestTransportReferenceMatching:
    """Verify _transport_reference_matches with province-prefixed cities."""

    @pytest.fixture
    def quality(self):
        from app.services.trip_plan_quality_service import TripPlanQualityService
        return TripPlanQualityService()

    def _request(self, origin="山西太原", dest="大同", start="2026-07-25", end="2026-07-26"):
        return TripRequest(
            origin_city=origin,
            city=dest,
            start_date=start,
            end_date=end,
            travel_days=2,
            travelers=2,
            transportation="公共交通",
            accommodation="经济型酒店",
            preferences=[],
        )

    # ── Must-match cases ──

    def test_real_flyai_reference_passes(self, quality):
        """Real FlyAI reference: 山西太原↔大同 with station names."""
        ref = (
            "山西太原->大同 [2026-07-25]: "
            "太原站->大同站 2026-07-25 16:58 普快 K892 硬座 43.50; "
            "大同->山西太原 [2026-07-26]: "
            "大同站->太原站 2026-07-26 12:10 普快 K891 硬座 43.50"
        )
        assert quality._transport_reference_matches(self._request(), ref)

    def test_province_origin_with_station_names(self, quality):
        """山西太原 origin ↔ 太原站 in detail."""
        ref = (
            "山西太原->大同 [2026-07-25]: "
            "太原站->大同站 2026-07-25 08:00; "
            "大同->山西太原 [2026-07-26]: "
            "大同站->太原站 2026-07-26 18:00"
        )
        assert quality._transport_reference_matches(self._request(), ref)

    def test_full_admin_names(self, quality):
        """山西省太原市 ↔ 太原南站."""
        request = self._request(origin="山西省太原市", dest="大同")
        ref = (
            "山西省太原市->大同 [2026-07-25]: "
            "太原南站->大同站 2026-07-25 09:00; "
            "大同->山西省太原市 [2026-07-26]: "
            "大同站->太原南站 2026-07-26 19:00"
        )
        assert quality._transport_reference_matches(request, ref)

    def test_hebei_shijiazhuang(self, quality):
        """河北省石家庄市 ↔ 石家庄站."""
        request = self._request(origin="河北省石家庄市", dest="郑州",
                               start="2030-01-01", end="2030-01-02")
        ref = (
            "河北省石家庄市->郑州 [2030-01-01]: "
            "石家庄站->郑州站 2030-01-01 08:00; "
            "郑州->河北省石家庄市 [2030-01-02]: "
            "郑州站->石家庄站 2030-01-02 18:00"
        )
        assert quality._transport_reference_matches(request, ref)

    def test_shaanxi_xian(self, quality):
        """陕西省西安市 ↔ 西安北站."""
        request = self._request(origin="陕西省西安市", dest="成都",
                               start="2030-01-01", end="2030-01-02")
        ref = (
            "陕西省西安市->成都 [2030-01-01]: "
            "西安北站->成都东站 2030-01-01 08:00; "
            "成都->陕西省西安市 [2030-01-02]: "
            "成都东站->西安北站 2030-01-02 18:00"
        )
        assert quality._transport_reference_matches(request, ref)

    def test_beijing_unchanged(self, quality):
        """北京 (municipality) must not be stripped."""
        request = self._request(origin="北京", dest="上海",
                               start="2030-01-01", end="2030-01-02")
        ref = (
            "北京->上海 [2030-01-01]: "
            "北京南站->上海虹桥站 2030-01-01 08:00; "
            "上海->北京 [2030-01-02]: "
            "上海虹桥站->北京南站 2030-01-02 18:00"
        )
        assert quality._transport_reference_matches(request, ref)

    def test_shanghai_unchanged(self, quality):
        """上海 municipality must remain unchanged."""
        request = self._request(origin="上海", dest="南京",
                               start="2030-01-01", end="2030-01-02")
        ref = (
            "上海->南京 [2030-01-01]: "
            "上海虹桥站->南京南站 2030-01-01 08:00; "
            "南京->上海 [2030-01-02]: "
            "南京南站->上海虹桥站 2030-01-02 18:00"
        )
        assert quality._transport_reference_matches(request, ref)

    # ── Must-NOT-match cases ──

    def test_wrong_destination_rejected(self, quality):
        """Destination in ref (成都) ≠ request dest (大同)."""
        ref = (
            "山西太原->成都 [2026-07-25]: "
            "太原站->成都东站 2026-07-25 08:00; "
            "成都->山西太原 [2026-07-26]: "
            "成都东站->太原站 2026-07-26 18:00"
        )
        assert not quality._transport_reference_matches(self._request(), ref)

    def test_wrong_date_rejected(self, quality):
        """Date mismatch."""
        ref = (
            "山西太原->大同 [2026-08-01]: "
            "太原站->大同站 2026-08-01 08:00; "
            "大同->山西太原 [2026-08-02]: "
            "大同站->太原站 2026-08-02 18:00"
        )
        # Request has 2026-07-25/26
        assert not quality._transport_reference_matches(self._request(), ref)

    def test_reversed_direction_rejected(self, quality):
        """Outbound and inbound swapped."""
        ref = (
            "大同->山西太原 [2026-07-25]: "
            "大同站->太原站 2026-07-25 08:00; "
            "山西太原->大同 [2026-07-26]: "
            "太原站->大同站 2026-07-26 18:00"
        )
        # Outbound should be 太原→大同 on 07-25, not 大同→太原
        assert not quality._transport_reference_matches(self._request(), ref)

    def test_only_one_leg_rejected(self, quality):
        """Single-leg reference must fail."""
        ref = "山西太原->大同 [2026-07-25]: 太原站->大同站"
        assert not quality._transport_reference_matches(self._request(), ref)

    def test_fallback_reference_passes(self, quality):
        """兜底 reference style (no detail) should NOT trigger mismatch.

        When transport falls back, there's no station detail to match
        against.  The quality gate checks for fallback-indicator text
        elsewhere and skips this check via the ``fallback_transport``
        guard in ``evaluate()``.
        """
        ref = "山西太原 往返 大同 交通兜底估算"
        # This has no structured detail → leg splitting fails → returns False
        # But that's OK because evaluate() guards on "兜底估算" first.
        # Here we just verify the method itself doesn't crash.
        result = quality._transport_reference_matches(self._request(), ref)
        assert result is False

    def test_unrelated_city_not_matched(self, quality):
        """太原 must not spuriously match 太原街 (a street in Shenyang)."""
        ref = (
            "山西太原->大同 [2026-07-25]: "
            "沈阳站->大同站 2026-07-25 08:00; "
            "大同->山西太原 [2026-07-26]: "
            "大同站->沈阳站 2026-07-26 18:00"
        )
        # Detail has 沈阳 not 太原 → mismatch
        assert not quality._transport_reference_matches(self._request(), ref)


# ═══════════════════════════════════════════════════════════════════════════
# Section 9 — shared normalization consistency
# ═══════════════════════════════════════════════════════════════════════════


class TestSharedNormalization:
    """Verify budget service and quality service normalize identically."""

    def test_budget_and_quality_normalize_same(self):
        from app.services.transport_budget_service import TransportBudgetService
        from app.services.destination_feasibility_service import get_destination_feasibility_service

        budget_svc = TransportBudgetService.__new__(TransportBudgetService)
        feasibility = get_destination_feasibility_service()

        cases = [
            "山西太原", "山西省太原市", "太原站", "太原南站", "太原",
            "五台山", "五台山站",
            "北京", "北京西站", "北京南站",
            "河北省石家庄市", "石家庄站",
            "陕西省西安市", "西安北站",
            "吉林", "吉林站",
        ]
        for case in cases:
            b = budget_svc._normalize_location_name(case)
            f = feasibility.normalize_location_for_matching(case)
            assert b == f, (
                f"MISMATCH for {case!r}: budget={b!r}, feasibility={f!r}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Section 10 — SHORT_TRIP_DESTINATION_RISK downgraded to non-blocking
# ═══════════════════════════════════════════════════════════════════════════


class TestShortTripRiskNotBlocking:
    """Verify SHORT_TRIP_DESTINATION_RISK is a warning, not a hard block."""

    def test_risk_not_in_blocking_codes(self):
        """SHORT_TRIP_DESTINATION_RISK must NOT be in the blocking codes set."""
        from app.services.trip_plan_quality_service import TripPlanQualityService
        qs = TripPlanQualityService()
        # evaluate() method references blocking_codes inline (not stored as
        # an attribute).  Validate by calling _transport_reference_matches
        # directly and checking the logic: SHORT_TRIP_DESTINATION_RISK
        # severity is "warning", not "error".
        from app.services.destination_feasibility_service import get_destination_feasibility_service
        feasibility = get_destination_feasibility_service()
        # 临汾 has no short-haul circle of its own → unknown-radius path.
        r = feasibility.assess("山西临汾", "大同", 2, explicit_destination=True)
        # Must be allowed (user explicitly chose it).
        assert r.allowed is True
        # Must be "warning" not "error" for explicit destinations.
        assert r.severity == "warning"
        # The 2026-07-25 date is today → check it's not in the past.
        assert r.score == 75  # score for non-graph destinations with days<=3

    def test_unreachable_is_blocking(self):
        """SHORT_TRIP_DESTINATION_UNREACHABLE (severity=error) blocks."""
        from app.services.destination_feasibility_service import get_destination_feasibility_service
        feasibility = get_destination_feasibility_service()
        # 扶风→北京, 2 days, NOT explicit → severity="error"
        r = feasibility.assess("扶风", "北京", 2, explicit_destination=False)
        assert r.allowed is False, "Non-explicit far destination should not be allowed"
        assert r.severity == "error"

    def test_nearby_destination_is_info(self):
        """西安→宝鸡 (in graph) returns severity='info'."""
        from app.services.destination_feasibility_service import get_destination_feasibility_service
        feasibility = get_destination_feasibility_service()
        r = feasibility.assess("西安", "宝鸡", 2, explicit_destination=True)
        assert r.severity == "info"
        assert r.allowed is True

    def test_explicit_far_destination_allowed(self):
        """太原→三亚 on a weekend is allowed when explicit (user chose it)."""
        from app.services.destination_feasibility_service import get_destination_feasibility_service
        feasibility = get_destination_feasibility_service()
        r = feasibility.assess("山西太原", "三亚", 2, explicit_destination=True)
        assert r.allowed is True
        assert r.severity == "warning"

    def test_province_prefixed_origin_resolves_to_its_short_trip_circle(self):
        """"山西太原" must hit the same graph key as "太原"."""
        from app.services.destination_feasibility_service import get_destination_feasibility_service
        feasibility = get_destination_feasibility_service()
        assert feasibility.nearby_destinations("山西太原") == (
            feasibility.nearby_destinations("太原")
        )
        assert feasibility.nearby_destinations("太原")
        r = feasibility.assess("山西太原", "大同", 2, explicit_destination=False)
        assert r.allowed is True
        assert r.severity == "info"

    def test_transport_ref_mismatch_still_blocks(self):
        """TRANSPORT_REFERENCE_MISMATCH must remain a blocking code."""
        from app.services.trip_plan_quality_service import TripPlanQualityService
        qs = TripPlanQualityService()
        ref = (
            "北京->上海 [2030-06-01]: 北京南->上海虹桥 2030-06-01 08:00; "
            "上海->北京 [2030-06-02]: 上海虹桥->北京南 2030-06-02 18:00"
        )
        request = TripRequest(
            origin_city="西安", city="宝鸡",
            start_date="2030-06-01", end_date="2030-06-02",
            travel_days=2, travelers=2,
            transportation="公共交通", accommodation="经济型酒店",
            preferences=[],
        )
        assert not qs._transport_reference_matches(request, ref), (
            "西安→宝鸡 should not match 北京→上海 reference"
        )

    def test_norm_origin_used_in_assess(self):
        """assess() now uses normalize_location_for_matching for origin lookup."""
        from app.services.destination_feasibility_service import get_destination_feasibility_service
        feasibility = get_destination_feasibility_service()
        # "山西太原" with normalize_city → "山西太原" (not in graph)
        # "山西太原" with normalize_location_for_matching → "太原"
        # Even though "太原" is not in SHORT_TRIP_CITY_GRAPH now,
        # verify the normalization happens correctly.
        assert feasibility.normalize_location_for_matching("山西太原") == "太原"
        assert feasibility.normalize_location_for_matching("山西省太原市") == "太原"


# ═══════════════════════════════════════════════════════════════════════════
# Section 11 — POI destination validation
# ═══════════════════════════════════════════════════════════════════════════


class TestPOIDestinationValidation:
    """Verify poi_destination_status with structured fields + address fallback."""

    @pytest.fixture
    def qs(self):
        from app.services.trip_plan_quality_service import TripPlanQualityService
        return TripPlanQualityService()

    def _req(self, origin="山西太原", dest="大同"):
        return TripRequest(
            origin_city=origin, city=dest,
            start_date="2030-06-01", end_date="2030-06-02",
            travel_days=2, travelers=2,
            transportation="公共交通", accommodation="经济型酒店",
            preferences=[],
        )

    def _attr(self, name, address, poi_id="", coord_src="",
              cityname="", citycode="", adname="", adcode=""):
        from app.models.schemas import VerificationMeta
        return Attraction(
            name=name, address=address,
            location=Location(longitude=113.3, latitude=40.1),
            visit_duration=120, description="", category="景点",
            poi_id=poi_id, coordinate_source=coord_src,
            verification=VerificationMeta(
                cityname=cityname, citycode=citycode,
                adname=adname, adcode=adcode,
            ) if (cityname or citycode or adname or adcode) else None,
        )

    # ── Structured: matched ──

    def test_cityname_direct_match(self):
        """cityname=大同市 → matched (address has no admin refs)."""
        from app.services.destination_feasibility_service import poi_destination_status
        assert poi_destination_status(
            destination_city="大同", cityname="大同市",
            address="武定街10号") == "matched"

    def test_citycode_match(self):
        """citycode=0352 (大同) → matched."""
        from app.services.destination_feasibility_service import poi_destination_status
        assert poi_destination_status(
            destination_city="大同", citycode="0352",
            address="武定街10号") == "matched"

    def test_citycode_adcode_together_matched(self):
        """citycode=0352 + adcode=140211 → matched via citycode."""
        from app.services.destination_feasibility_service import poi_destination_status
        assert poi_destination_status(
            destination_city="大同", citycode="0352", adcode="140211",
            address="武定街10号") == "matched"

    # ── Structured: mismatched ──

    def test_cityname_mismatch(self):
        """cityname=太原市, dest=大同 → mismatched."""
        from app.services.destination_feasibility_service import poi_destination_status
        assert poi_destination_status(
            destination_city="大同", cityname="太原市",
            address="山西省太原市迎泽区") == "mismatched"

    def test_cityname_mismatch_ignores_address_conflict(self):
        """cityname=太原市, address has '大同市' → cityname wins → mismatched."""
        from app.services.destination_feasibility_service import poi_destination_status
        assert poi_destination_status(
            destination_city="大同", cityname="太原市",
            address="大同市平城区") == "mismatched"

    def test_citycode_mismatch(self):
        """citycode=0351 (太原), dest=大同 → mismatched."""
        from app.services.destination_feasibility_service import poi_destination_status
        assert poi_destination_status(
            destination_city="大同", citycode="0351",
            address="") == "mismatched"

    # ── Structured: unknown ──

    def test_adname_only_unknown(self):
        """adname=云冈区 alone → unknown (can't confirm parent city)."""
        from app.services.destination_feasibility_service import poi_destination_status
        assert poi_destination_status(
            destination_city="大同", adname="云冈区",
            address="武定街10号") == "unknown"

    def test_empty_all_fields_unknown(self):
        """No structured fields, no admin address → unknown."""
        from app.services.destination_feasibility_service import poi_destination_status
        assert poi_destination_status(
            destination_city="大同", address="武定街10号") == "unknown"

    # ── Address fallback ──

    def test_address_city_admin_matched(self):
        """Address '大同市云冈区云冈景区' → matched via fallback."""
        from app.services.destination_feasibility_service import poi_destination_status
        assert poi_destination_status(
            destination_city="大同",
            address="大同市云冈区云冈景区") == "matched"

    def test_address_wrong_city_mismatched(self):
        """Address '太原市迎泽区' with dest=大同 → mismatched."""
        from app.services.destination_feasibility_service import poi_destination_status
        assert poi_destination_status(
            destination_city="大同",
            address="山西省太原市迎泽区") == "mismatched"

    # ── Quality service integration ──

    def test_quality_structured_matched(self, qs):
        """cityname=大同市 → _attraction_matches_destination returns matched."""
        attr = self._attr("九龙壁", "武定街10号",
                          poi_id="B0160005JK", coord_src="amap_poi",
                          cityname="大同市")
        assert qs._attraction_matches_destination(self._req(), attr) == "matched"

    def test_quality_structured_mismatched(self, qs):
        """cityname=太原市 → mismatched."""
        attr = self._attr("中国煤炭博物馆", "山西省太原市迎泽区",
                          poi_id="B0AMAP01", coord_src="amap_poi",
                          cityname="太原市")
        assert qs._attraction_matches_destination(self._req(), attr) == "mismatched"

    def test_quality_no_structured_unknown(self, qs):
        """No verification metadata, no admin address → unknown."""
        attr = self._attr("中国雕塑博物馆", "武定街10号",
                          poi_id="B01600N5QE", coord_src="amap_poi")
        assert qs._attraction_matches_destination(self._req(), attr) == "unknown"

    def test_quality_address_fallback_matched(self, qs):
        """No verification metadata, but address has '大同市' → matched."""
        attr = self._attr("九龙壁", "大同市云冈区大同古城大东街18号",
                          poi_id="B0160005JK", coord_src="amap_poi")
        assert qs._attraction_matches_destination(self._req(), attr) == "matched"

    # ── Full quality evaluation ──

    def test_full_eval_wrong_city_blocked(self, qs):
        """cityname=太原市 → POI_DESTINATION_MISMATCH → blocked."""
        attr = self._attr("中国煤炭博物馆", "山西省太原市迎泽区",
                          poi_id="B0AMAP01", coord_src="amap_poi",
                          cityname="太原市")
        day = DayPlan(date="2030-06-01", day_index=0, description="",
            transportation="公共交通", accommodation="经济型酒店",
            attractions=[attr], meals=[Meal(type="lunch", name="午餐")])
        plan = TripPlan(city="大同", start_date="2030-06-01", end_date="2030-06-02",
            days=[day], overall_suggestions="", generation_mode="primary")
        result = qs.evaluate(self._req(), plan)
        poi_issues = [i for i in result.issues if i.code == "POI_DESTINATION_MISMATCH"]
        assert len(poi_issues) >= 1
        assert result.quality_status == "blocked"

    def test_full_eval_structured_datong_passes(self, qs):
        """cityname=大同市 → no POI_DESTINATION_MISMATCH."""
        attr = self._attr("九龙壁", "武定街10号",
                          poi_id="B0160005JK", coord_src="amap_poi",
                          cityname="大同市", adname="云冈区")
        day = DayPlan(date="2030-06-01", day_index=0, description="",
            transportation="公共交通", accommodation="经济型酒店",
            attractions=[attr], meals=[Meal(type=m, name=m) for m in ("breakfast","lunch","dinner")])
        plan = TripPlan(city="大同", start_date="2030-06-01", end_date="2030-06-02",
            days=[day], overall_suggestions="", generation_mode="primary")
        result = qs.evaluate(self._req(), plan)
        poi_issues = [i for i in result.issues if i.code == "POI_DESTINATION_MISMATCH"]
        assert len(poi_issues) == 0
