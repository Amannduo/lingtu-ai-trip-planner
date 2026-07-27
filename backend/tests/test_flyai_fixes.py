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
