"""Budget estimation service backed by FlyAI CLI when available."""

from __future__ import annotations

import math
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from ..config import get_settings
from ..models.schemas import Budget, TripPlan, TripRequest


@dataclass
class QuoteResult:
    unit_price: int = 0
    total_price: int = 0
    reference: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    source: str = "heuristic"


class TransportBudgetService:
    """Estimate hotel and transportation costs for a trip."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.flyai_enabled = bool(self.settings.flyai_enabled)
        self.flyai_command = self._split_command(self.settings.flyai_cli_command)
        print(
            "[budget] service initialized: "
            f"flyai_enabled={self.flyai_enabled}, "
            f"api_key={'configured' if self.settings.flyai_api_key else 'missing'}, "
            f"command={' '.join(self.flyai_command) if self.flyai_command else 'not configured'}"
        )

    def estimate_budget(self, request: TripRequest, trip_plan: TripPlan) -> Budget:
        print(
            "[budget] start estimate: "
            f"origin={request.origin_city or '-'}, destination={request.city}, "
            f"travelers={request.travelers}, days={request.travel_days}, "
            f"intercity={request.intercity_transportation or '-'}, local={request.transportation}"
        )
        hotel_nights = self._get_hotel_nights(request)
        hotel_rooms = max(1, math.ceil(request.travelers / 2))

        hotel_quote = self._estimate_hotel(request, trip_plan, hotel_nights, hotel_rooms)
        intercity_quote = self._estimate_intercity_transport(request)

        attraction_total = self._sum_attraction_costs(trip_plan)
        meal_total = self._sum_meal_costs(trip_plan)
        budget_notes: List[str] = []

        if meal_total <= 0:
            meal_total = self._estimate_meal_cost(request)
            budget_notes.append("餐饮费用缺少结构化价格，已按出行天数和住宿档次估算。")

        local_transport_total = self._estimate_local_transport(request)
        budget_notes.append(self._build_local_transport_note(request, local_transport_total))

        budget_notes.extend(hotel_quote.notes)
        budget_notes.extend(intercity_quote.notes)
        budget_notes = self._unique_notes(budget_notes)

        total_transportation = local_transport_total + intercity_quote.total_price
        total = attraction_total + hotel_quote.total_price + meal_total + total_transportation
        budget_source = self._build_budget_source(hotel_quote.source, intercity_quote.source)
        print(
            "[budget] final estimate: "
            f"hotel={hotel_quote.total_price}, intercity={intercity_quote.total_price}, "
            f"local_transport={local_transport_total}, meals={meal_total}, "
            f"attractions={attraction_total}, total={total}, source={budget_source}"
        )

        return Budget(
            total_attractions=attraction_total,
            total_hotels=hotel_quote.total_price,
            total_meals=meal_total,
            total_transportation=total_transportation,
            total=total,
            hotel_nights=hotel_nights,
            hotel_rooms=hotel_rooms,
            hotel_unit_price=hotel_quote.unit_price,
            intercity_transportation=intercity_quote.total_price,
            local_transportation=local_transport_total,
            transport_unit_price=intercity_quote.unit_price,
            budget_source=budget_source,
            hotel_reference=hotel_quote.reference,
            transport_reference=intercity_quote.reference,
            budget_notes=budget_notes
        )

    def _estimate_hotel(
        self,
        request: TripRequest,
        trip_plan: TripPlan,
        hotel_nights: int,
        hotel_rooms: int
    ) -> QuoteResult:
        if hotel_nights <= 0:
            print("[budget] hotel skipped: no overnight stay")
            return QuoteResult(
                total_price=0,
                notes=["行程未跨夜，酒店费用按 0 计算。"],
                source="not_required"
            )

        if self.flyai_enabled:
            print(
                "[budget] hotel FlyAI search: "
                f"city={request.city}, nights={hotel_nights}, rooms={hotel_rooms}, "
                f"accommodation={request.accommodation}"
            )
            args = [
                "search-hotel",
                "--dest-name", request.city,
                "--check-in-date", request.start_date,
                "--check-out-date", request.end_date,
                "--sort", "price_asc",
            ]

            poi_name = self._first_attraction_name(trip_plan)
            if poi_name:
                args.extend(["--poi-name", poi_name])

            max_price = self._suggest_hotel_price_cap(request, hotel_nights, hotel_rooms)
            if max_price:
                args.extend(["--max-price", str(max_price)])

            hotel_type, hotel_stars = self._map_accommodation_filters(request.accommodation)
            if hotel_type:
                args.extend(["--hotel-types", hotel_type])
            if hotel_stars:
                args.extend(["--hotel-stars", hotel_stars])

            data = self._run_flyai(args)
            item = self._pick_first_item(data)
            if not item:
                item = self._pick_first_item(self._run_flyai([
                    "search-hotel",
                    "--dest-name", request.city,
                    "--check-in-date", request.start_date,
                    "--check-out-date", request.end_date,
                    "--sort", "price_asc",
                ]))
            if item:
                unit_price, masked = self._parse_price(item.get("price"))
                if unit_price > 0:
                    notes: List[str] = []
                    if masked:
                        notes.append("FlyAI 酒店价格在体验模式下被脱敏，已按价格区间中位数估算。")
                    reference = self._build_hotel_reference(item)
                    print(
                        "[budget] hotel FlyAI hit: "
                        f"unit={unit_price}, nights={hotel_nights}, rooms={hotel_rooms}, "
                        f"total={unit_price * hotel_nights * hotel_rooms}, reference={reference}"
                    )
                    return QuoteResult(
                        unit_price=unit_price,
                        total_price=unit_price * hotel_nights * hotel_rooms,
                        reference=reference,
                        notes=notes,
                        source="flyai_hotel"
                    )

        fallback_unit = self._fallback_hotel_price(request.accommodation)
        print(
            "[budget] hotel fallback: "
            f"unit={fallback_unit}, nights={hotel_nights}, rooms={hotel_rooms}, "
            f"total={fallback_unit * hotel_nights * hotel_rooms}"
        )
        return QuoteResult(
            unit_price=fallback_unit,
            total_price=fallback_unit * hotel_nights * hotel_rooms,
            reference=f"{request.accommodation} 参考单晚 {fallback_unit} 元",
            notes=["未获取到 FlyAI 酒店价格，已按住宿档次使用兜底单晚价格。"],
            source="heuristic_hotel"
        )

    def _estimate_intercity_transport(self, request: TripRequest) -> QuoteResult:
        if not request.origin_city:
            print("[budget] intercity skipped: origin city not provided")
            return QuoteResult(
                notes=["未提供出发城市，未计算城际交通费用。"],
                source="not_provided"
            )

        if request.origin_city == request.city:
            print("[budget] intercity skipped: origin equals destination")
            return QuoteResult(
                notes=["出发地与目的地相同，城际交通费用按 0 计算。"],
                source="not_required"
            )

        mode = (request.intercity_transportation or "").strip().lower()
        print(
            "[budget] intercity estimate: "
            f"origin={request.origin_city}, destination={request.city}, "
            f"mode={request.intercity_transportation or '-'}"
        )
        if "自驾" in mode:
            fallback = 400 * request.travelers
            print(f"[budget] intercity drive fallback: unit=400, total={fallback}")
            return QuoteResult(
                unit_price=400,
                total_price=fallback,
                reference=f"{request.origin_city} 往返 {request.city} 自驾估算",
                notes=["FlyAI 不提供自驾价格，城际交通按单人 400 元兜底估算。"],
                source="heuristic_drive"
            )

        if "飞机" in mode:
            flight_quote = self._estimate_roundtrip_ticket(request, "flight")
            if flight_quote.total_price > 0:
                return flight_quote

        if "火车" in mode or "高铁" in mode or "铁路" in mode:
            train_quote = self._estimate_roundtrip_ticket(request, "train")
            if train_quote.total_price > 0:
                return train_quote

        flight_quote = self._estimate_roundtrip_ticket(request, "flight")
        train_quote = self._estimate_roundtrip_ticket(request, "train")
        candidates = [quote for quote in [flight_quote, train_quote] if quote.total_price > 0]
        if candidates:
            chosen = min(candidates, key=lambda quote: quote.total_price)
            chosen.notes.append("未指定城际交通方式，已自动选择当前更便宜的 FlyAI 往返方案。")
            chosen.notes = self._unique_notes(chosen.notes)
            return chosen

        fallback_unit = 300
        print(
            "[budget] intercity fallback: "
            f"unit={fallback_unit}, travelers={request.travelers}, total={fallback_unit * request.travelers * 2}"
        )
        return QuoteResult(
            unit_price=fallback_unit,
            total_price=fallback_unit * request.travelers * 2,
            reference=f"{request.origin_city} 往返 {request.city} 交通兜底估算",
            notes=["未获取到 FlyAI 城际交通价格，已按单人往返 300 元兜底估算。"],
            source="heuristic_transport"
        )

    def _estimate_roundtrip_ticket(self, request: TripRequest, ticket_type: str) -> QuoteResult:
        if not self.flyai_enabled:
            print(f"[budget] {ticket_type} skipped: FlyAI disabled")
            return QuoteResult()

        command = "search-flight" if ticket_type == "flight" else "search-train"
        print(
            "[budget] FlyAI roundtrip search: "
            f"type={ticket_type}, origin={request.origin_city}, destination={request.city}, "
            f"out={request.start_date}, back={request.end_date}"
        )
        outbound = self._run_flyai([
            command,
            "--origin", request.origin_city,
            "--destination", request.city,
            "--dep-date", request.start_date,
            "--sort-type", "3",
        ])
        inbound = self._run_flyai([
            command,
            "--origin", request.city,
            "--destination", request.origin_city,
            "--dep-date", request.end_date,
            "--sort-type", "3",
        ])

        outbound_item = self._pick_first_item(outbound)
        inbound_item = self._pick_first_item(inbound)
        if not outbound_item or not inbound_item:
            print(f"[budget] {ticket_type} FlyAI miss: outbound={bool(outbound_item)}, inbound={bool(inbound_item)}")
            return QuoteResult()

        outbound_price, outbound_masked = self._extract_ticket_price(outbound_item, ticket_type)
        inbound_price, inbound_masked = self._extract_ticket_price(inbound_item, ticket_type)
        if outbound_price <= 0 or inbound_price <= 0:
            print(
                f"[budget] {ticket_type} FlyAI price parse failed: "
                f"outbound={outbound_price}, inbound={inbound_price}"
            )
            return QuoteResult()

        unit_price = outbound_price + inbound_price
        notes: List[str] = []
        if outbound_masked or inbound_masked:
            notes.append("FlyAI 交通价格在体验模式下被脱敏，已按价格区间中位数估算。")

        reference = self._build_transport_reference(
            request.origin_city,
            request.city,
            outbound_item,
            inbound_item,
            ticket_type
        )
        source = f"flyai_{ticket_type}"
        print(
            "[budget] FlyAI roundtrip hit: "
            f"type={ticket_type}, outbound={outbound_price}, inbound={inbound_price}, "
            f"unit={unit_price}, travelers={request.travelers}, total={unit_price * request.travelers}"
        )
        return QuoteResult(
            unit_price=unit_price,
            total_price=unit_price * request.travelers,
            reference=reference,
            notes=notes,
            source=source
        )

    def _run_flyai(self, arguments: Sequence[str]) -> Dict[str, Any]:
        if not self.flyai_enabled or not self.flyai_command:
            print("[budget] FlyAI skipped: disabled or command not configured")
            return {}

        command = [*self.flyai_command, *arguments]
        print(f"[budget] FlyAI run: {' '.join(arguments)}")
        env = None
        if self.settings.flyai_api_key:
            env = dict(os.environ)
            env["FLYAI_API_KEY"] = self.settings.flyai_api_key
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.settings.flyai_timeout,
                check=False,
                env=env
            )
        except Exception as exc:
            print(f"[budget] FlyAI command failed before response: {exc}")
            return {}

        if result.returncode != 0 or not result.stdout.strip():
            stderr = (result.stderr or "").strip()
            if len(stderr) > 300:
                stderr = stderr[:300] + "..."
            print(
                "[budget] FlyAI no usable response: "
                f"returncode={result.returncode}, stdout_empty={not bool(result.stdout.strip())}, "
                f"stderr={stderr or '-'}"
            )
            return {}

        try:
            data = self._safe_json_loads(result.stdout.strip())
            print("[budget] FlyAI response parsed")
            return data
        except Exception as exc:
            print(f"[budget] FlyAI JSON parse failed: {exc}")
            return {}

    def _pick_first_item(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        items = data.get("data", {}).get("itemList")
        if isinstance(items, list) and items:
            item = items[0]
            return item if isinstance(item, dict) else None
        return None

    def _extract_ticket_price(self, item: Dict[str, Any], ticket_type: str) -> tuple[int, bool]:
        if ticket_type == "flight":
            return self._parse_price(item.get("ticketPrice") or item.get("price"))
        return self._parse_price(item.get("price") or item.get("ticketPrice"))

    def _parse_price(self, raw_price: Any) -> tuple[int, bool]:
        if raw_price is None:
            return 0, False

        text = str(raw_price).strip()
        if not text:
            return 0, False

        masked_match = re.search(r"(\d+[xX]+)", text)
        if masked_match:
            token = masked_match.group(1).lower()
            low = int(token.replace("x", "0"))
            high = int(token.replace("x", "9"))
            return int(math.ceil((low + high) / 2)), True

        number_match = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
        if number_match:
            return int(round(float(number_match.group(0)))), False

        return 0, False

    def _first_attraction_name(self, trip_plan: TripPlan) -> Optional[str]:
        for day in trip_plan.days:
            for attraction in day.attractions:
                if attraction.name:
                    return attraction.name
        return None

    def _sum_attraction_costs(self, trip_plan: TripPlan) -> int:
        total = 0
        for day in trip_plan.days:
            for attraction in day.attractions:
                total += max(0, int(attraction.ticket_price or 0))
        return total

    def _sum_meal_costs(self, trip_plan: TripPlan) -> int:
        total = 0
        for day in trip_plan.days:
            for meal in day.meals:
                total += max(0, int(meal.estimated_cost or 0))
        return total

    def _estimate_meal_cost(self, request: TripRequest) -> int:
        daily_cost = 120
        accommodation = request.accommodation
        if "舒适" in accommodation:
            daily_cost = 180
        elif "豪华" in accommodation:
            daily_cost = 320
        elif "民宿" in accommodation:
            daily_cost = 140
        return daily_cost * request.travel_days * request.travelers

    def _estimate_local_transport(self, request: TripRequest) -> int:
        mode = request.transportation
        if "步行" in mode:
            return 10 * request.travel_days * request.travelers
        if "自驾" in mode:
            return 180 * request.travel_days
        if "混合" in mode:
            return 50 * request.travel_days * request.travelers
        return 25 * request.travel_days * request.travelers

    def _build_local_transport_note(self, request: TripRequest, total_cost: int) -> str:
        mode = request.transportation or "公共交通"
        return f"市内交通按“{mode}”估算，共 {total_cost} 元。"

    def _suggest_hotel_price_cap(self, request: TripRequest, hotel_nights: int, hotel_rooms: int) -> Optional[int]:
        if not request.budget or hotel_nights <= 0:
            return None

        nightly_cap = int(request.budget * 0.45 / max(hotel_nights * hotel_rooms, 1))
        return max(120, nightly_cap) if nightly_cap > 0 else None

    def _map_accommodation_filters(self, accommodation: str) -> tuple[Optional[str], Optional[str]]:
        if "民宿" in accommodation:
            return "homestay", None
        if "亲子" in accommodation:
            return "hotel", "3,4"
        if "经济" in accommodation:
            return "hotel", "2,3"
        if "舒适" in accommodation:
            return "hotel", "3,4"
        if "豪华" in accommodation:
            return "hotel", "4,5"
        return "hotel", None

    def _fallback_hotel_price(self, accommodation: str) -> int:
        if "民宿" in accommodation:
            return 280
        if "亲子" in accommodation:
            return 560
        if "经济" in accommodation:
            return 220
        if "舒适" in accommodation:
            return 380
        if "豪华" in accommodation:
            return 820
        return 300

    def _get_hotel_nights(self, request: TripRequest) -> int:
        try:
            start = datetime.strptime(request.start_date, "%Y-%m-%d")
            end = datetime.strptime(request.end_date, "%Y-%m-%d")
        except ValueError:
            return max(request.travel_days - 1, 0)
        return max((end - start).days, 0)

    def _build_hotel_reference(self, item: Dict[str, Any]) -> str:
        name = str(item.get("name") or "FlyAI 酒店结果")
        price = str(item.get("price") or "")
        star = str(item.get("star") or "")
        parts = [name]
        if star:
            parts.append(star)
        if price:
            parts.append(price)
        return " | ".join(parts)

    def _build_transport_reference(
        self,
        origin_city: str,
        destination_city: str,
        outbound_item: Dict[str, Any],
        inbound_item: Dict[str, Any],
        ticket_type: str
    ) -> str:
        outbound = self._describe_ticket_item(outbound_item, ticket_type)
        inbound = self._describe_ticket_item(inbound_item, ticket_type)
        return f"{origin_city}->{destination_city}: {outbound}; {destination_city}->{origin_city}: {inbound}"

    def _describe_ticket_item(self, item: Dict[str, Any], ticket_type: str) -> str:
        journeys = item.get("journeys")
        segment: Dict[str, Any] = {}
        if isinstance(journeys, list) and journeys:
            first_journey = journeys[0]
            if isinstance(first_journey, dict):
                segments = first_journey.get("segments")
                if isinstance(segments, list) and segments:
                    first_segment = segments[0]
                    if isinstance(first_segment, dict):
                        segment = first_segment

        transport_no = str(segment.get("marketingTransportNo") or "")
        transport_name = str(segment.get("marketingTransportName") or ("航班" if ticket_type == "flight" else "列车"))
        seat = str(segment.get("seatClassName") or "")
        price = item.get("ticketPrice") if ticket_type == "flight" else item.get("price")

        parts = [transport_name]
        if transport_no:
            parts.append(transport_no)
        if seat:
            parts.append(seat)
        if price:
            parts.append(str(price))
        return " ".join(parts)

    def _build_budget_source(self, hotel_source: str, transport_source: str) -> str:
        source_labels: List[str] = []
        if hotel_source.startswith("flyai"):
            source_labels.append("FlyAI 酒店")
        elif hotel_source not in {"", "not_required"}:
            source_labels.append("酒店兜底估算")

        if transport_source.startswith("flyai"):
            source_labels.append("FlyAI 城际交通")
        elif transport_source not in {"", "not_required", "not_provided"}:
            source_labels.append("城际交通兜底估算")

        source_labels.append("市内交通规则估算")
        return " + ".join(self._unique_notes(source_labels))

    def _split_command(self, command: str) -> List[str]:
        if not command:
            return []
        parts = shlex.split(command, posix=False)
        if not parts:
            return []
        resolved = shutil.which(parts[0])
        if resolved:
            parts[0] = resolved
        return parts

    def _safe_json_loads(self, text: str) -> Dict[str, Any]:
        import json

        return json.loads(text)

    def _unique_notes(self, notes: Sequence[str]) -> List[str]:
        seen = set()
        result: List[str] = []
        for note in notes:
            normalized = note.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result


_transport_budget_service: Optional[TransportBudgetService] = None


def get_transport_budget_service() -> TransportBudgetService:
    global _transport_budget_service
    if _transport_budget_service is None:
        _transport_budget_service = TransportBudgetService()
    return _transport_budget_service
