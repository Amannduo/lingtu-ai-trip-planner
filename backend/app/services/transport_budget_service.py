"""Budget estimation service backed by FlyAI CLI when available."""

from __future__ import annotations

import logging
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from ..config import get_settings
from ..models.schemas import Budget, TripPlan, TripRequest
from .destination_feasibility_service import get_destination_feasibility_service


logger = logging.getLogger(__name__)


@dataclass
class QuoteResult:
    unit_price: int = 0
    total_price: int = 0
    reference: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    source: str = ""


class TransportBudgetService:
    """Estimate hotel and transportation costs for a trip."""

    _FLYAI_UNPINNED_PACKAGE = "@fly-ai/flyai-cli"
    _FLYAI_PINNED_PACKAGE = "@fly-ai/flyai-cli@1.0.16"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.flyai_command = self._split_command(self.settings.flyai_cli_command)
        # A configured key is the explicit opt-in boundary. In particular, a
        # default npx command must never download or execute a package merely
        # because the feature flag retained its legacy default.
        self.flyai_enabled = bool(
            self.settings.flyai_enabled
            and self.settings.flyai_api_key
            and self.flyai_command
        )
        logger.info(
            "[budget] service initialized: "
            f"flyai_enabled={self.flyai_enabled}, "
            f"api_key={'configured' if self.settings.flyai_api_key else 'missing'}, "
            f"command_configured={bool(self.flyai_command)}"
        )

    def estimate_budget(self, request: TripRequest, trip_plan: TripPlan) -> Budget:
        logger.info(
            "[budget] estimate started: "
            f"travelers={request.travelers}, days={request.travel_days}, "
            f"intercity_required={bool(request.origin_city)}"
        )
        hotel_nights = self._get_hotel_nights(request)
        hotel_rooms = max(1, math.ceil(request.travelers / 2))

        # Hotel and intercity quotes are independent external calls. Running
        # them together reduces latency without adding requests.
        with ThreadPoolExecutor(max_workers=2) as executor:
            hotel_future = executor.submit(
                self._estimate_hotel,
                request,
                trip_plan,
                hotel_nights,
                hotel_rooms,
            )
            intercity_future = executor.submit(
                self._estimate_intercity_transport,
                request,
            )
            hotel_quote = hotel_future.result()
            intercity_quote = intercity_future.result()

        attraction_total = self._sum_attraction_costs(
            trip_plan, request.travelers
        )
        meal_total = self._sum_meal_costs(trip_plan, request.travelers)
        has_attractions = any(day.attractions for day in trip_plan.days)
        budget_notes: List[str] = [f"餐饮按{request.travelers}人计算。"]
        if attraction_total > 0:
            budget_notes.append(
                f"已填写的景点门票参考价按{request.travelers}人计算，"
                "实际票价和优惠政策仍需通过景区官方渠道复核。"
            )
        elif has_attractions:
            budget_notes.append(
                "景点门票当前暂按0元计入；0仅表示未取得可核验的结构化票价，"
                "不代表已确认免费，出发前需核对官方票价、免费政策和预约要求。"
            )

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
        logger.info(
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
            logger.info("[budget] hotel skipped: no overnight stay")
            return QuoteResult(
                total_price=0,
                notes=["行程未跨夜，酒店费用按 0 计算。"],
                source="not_required"
            )

        planned_hotels = self._planned_hotels(trip_plan, hotel_nights)
        planned_hotel_names = [
            hotel.name for hotel in planned_hotels if hotel.name
        ]
        unique_planned_hotel_names = list(dict.fromkeys(planned_hotel_names))

        # A single quote is valid only when all overnight entries point to the
        # same selected hotel. For hotel-switch itineraries, use the weighted
        # per-night map estimates below instead of multiplying hotel A's quote
        # across nights spent at hotel B.
        if self.flyai_enabled and len(unique_planned_hotel_names) <= 1:
            norm_dest = get_destination_feasibility_service().normalize_city(
                request.city
            )
            logger.info(
                "[budget] hotel FlyAI search: "
                "city=%r (norm=%r), nights=%d, rooms=%d, accommodation=%r, "
                "check_in=%s, check_out=%s",
                request.city, norm_dest,
                hotel_nights, hotel_rooms,
                request.accommodation,
                request.start_date, request.end_date,
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
            item = self._pick_hotel_item(
                data,
                request.accommodation,
                expected_names=unique_planned_hotel_names,
            )
            if not item:
                item = self._pick_hotel_item(
                    self._run_flyai([
                        "search-hotel",
                        "--dest-name", request.city,
                        "--check-in-date", request.start_date,
                        "--check-out-date", request.end_date,
                        "--sort", "price_asc",
                    ]),
                    request.accommodation,
                    expected_names=unique_planned_hotel_names,
                )
            if item:
                unit_price, masked = self._parse_price(item.get("price"))
                if not masked and unit_price > 0:
                    reference = self._build_hotel_reference(item)
                    logger.info(
                        "[budget] hotel FlyAI hit: "
                        f"unit={unit_price}, nights={hotel_nights}, rooms={hotel_rooms}, "
                        f"total={unit_price * hotel_nights * hotel_rooms}"
                    )
                    return QuoteResult(
                        unit_price=unit_price,
                        total_price=unit_price * hotel_nights * hotel_rooms,
                        reference=reference,
                        source="flyai_hotel",
                    )
                logger.info(
                    "[budget] hotel FlyAI price is masked or invalid; "
                    "falling back instead of presenting it as verified"
                )
            logger.info("[budget] hotel FlyAI quotes rejected: no compatible nightly rate")
        elif self.flyai_enabled:
            logger.info(
                "[budget] hotel FlyAI skipped: itinerary changes hotels; "
                "using weighted per-night map estimates"
            )

        planned_prices = [
            int(hotel.estimated_cost)
            for hotel in planned_hotels
            if int(hotel.estimated_cost or 0) > 0
        ]
        if unique_planned_hotel_names and planned_prices:
            unit_price = max(1, round(sum(planned_prices) / len(planned_prices)))
            names = "、".join(unique_planned_hotel_names[:2])
            return QuoteResult(
                unit_price=unit_price,
                total_price=unit_price * hotel_nights * hotel_rooms,
                reference=(
                    f"{names} 地图酒店参考单晚 {unit_price} 元"
                ),
                notes=[
                    "酒店单晚价与行程中实际选定酒店保持一致，"
                    "但仍属地图参考价，不是可预订的实时报价。"
                ],
                source="map_hotel_estimate",
            )

        fallback_unit = self._fallback_hotel_price(request.accommodation)
        logger.info(
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
            logger.info("[budget] intercity skipped: origin city not provided")
            return QuoteResult(
                notes=["未提供出发城市，未计算城际交通费用。"],
                source="not_provided"
            )

        feasibility = get_destination_feasibility_service()
        if (
            feasibility.normalize_city(request.origin_city)
            == feasibility.normalize_city(request.city)
        ):
            logger.info("[budget] intercity skipped: origin equals destination")
            return QuoteResult(
                notes=["出发地与目的地相同，城际交通费用按 0 计算。"],
                source="not_required"
            )

        mode = (request.intercity_transportation or "").strip().lower()
        logger.info("[budget] intercity estimate started")
        if "自驾" in mode:
            vehicle_count = max(1, math.ceil(request.travelers / 4))
            total = 400 * vehicle_count
            per_person = math.ceil(total / request.travelers)
            logger.info(
                "[budget] intercity drive fallback: "
                f"vehicles={vehicle_count}, per_person={per_person}, total={total}"
            )
            return QuoteResult(
                unit_price=per_person,
                total_price=total,
                reference=f"{request.origin_city} 往返 {request.city} 自驾估算",
                notes=[
                    f"自驾费用按{vehicle_count}辆车、每车往返约400元估算，"
                    "包含基础油费与路桥费缓冲，出发前请按车型和实时路线复核。"
                ],
                source="heuristic_drive"
            )

        if (
            feasibility.is_precise_short_trip(request.origin_city, request.city)
            and not any(keyword in mode for keyword in ("飞机", "火车", "高铁", "铁路"))
        ):
            unit_price = 160
            total = unit_price * request.travelers
            return QuoteResult(
                unit_price=unit_price,
                total_price=total,
                reference=f"{request.origin_city} 往返 {request.city} 周边短途估算",
                notes=[
                    "县域短途暂未取得实时班次价格，已按每人往返160元估算；"
                    "请在出发前比较客运、包车与自驾成本。"
                ],
                source="heuristic_short_haul",
            )

        explicit_flight = "飞机" in mode
        explicit_train = any(keyword in mode for keyword in ("火车", "高铁", "动车", "铁路"))
        if explicit_flight and not explicit_train:
            try:
                flight_quote = self._estimate_roundtrip_ticket(request, "flight")
            except Exception as exc:
                logger.info("[budget] flight roundtrip search raised %s; falling back", type(exc).__name__)
                flight_quote = QuoteResult()
            if flight_quote.total_price > 0:
                return flight_quote
            return self._fallback_mode_transport(request, "飞机", 1200)

        if explicit_train and not explicit_flight:
            try:
                train_quote = self._estimate_roundtrip_ticket(request, "train")
            except Exception as exc:
                logger.info("[budget] train roundtrip search raised %s; falling back", type(exc).__name__)
                train_quote = QuoteResult()
            if train_quote.total_price > 0:
                return train_quote
            label = "高铁/动车" if any(keyword in mode for keyword in ("高铁", "动车")) else "火车"
            return self._fallback_mode_transport(request, label, 600)

        flight_quote: QuoteResult = QuoteResult()
        train_quote: QuoteResult = QuoteResult()
        try:
            flight_quote = self._estimate_roundtrip_ticket(request, "flight")
        except Exception as exc:
            logger.info("[budget] flight roundtrip search raised %s; falling back", type(exc).__name__)
        try:
            train_quote = self._estimate_roundtrip_ticket(request, "train")
        except Exception as exc:
            logger.info("[budget] train roundtrip search raised %s; falling back", type(exc).__name__)
        candidates = [quote for quote in [flight_quote, train_quote] if quote.total_price > 0]
        if candidates:
            # Prefer fully-verified roundtrip results over partial estimates.
            fully_verified = [
                q for q in candidates
                if q.source in {"flyai_train", "flyai_flight"}
            ]
            chosen_pool = fully_verified if fully_verified else candidates
            chosen = min(chosen_pool, key=lambda quote: quote.total_price)
            if fully_verified and len(fully_verified) < len(candidates):
                chosen.notes.append(
                    "未指定城际交通方式，已优先选择双向可核验的 FlyAI 往返方案。"
                )
            else:
                chosen.notes.append(
                    "未指定城际交通方式，已自动选择当前更便宜的方案。"
                )
            chosen.notes = self._unique_notes(chosen.notes)
            return chosen

        fallback_unit = 600
        logger.info(
            "[budget] intercity fallback: "
            f"roundtrip_unit={fallback_unit}, travelers={request.travelers}, "
            f"total={fallback_unit * request.travelers}"
        )
        return QuoteResult(
            unit_price=fallback_unit,
            total_price=fallback_unit * request.travelers,
            reference=f"{request.origin_city} 往返 {request.city} 交通兜底估算",
            notes=["未获取到 FlyAI 城际交通价格，已按单人往返 600 元进行城际交通综合兜底估算。"],
            source="heuristic_transport"
        )

    def _fallback_mode_transport(
        self,
        request: TripRequest,
        label: str,
        roundtrip_unit: int,
    ) -> QuoteResult:
        total = roundtrip_unit * request.travelers
        return QuoteResult(
            unit_price=roundtrip_unit,
            total_price=total,
            reference=(
                f"{request.origin_city} 往返 {request.city} {label}兜底估算"
                "（非实时班次）"
            ),
            notes=[
                f"未获取到 FlyAI {label}实时价格，已按单人往返 "
                f"{roundtrip_unit} 元保守估算；出发前请在官方渠道核对班次和票价。"
            ],
            source="heuristic_transport",
        )

    def _estimate_roundtrip_ticket(self, request: TripRequest, ticket_type: str) -> QuoteResult:
        if not self.flyai_enabled:
            logger.info(f"[budget] {ticket_type} skipped: FlyAI disabled")
            return QuoteResult()

        command = "search-flight" if ticket_type == "flight" else "search-train"
        label = "航班" if ticket_type == "flight" else "列车"
        logger.info(
            "[budget] FlyAI roundtrip search: type=%s, "
            "origin=%r, dest=%r, out_date=%s, return_date=%s",
            ticket_type,
            request.origin_city,
            request.city,
            request.start_date,
            request.end_date,
        )
        outbound_raw = self._run_flyai([
            command,
            "--origin", request.origin_city,
            "--destination", request.city,
            "--dep-date", request.start_date,
            "--sort-type", "3",
        ])
        inbound_raw = self._run_flyai([
            command,
            "--origin", request.city,
            "--destination", request.origin_city,
            "--dep-date", request.end_date,
            "--sort-type", "3",
        ])

        outbound_item = self._pick_ticket_item(
            outbound_raw,
            ticket_type,
            request.intercity_transportation or "",
            origin_city=request.origin_city,
            destination_city=request.city,
            departure_date=request.start_date,
        )
        inbound_item = self._pick_ticket_item(
            inbound_raw,
            ticket_type,
            request.intercity_transportation or "",
            origin_city=request.city,
            destination_city=request.origin_city,
            departure_date=request.end_date,
        )

        # ── price extraction ──────────────────────────────────────────
        outbound_price = 0
        inbound_price = 0
        outbound_masked = False
        inbound_masked = False
        if outbound_item:
            outbound_price, outbound_masked = self._extract_ticket_price(
                outbound_item, ticket_type
            )
        if inbound_item:
            inbound_price, inbound_masked = self._extract_ticket_price(
                inbound_item, ticket_type
            )

        if outbound_masked or inbound_masked:
            logger.info(
                "[budget] %s FlyAI prices are masked; "
                "falling back instead of presenting a midpoint as verified",
                ticket_type,
            )
            return QuoteResult()

        if outbound_item and inbound_item:
            # ── both legs hit ──────────────────────────────────────────
            if outbound_price <= 0 or inbound_price <= 0:
                logger.info(
                    "[budget] %s FlyAI price parse failed: "
                    "outbound=%d, inbound=%d",
                    ticket_type, outbound_price, inbound_price,
                )
                return QuoteResult()
            unit_price = outbound_price + inbound_price
            reference = self._build_transport_reference(
                request.origin_city,
                request.city,
                outbound_item,
                inbound_item,
                ticket_type,
                request.start_date,
                request.end_date,
            )
            source = f"flyai_{ticket_type}"
            logger.info(
                "[budget] FlyAI %s roundtrip hit: "
                "outbound=%d, inbound=%d, unit=%d, travelers=%d, total=%d",
                ticket_type, outbound_price, inbound_price, unit_price,
                request.travelers, unit_price * request.travelers,
            )
            return QuoteResult(
                unit_price=unit_price,
                total_price=unit_price * request.travelers,
                reference=reference,
                source=source,
            )

        if outbound_item:
            # ── outbound only ───────────────────────────────────────────
            if outbound_price <= 0:
                logger.info(
                    "[budget] %s outbound price parse failed: %d",
                    ticket_type, outbound_price,
                )
                return QuoteResult()
            estimated_return = outbound_price
            unit_price = outbound_price + estimated_return
            notes: List[str] = [
                f"返程{label}暂未取得实时报价，按去程同价估算（{estimated_return} 元）；"
                "本预算不是可预订的往返票价，出发前请在官方渠道核对返程票价。"
            ]
            reference = (
                f"{self._describe_ticket_item(outbound_item, ticket_type)} "
                f"[去程 FlyAI 报价]; "
                f"{request.city}->{request.origin_city} "
                f"[{request.end_date}]: 返程按去程同价估算"
            )
            # Source label must clearly indicate this is NOT fully verified.
            source = f"flyai_{ticket_type}_partial"
            logger.info(
                "[budget] FlyAI %s outbound-only (partial estimate): "
                "outbound=%d, estimated_return=%d, unit=%d, "
                "inbound raw items=%d, inbound_reason=no_matching_item",
                ticket_type, outbound_price, estimated_return, unit_price,
                self._count_raw_items(inbound_raw),
            )
            return QuoteResult(
                unit_price=unit_price,
                total_price=unit_price * request.travelers,
                reference=reference,
                notes=notes,
                source=source,
            )

        if inbound_item:
            # ── inbound only ────────────────────────────────────────────
            if inbound_price <= 0:
                logger.info(
                    "[budget] %s inbound price parse failed: %d",
                    ticket_type, inbound_price,
                )
                return QuoteResult()
            estimated_outbound = inbound_price
            unit_price = estimated_outbound + inbound_price
            notes: List[str] = [
                f"去程{label}暂未取得实时报价，按返程同价估算（{estimated_outbound} 元）；"
                "本预算不是可预订的往返票价，出发前请在官方渠道核对去程票价。"
            ]
            reference = (
                f"{request.origin_city}->{request.city} "
                f"[{request.start_date}]: 去程按返程同价估算; "
                f"{self._describe_ticket_item(inbound_item, ticket_type)} "
                f"[返程 FlyAI 报价]"
            )
            source = f"flyai_{ticket_type}_partial"
            logger.info(
                "[budget] FlyAI %s inbound-only (partial estimate): "
                "estimated_outbound=%d, inbound=%d, unit=%d, "
                "outbound raw items=%d, outbound_reason=no_matching_item",
                ticket_type, estimated_outbound, inbound_price, unit_price,
                self._count_raw_items(outbound_raw),
            )
            return QuoteResult(
                unit_price=unit_price,
                total_price=unit_price * request.travelers,
                reference=reference,
                notes=notes,
                source=source,
            )

        # ── both legs missed ────────────────────────────────────────────
        logger.info(
            "[budget] %s FlyAI miss: outbound=%s, inbound=%s, "
            "outbound_raw_items=%d, inbound_raw_items=%d",
            ticket_type,
            bool(outbound_item), bool(inbound_item),
            self._count_raw_items(outbound_raw),
            self._count_raw_items(inbound_raw),
        )
        return QuoteResult()

    def _count_raw_items(self, data: Any) -> int:
        """Return the number of raw items in a FlyAI response (for logging)."""
        if not isinstance(data, dict):
            return -1  # signal: response was not even a JSON object
        container = data.get("data")
        if not isinstance(container, dict):
            return -2  # signal: missing nested 'data'
        items = container.get("itemList")
        if isinstance(items, list):
            return len(items)
        return -3  # signal: itemList is missing or wrong type

    def _run_flyai(self, arguments: Sequence[str]) -> Any:
        # Return type is Any (not Dict[str, Any]) because json.loads()
        # can return any JSON value — list, dict, str, int, float, bool,
        # or None.  Every caller MUST guard isinstance(data, dict) before
        # calling .get() on the result.
        api_key = str(getattr(self.settings, "flyai_api_key", "") or "")
        if not self.flyai_enabled or not self.flyai_command or not api_key:
            logger.info("[budget] FlyAI skipped: disabled, missing key, or command not configured")
            return {}

        command = [*self.flyai_command, *arguments]
        operation = (
            str(arguments[0])
            if arguments and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", str(arguments[0]))
            else "unknown"
        )
        # Log query parameters at INFO (sanitized — only user-supplied
        # location/date values, never API keys or tokens).
        param_summary = self._summarize_flyai_args(arguments)
        logger.info(
            "[budget] FlyAI request: operation=%s, %s",
            operation, param_summary,
        )
        env = self._build_subprocess_env(api_key)
        try:
            # Keep the third-party CLI away from the application working
            # directory, where dotenv files commonly contain unrelated keys.
            with tempfile.TemporaryDirectory(prefix="travel-budget-") as isolated_cwd:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=self.settings.flyai_timeout,
                    check=False,
                    env=env,
                    cwd=isolated_cwd,
                )
        except Exception as exc:
            logger.info(
                "[budget] FlyAI command failed: operation=%s, error=%s",
                operation, type(exc).__name__,
            )
            return {}

        if result.returncode != 0 or not result.stdout.strip():
            logger.info(
                "[budget] FlyAI no usable response: "
                "operation=%s, returncode=%d, stdout_empty=%s",
                operation,
                result.returncode,
                not bool(result.stdout.strip()),
            )
            return {}

        try:
            data = self._safe_json_loads(result.stdout.strip())
            item_count = self._count_raw_items(data)
            if item_count >= 0:
                logger.info(
                    "[budget] FlyAI response: operation=%s, items=%d",
                    operation, item_count,
                )
            else:
                logger.info(
                    "[budget] FlyAI response: operation=%s, type=%s",
                    operation,
                    type(data).__name__,
                )
            return data
        except Exception as exc:
            logger.info(
                "[budget] FlyAI JSON parse failed: operation=%s, error=%s",
                operation, type(exc).__name__,
            )
            return {}

    def _summarize_flyai_args(self, arguments: Sequence[str]) -> str:
        """Return a sanitized key=value summary of FlyAI CLI arguments.

        Only user-supplied location, date and filter parameters are logged.
        API keys, tokens, and secrets are never present in *arguments* —
        they are injected via environment variable in the subprocess call.
        """
        known_flags = {
            "--dest-name", "--poi-name",
            "--check-in-date", "--check-out-date",
            "--origin", "--destination", "--dep-date",
            "--sort", "--sort-type",
            "--max-price", "--hotel-types", "--hotel-stars",
        }
        parts: List[str] = []
        arg_list = list(arguments)
        i = 1  # skip the sub-command name (index 0)
        while i < len(arg_list):
            flag = arg_list[i]
            if flag in known_flags and i + 1 < len(arg_list):
                value = arg_list[i + 1]
                parts.append(f"{flag}={value}")
                i += 2
            else:
                i += 1
        return ", ".join(parts) if parts else "no parameters logged"

    def _planned_hotels(
        self,
        trip_plan: TripPlan,
        hotel_nights: int,
    ) -> List[Any]:
        # Keep one entry per overnight date. Deduplicating by POI would lose
        # frequency information and produce the wrong weighted nightly rate
        # when one hotel is used for two nights and another for one night.
        return [
            day.hotel
            for day in trip_plan.days[:hotel_nights]
            if day.hotel is not None
        ]

    def _hotel_names_match(self, expected: str, actual: str) -> bool:
        normalized_expected = re.sub(r"[\W_]+", "", expected or "").casefold()
        normalized_actual = re.sub(r"[\W_]+", "", actual or "").casefold()
        return bool(
            normalized_expected
            and normalized_actual
            and (
                normalized_expected in normalized_actual
                or normalized_actual in normalized_expected
            )
        )

    def _pick_hotel_item(
        self,
        data: Dict[str, Any],
        accommodation: str,
        expected_names: Optional[Sequence[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        # FlyAI may return a JSON array (list) instead of an object in edge
        # cases such as empty result sets or internal errors.  Guard against
        # that to avoid AttributeError on .get().
        if not isinstance(data, dict):
            logger.info("[budget] hotel FlyAI response is not a JSON object; skipping")
            return None
        container = data.get("data")
        if not isinstance(container, dict):
            logger.info("[budget] hotel FlyAI response missing nested 'data' object; skipping")
            return None
        items = container.get("itemList")
        if not isinstance(items, list):
            logger.info(
                "[budget] hotel FlyAI response has no itemList; "
                "keys=%s",
                list(container.keys())[:5] if isinstance(container, dict) else "n/a",
            )
            return None
        price_floor = self._hotel_unit_price_floor(accommodation)
        total = 0
        rejected_price_floor = 0
        rejected_masked = 0
        rejected_accommodation = 0
        rejected_name = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            total += 1
            item_name = str(item.get("name") or "?")
            item_price = item.get("price")
            unit_price, masked = self._parse_price(item_price)
            if masked:
                rejected_masked += 1
                logger.debug(
                    "[budget] hotel candidate rejected: name=%r, "
                    "reason=masked_price, raw_price=%r",
                    item_name, item_price,
                )
                continue
            if unit_price < price_floor:
                rejected_price_floor += 1
                logger.debug(
                    "[budget] hotel candidate rejected: name=%r, "
                    "reason=below_price_floor, price=%d, floor=%d",
                    item_name, unit_price, price_floor,
                )
                continue
            if not self._hotel_matches_accommodation(accommodation, item):
                rejected_accommodation += 1
                logger.debug(
                    "[budget] hotel candidate rejected: name=%r, "
                    "reason=accommodation_type_mismatch, accommodation=%r",
                    item_name, accommodation,
                )
                continue
            if expected_names and not any(
                self._hotel_names_match(expected, str(item.get("name") or ""))
                for expected in expected_names
            ):
                rejected_name += 1
                logger.debug(
                    "[budget] hotel candidate rejected: name=%r, "
                    "reason=name_mismatch, expected=%s",
                    item_name, expected_names,
                )
                continue
            # First compatible candidate wins (results are price-ascending).
            logger.info(
                "[budget] hotel FlyAI hit: name=%r, price=%d, "
                "total_candidates=%d, "
                "rejected=(price_floor=%d, masked=%d, accommodation=%d, name=%d)",
                item_name, unit_price, total,
                rejected_price_floor, rejected_masked,
                rejected_accommodation, rejected_name,
            )
            return item

        # No candidate survived.
        logger.info(
            "[budget] hotel FlyAI all %d candidates rejected: "
            "price_floor=%d, masked=%d, accommodation=%d, name=%d",
            total, rejected_price_floor, rejected_masked,
            rejected_accommodation, rejected_name,
        )
        return None

    def _pick_ticket_item(
        self,
        data: Dict[str, Any],
        ticket_type: str,
        requested_mode: str,
        *,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        departure_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        # FlyAI may return a JSON array (list) instead of an object in edge
        # cases such as empty result sets or internal errors.  Guard against
        # that to avoid AttributeError on .get().
        if not isinstance(data, dict):
            logger.info("[budget] %s FlyAI response is not a JSON object; skipping", ticket_type)
            return None
        container = data.get("data")
        if not isinstance(container, dict):
            logger.info("[budget] %s FlyAI response missing nested 'data' object; skipping", ticket_type)
            return None
        items = container.get("itemList")
        if not isinstance(items, list):
            logger.info(
                "[budget] %s FlyAI response has no itemList; "
                "keys=%s",
                ticket_type,
                list(container.keys())[:5] if isinstance(container, dict) else "n/a",
            )
            return None
        candidates = [item for item in items if isinstance(item, dict)]
        raw_count = len(candidates)
        if origin_city and destination_city and departure_date:
            norm_origin = self._normalize_location_name(origin_city)
            norm_dest = self._normalize_location_name(destination_city)
            logger.debug(
                "[budget] %s filtering %d raw items: "
                "origin=%r (norm=%r), dest=%r (norm=%r), date=%r",
                ticket_type, raw_count,
                origin_city, norm_origin,
                destination_city, norm_dest,
                departure_date,
            )
            candidates = [
                item
                for item in candidates
                if self._ticket_item_matches_request(
                    item,
                    origin_city,
                    destination_city,
                    departure_date,
                )
            ]
            # Also filter out unreasonable flight itineraries (extreme
            # detours, excessive transfers for short trips).
            if ticket_type == "flight" and candidates:
                candidates = [
                    item
                    for item in candidates
                    if not self._flight_itinerary_unreasonable(
                        item, origin_city, destination_city,
                    )
                ]
        matched_count = len(candidates)
        if raw_count > 0 and matched_count == 0:
            # Collect one sample destination to aid debugging.
            sample_destinations = self._sample_destinations(items, ticket_type)
            logger.info(
                "[budget] %s all %d items filtered out: "
                "origin=%r, dest=%r, date=%r, sample_destinations=%s",
                ticket_type, raw_count,
                origin_city, destination_city, departure_date,
                sample_destinations,
            )

        if ticket_type != "train" or not any(
            marker in requested_mode for marker in ("高铁", "动车")
        ):
            if candidates:
                logger.debug(
                    "[budget] %s selected first of %d candidates",
                    ticket_type, matched_count,
                )
            return candidates[0] if candidates else None

        for item in candidates:
            if self._is_high_speed_train_item(item):
                logger.debug(
                    "[budget] %s selected high-speed train from %d candidates",
                    ticket_type, matched_count,
                )
                return item
        if matched_count > 0:
            logger.info(
                "[budget] %s no high-speed train in %d matched candidates; "
                "requested_mode=%r",
                ticket_type, matched_count, requested_mode,
            )
        return None

    def _ticket_item_matches_request(
        self,
        item: Dict[str, Any],
        origin_city: str,
        destination_city: str,
        departure_date: str,
    ) -> bool:
        segments = self._ticket_segments(item)
        if not segments:
            logger.debug(
                "[budget] ticket item rejected: no segments in journeys"
            )
            return False
        journeys = item.get("journeys")
        first_journey = (
            journeys[0]
            if isinstance(journeys, list)
            and journeys
            and isinstance(journeys[0], dict)
            else {}
        )
        first_segment = segments[0]
        last_segment = segments[-1]

        # Collect every plausible origin / destination field value across
        # the segment, journey and item levels.  A single FlyAI response
        # shape must not decide whether the ticket matches — as long as
        # *any* candidate field matches, we accept the item.
        origin_candidates = self._collect_location_fields(
            (first_segment, first_journey, item),
            (
                # Journey-level & standard schema (highest priority).
                "departureCityName",
                "originCityName",
                "departureStationName",
                "originStationName",
                # Abbreviated variants used by FlyAI CLI (dep/arr prefix).
                "depCityName",
                "depStationName",
                "depAirportName",
                "depStationCode",
                "departureStation",
                "originStation",
                # Fallback generic names.
                "origin",
                "from",
                "departure",
            ),
        )
        dest_candidates = self._collect_location_fields(
            (last_segment, first_journey, item),
            (
                # Journey-level & standard schema.
                "arrivalCityName",
                "destinationCityName",
                "arrivalStationName",
                "destinationStationName",
                # Abbreviated variants (FlyAI CLI).
                "arrCityName",
                "arrStationName",
                "arrAirportName",
                "arrStationCode",
                "arrivalStation",
                "destinationStation",
                # Fallback.
                "destination",
                "to",
                "arrival",
            ),
        )

        origin_match = any(
            self._location_matches(origin_city, candidate)
            for candidate in origin_candidates
        )
        dest_match = any(
            self._location_matches(destination_city, candidate)
            for candidate in dest_candidates
        )

        if not origin_match or not dest_match:
            logger.debug(
                "[budget] ticket item rejected: location mismatch "
                f"origin_candidates={origin_candidates}, "
                f"dest_candidates={dest_candidates}, "
                f"expected_origin={origin_city!r}, "
                f"expected_dest={destination_city!r}"
            )
            return False

        actual_date = self._ticket_field_text(
            (first_segment, first_journey, item),
            (
                "departureDate",
                "depDate",
                "depDateTime",
                "departureDateTime",
                "departureTime",
                "depTime",
                "startDate",
                "startTime",
                "date",
            ),
        )
        date_ok = self._date_matches(departure_date, actual_date)
        if not date_ok:
            logger.debug(
                "[budget] ticket item rejected: date mismatch "
                f"expected={departure_date!r}, actual={actual_date!r}"
            )
        return date_ok

    def _flight_itinerary_unreasonable(
        self,
        item: Dict[str, Any],
        origin_city: str,
        destination_city: str,
    ) -> bool:
        """Return True if a flight itinerary is clearly unreasonable.

        Checks segment count and total duration.  Extreme detours (e.g.
        Taiyuan → Shanghai → Wutaishan) are detected via excessive total
        duration rather than character-based city-name heuristics.
        """
        segments = self._ticket_segments(item)
        if not segments:
            return False  # can't assess, allow through

        # Single-segment direct flight: always reasonable.
        if len(segments) <= 1:
            return False

        # Multi-segment (transfer) flight: aggregate total duration
        # across all journeys.  FlyAI returns this as a string ("1525"),
        # not an integer.
        total_duration_min = 0
        for journey in item.get("journeys") or []:
            if not isinstance(journey, dict):
                continue
            td = journey.get("totalDuration")
            try:
                td_int = int(td) if td not in (None, "") else 0
            except (ValueError, TypeError):
                td_int = 0
            if td_int > 0:
                total_duration_min += td_int

        # Domestic two-segment itinerary exceeding 12 hours is a detour,
        # not a reasonable connection.
        if total_duration_min > 720:
            logger.info(
                "[budget] flight rejected as unreasonable: "
                "segments=%d, total_duration_min=%d",
                len(segments), total_duration_min,
            )
            return True

        # Three or more segments for a domestic short/medium-haul route
        # is excessive regardless of total time.
        if len(segments) >= 3:
            logger.info(
                "[budget] flight rejected as unreasonable: "
                "segments=%d (too many transfers)",
                len(segments),
            )
            return True

        return False

    def _sample_destinations(
        self,
        items: list,
        ticket_type: str,
    ) -> list[str]:
        """Return up to 3 distinct destination station/city names for logging."""
        results: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            segments = self._ticket_segments(item)
            for seg in segments[-2:]:  # last 2 segments — closest to arrival
                for field in ("arrStationName", "arrCityName",
                              "arrStationShortName"):
                    val = str(seg.get(field) or "").strip()
                    if val and val.casefold() not in seen:
                        seen.add(val.casefold())
                        results.append(val)
            if len(results) >= 3:
                break
        return results

    def _collect_location_fields(
        self,
        mappings: Sequence[Dict[str, Any]],
        keys: Sequence[str],
    ) -> List[str]:
        """Return every non-empty value found for *keys* across *mappings*.

        Unlike :meth:`_ticket_field_text` which stops at the first hit, this
        collector gathers all candidates so that a match can succeed from
        *any* level (segment station name, journey city name, item-level
        origin, …).
        """
        results: List[str] = []
        seen: set[str] = set()
        normalized_mappings = [
            {str(key).casefold(): value for key, value in mapping.items()}
            for mapping in mappings
            if isinstance(mapping, dict)
        ]
        for preferred_key in keys:
            normalized_key = preferred_key.casefold()
            for mapping in normalized_mappings:
                raw_value = mapping.get(normalized_key)
                if raw_value is None:
                    continue
                text: Optional[str] = None
                if isinstance(raw_value, dict):
                    for nested_key in (
                        "name",
                        "cityName",
                        "stationName",
                        "value",
                        "text",
                    ):
                        nested = raw_value.get(nested_key)
                        if nested and str(nested).strip():
                            text = str(nested).strip()
                            break
                elif raw_value not in (None, ""):
                    text = str(raw_value).strip()
                if text and text.casefold() not in seen:
                    seen.add(text.casefold())
                    results.append(text)
        return results

    def _ticket_field_text(
        self,
        mappings: Sequence[Dict[str, Any]],
        keys: Sequence[str],
    ) -> str:
        normalized_mappings = [
            {str(key).casefold(): value for key, value in mapping.items()}
            for mapping in mappings
            if isinstance(mapping, dict)
        ]
        # Respect caller-defined field priority across every schema level.
        # JSON object insertion order must not make a time-only field win over
        # an available full departure date-time.
        for preferred_key in keys:
            normalized_key = preferred_key.casefold()
            for mapping in normalized_mappings:
                if normalized_key not in mapping:
                    continue
                raw_value = mapping[normalized_key]
                if isinstance(raw_value, dict):
                    for nested_key in (
                        "name",
                        "cityName",
                        "stationName",
                        "value",
                        "text",
                    ):
                        nested = raw_value.get(nested_key)
                        if nested:
                            return str(nested).strip()
                elif raw_value not in (None, ""):
                    return str(raw_value).strip()
        return ""

    _LOCATION_NORM_CACHE: dict[str, str] = {}

    def _normalize_location_name(self, raw: Optional[str]) -> str:
        """Return a stripped-down core name for matching city ↔ station.

        Delegates to the shared ``DestinationFeasibilityService``
        implementation so all services use the same normalization rules.
        """
        value = " ".join(str(raw or "").split())
        if not value:
            return ""
        cache_key = value.casefold()
        cached = self._LOCATION_NORM_CACHE.get(cache_key)
        if cached is not None:
            return cached
        feasibility = get_destination_feasibility_service()
        result = feasibility.normalize_location_for_matching(raw)
        self._LOCATION_NORM_CACHE[cache_key] = result
        return result

    def _location_matches(self, expected: str, actual: str) -> bool:
        """Return True when *expected* and *actual* represent the same city.

        Both sides are normalised independently (province prefix, city suffix,
        station suffix) before comparison.  This allows ``山西太原`` to match
        ``太原站``, ``太原南站``, or ``太原``.
        """
        norm_expected = self._normalize_location_name(expected)
        norm_actual = self._normalize_location_name(actual)

        if not norm_expected or not norm_actual:
            return False

        # Exact match after normalization.
        if norm_expected.casefold() == norm_actual.casefold():
            return True

        # Substring containment — handles cases like "五台山" ↔ "五台山风景区".
        return (
            norm_expected.casefold() in norm_actual.casefold()
            or norm_actual.casefold() in norm_expected.casefold()
        )

    def _date_matches(self, expected: str, actual: str) -> bool:
        expected_digits = re.sub(r"\D", "", expected or "")
        match = re.search(r"(20\d{2})[-/]?(\d{2})[-/]?(\d{2})", actual or "")
        return bool(
            len(expected_digits) == 8
            and match
            and "".join(match.groups()) == expected_digits
        )

    def _is_high_speed_train_item(self, item: Dict[str, Any]) -> bool:
        segments = self._ticket_segments(item)
        if not segments:
            return False
        has_high_speed_evidence = False
        for segment in segments:
            train_no = str(segment.get("marketingTransportNo") or "").strip().upper()
            train_name = str(segment.get("marketingTransportName") or "")
            if train_no:
                match = re.match(r"^([A-Z])\s*\d", train_no)
                if not match or match.group(1) not in {"G", "D", "C"}:
                    return False
                has_high_speed_evidence = True
                continue
            if any(marker in train_name for marker in ("高铁", "动车", "城际")):
                has_high_speed_evidence = True
            else:
                return False
        return has_high_speed_evidence

    def _ticket_segments(self, item: Dict[str, Any]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        journeys = item.get("journeys")
        if not isinstance(journeys, list):
            return result
        for journey in journeys:
            if not isinstance(journey, dict):
                continue
            segments = journey.get("segments")
            if not isinstance(segments, list):
                continue
            result.extend(segment for segment in segments if isinstance(segment, dict))
        return result

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

        # Experience-mode and redacted prices are not bookable evidence. Do
        # not let alternative masks such as ``2**`` or ``2??`` fall through
        # to the generic number parser as a real 2-yuan quote.
        if re.search(r"[xX*＊?？]", text):
            return 0, True

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

    def _sum_attraction_costs(
        self,
        trip_plan: TripPlan,
        travelers: int = 1,
    ) -> int:
        single_person_total = 0
        for day in trip_plan.days:
            for attraction in day.attractions:
                single_person_total += max(0, int(attraction.ticket_price or 0))
        return single_person_total * max(1, travelers)

    def _sum_meal_costs(
        self,
        trip_plan: TripPlan,
        travelers: int = 1,
    ) -> int:
        single_person_total = 0
        for day in trip_plan.days:
            for meal in day.meals:
                single_person_total += max(0, int(meal.estimated_cost or 0))
        return single_person_total * max(1, travelers)

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

    def _hotel_unit_price_floor(self, accommodation: str) -> int:
        if "豪华" in accommodation:
            return 400
        if "舒适" in accommodation or "亲子" in accommodation:
            return 180
        if "民宿" in accommodation:
            return 100
        if "经济" in accommodation:
            return 100
        return 120

    def _hotel_matches_accommodation(
        self,
        accommodation: str,
        item: Dict[str, Any],
    ) -> bool:
        name = str(item.get("name") or "")
        explicitly_accepts_hostel = any(
            marker in accommodation for marker in ("青年旅舍", "青旅", "床位")
        )
        if not explicitly_accepts_hostel and any(
            marker in name
            for marker in (
                "青年旅舍", "青年旅社", "青旅", "床位",
                "钟点房", "小时房", "日租房",
            )
        ):
            return False
        return True

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
        ticket_type: str,
        outbound_date: str,
        inbound_date: str,
    ) -> str:
        outbound = self._describe_ticket_item(outbound_item, ticket_type)
        inbound = self._describe_ticket_item(inbound_item, ticket_type)
        return (
            f"{origin_city}->{destination_city} [{outbound_date}]: {outbound}; "
            f"{destination_city}->{origin_city} [{inbound_date}]: {inbound}"
        )

    def _describe_ticket_item(self, item: Dict[str, Any], ticket_type: str) -> str:
        journeys = item.get("journeys")
        segment: Dict[str, Any] = {}
        first_journey: Dict[str, Any] = {}
        if isinstance(journeys, list) and journeys:
            journey = journeys[0]
            if isinstance(journey, dict):
                first_journey = journey
                segments = first_journey.get("segments")
                if isinstance(segments, list) and segments:
                    first_segment = segments[0]
                    if isinstance(first_segment, dict):
                        segment = first_segment

        segments = self._ticket_segments(item)
        first_segment = segments[0] if segments else segment
        last_segment = segments[-1] if segments else segment
        origin = self._ticket_field_text(
            (first_segment, first_journey, item),
            (
                "departureStationName",
                "originStationName",
                "departureCityName",
                "originCityName",
                "depCityName",
                "depStationName",
                "departureStation",
                "originStation",
                "origin",
                "from",
                "departure",
            ),
        )
        destination = self._ticket_field_text(
            (last_segment, first_journey, item),
            (
                "arrivalStationName",
                "destinationStationName",
                "arrivalCityName",
                "destinationCityName",
                "arrCityName",
                "arrStationName",
                "arrivalStation",
                "destinationStation",
                "destination",
                "to",
                "arrival",
            ),
        )
        departure = self._ticket_field_text(
            (first_segment, first_journey, item),
            (
                "departureDateTime",
                "departureDate",
                "depDate",
                "depDateTime",
                "startDate",
                "date",
                "departureTime",
                "depTime",
                "startTime",
            ),
        )
        transport_no = str(first_segment.get("marketingTransportNo") or "")
        transport_name = str(
            first_segment.get("marketingTransportName")
            or ("航班" if ticket_type == "flight" else "列车")
        )
        seat = str(first_segment.get("seatClassName") or "")
        price = item.get("ticketPrice") if ticket_type == "flight" else item.get("price")

        parts: List[str] = []
        if origin and destination:
            parts.append(f"{origin}->{destination}")
        if departure:
            parts.append(departure)
        parts.append(transport_name)
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
        elif hotel_source == "map_hotel_estimate":
            source_labels.append("地图酒店参考价")
        elif hotel_source not in {"", "not_required"}:
            source_labels.append("酒店兜底估算")

        if transport_source in {"flyai_train", "flyai_flight"}:
            source_labels.append("FlyAI 往返报价")
        elif "_partial" in transport_source:
            # e.g. flyai_train_partial → only one leg was verified.
            source_labels.append("FlyAI 单程报价 + 单程估算")
        elif transport_source not in {"", "not_required", "not_provided"}:
            source_labels.append("城际交通兜底估算")

        source_labels.append("市内交通规则估算")
        return " + ".join(self._unique_notes(source_labels))

    def _build_subprocess_env(self, api_key: str) -> Dict[str, str]:
        allowed_names = {
            "path", "pathext", "systemroot", "windir", "comspec",
            "temp", "tmp", "home", "userprofile", "homedrive", "homepath",
            "appdata", "localappdata", "programfiles", "programfiles(x86)",
            "programdata", "lang", "lc_all", "term",
        }
        env = {
            key: value
            for key, value in os.environ.items()
            if key.casefold() in allowed_names and value
        }
        env["FLYAI_API_KEY"] = api_key
        return env

    def _split_command(self, command: str) -> List[str]:
        if not command:
            return []
        parts = shlex.split(command, posix=False)
        if not parts:
            return []
        parts = [
            self._FLYAI_PINNED_PACKAGE
            if part == self._FLYAI_UNPINNED_PACKAGE
            else part
            for part in parts
        ]
        resolved = shutil.which(parts[0])
        if resolved:
            parts[0] = resolved
        return parts

    def _safe_json_loads(self, text: str) -> Any:
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
