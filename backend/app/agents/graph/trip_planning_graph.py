"""Performance-aware LangGraph coordinator for trip generation.

The normal path makes exactly one primary planning-model call.  A repair
model is activated only when that response cannot be parsed into the required
TripPlan structure.  Map, weather, route, budget and quality stages remain
deterministic service nodes instead of additional free-form LLM agents.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, TypedDict

from ...models.schemas import (
    Attraction,
    DayPlan,
    Meal,
    TripPlan,
    TripPlanQualityIssue,
    TripPlanQualityResult,
    TripRequest,
    VerificationMeta,
)
from ...services.trip_plan_quality_service import (
    get_trip_plan_quality_service,
    refresh_quality_gate,
)
from ...services.trip_generation_errors import TripGenerationCancelledError


logger = logging.getLogger(__name__)


class TripPlanningState(TypedDict, total=False):
    request: TripRequest
    progress_callback: Optional[Callable[..., None]]
    attraction_pois: list
    attraction_response: str
    source_weather: list
    weather_response: str
    hotel_pois: list
    hotel_response: str
    planner_response: str
    trip_plan: TripPlan
    context_ready: bool
    model_ready: bool
    parsed: bool
    repairable: bool
    grounded: bool
    runtime: dict[str, Any]
    discovery_errors: list[str]
    enrichment_errors: list[str]
    failure_stage: str
    failure_type: str
    # ── quality repair tracking ──
    repair_count: int
    repaired_issue_codes: set[str]
    best_quality_score: int
    best_trip_plan: Optional[TripPlan]
    dirty_enrichments: set[str]


class TripPlanningAgentGraph:
    """Coordinate specialized trip-planning roles with conditional recovery."""

    def __init__(self, planner: Any) -> None:
        self.planner = planner
        self.graph_available, self._compiled_graph = self._try_build_langgraph()

    def _try_build_langgraph(self):
        try:
            from langgraph.graph import END, StateGraph

            graph = StateGraph(TripPlanningState)
            graph.add_node("discovery", self._discovery_node)
            graph.add_node("compose", self._compose_node)
            graph.add_node("parse", self._parse_node)
            graph.add_node("repair", self._repair_node)
            graph.add_node("ground", self._ground_node)
            graph.add_node("fallback", self._fallback_node)
            graph.add_node("enrich", self._enrich_node)
            graph.add_node("quality", self._quality_node)
            graph.add_node("repair_quality", self._repair_quality_node)
            graph.add_node("refresh_enrichment", self._refresh_enrichment_node)
            graph.set_entry_point("discovery")
            graph.add_conditional_edges(
                "discovery",
                self._after_discovery,
                {"compose": "compose", "fallback": "fallback"},
            )
            graph.add_conditional_edges(
                "compose",
                self._after_compose,
                {"parse": "parse", "fallback": "fallback"},
            )
            graph.add_conditional_edges(
                "parse",
                self._after_parse,
                {"ground": "ground", "repair": "repair", "fallback": "fallback"},
            )
            graph.add_conditional_edges(
                "repair",
                self._after_repair,
                {"ground": "ground", "fallback": "fallback"},
            )
            graph.add_conditional_edges(
                "ground",
                self._after_ground,
                {"enrich": "enrich", "fallback": "fallback"},
            )
            graph.add_edge("fallback", "enrich")
            graph.add_edge("enrich", "quality")
            # quality → after_quality → [repair_quality | END]
            graph.add_conditional_edges(
                "quality",
                self._after_quality,
                {
                    "repair_quality": "repair_quality",
                    "end": END,
                },
            )
            # repair_quality → refresh_enrichment → quality
            graph.add_edge("repair_quality", "refresh_enrichment")
            graph.add_edge("refresh_enrichment", "quality")
            return True, graph.compile()
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            logger.info(f"[trip_graph] LangGraph unavailable: {type(exc).__name__}")
            return False, None

    def run(
        self,
        request: TripRequest,
        progress_callback: Optional[Callable[..., None]] = None,
    ) -> TripPlan:
        # ``runtime`` is allocated per invocation. LangGraph may copy the
        # top-level state between nodes, while this nested checkpoint remains
        # available if the framework raises after a successful node.
        runtime: dict[str, Any] = {"checkpoint": {}, "completed": set()}
        initial: TripPlanningState = {
            "request": request,
            "progress_callback": progress_callback,
            "context_ready": False,
            "model_ready": False,
            "parsed": False,
            "repairable": False,
            "grounded": False,
            "runtime": runtime,
            # quality repair tracking
            "repair_count": 0,
            "repaired_issue_codes": set(),
            "best_quality_score": 0,
            "best_trip_plan": None,
            "dirty_enrichments": set(),
        }
        self._check_cancelled(initial)
        if self._compiled_graph is None:
            return self._run_sequential(initial)
        try:
            result = self._compiled_graph.invoke(initial)
            return self._select_final_plan(result)
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            # Never repeat the expensive primary call after a framework
            # failure. Resume deterministic work from the latest completed
            # node, or use the verified-map fallback if no plan was saved.
            logger.info(f"[trip_graph] execution failed: {type(exc).__name__}")
            return self._recover_from_checkpoint(initial)

    def _run_sequential(self, initial: TripPlanningState) -> TripPlan:
        state = dict(initial)
        state.update(self._discovery_node(state))
        if state.get("context_ready"):
            state.update(self._compose_node(state))
        if state.get("model_ready"):
            state.update(self._parse_node(state))
            if not state.get("parsed") and state.get("repairable"):
                state.update(self._repair_node(state))
        if state.get("parsed"):
            state.update(self._ground_node(state))
        if not state.get("grounded"):
            state.update(self._fallback_node(state))
        state.update(self._enrich_node(state))
        state.update(self._quality_node(state))
        # Sequential quality repair loop (same routing as the graph edges).
        for _ in range(self._MAX_QUALITY_REPAIRS):
            if self._after_quality(state) == "end":
                break
            state.update(self._repair_quality_node(state))
            state.update(self._refresh_enrichment_node(state))
            state.update(self._quality_node(state))
        return self._select_final_plan(state)

    @staticmethod
    def _plan_rank(plan: Optional[TripPlan]) -> tuple[int, int, int]:
        """Order plans by deliverability first, score second.

        Score alone is not a total order over "goodness": a repair round can
        raise the score while introducing a blocking issue, so ranking on the
        number would let a rejected plan displace a deliverable one.
        """
        quality = getattr(plan, "quality", None) if plan is not None else None
        if quality is None:
            return (-1, -1, -1)
        not_blocked = int(
            str(getattr(quality, "quality_status", "blocked")) != "blocked"
        )
        no_errors = int(
            not any(issue.severity == "error" for issue in quality.issues)
        )
        return (not_blocked, no_errors, int(quality.score))

    @classmethod
    def _select_final_plan(cls, state: TripPlanningState) -> TripPlan:
        """Pick the final answer, restoring the best repair-round snapshot.

        The repair loop can legitimately end on a plan that ranks lower than
        an earlier round; in that case the tracked best snapshot (including
        its own quality report) is returned instead.
        """
        plan = state.get("trip_plan")
        best = state.get("best_trip_plan")
        if isinstance(plan, TripPlan) and isinstance(best, TripPlan):
            if cls._plan_rank(best) > cls._plan_rank(plan):
                return best
        if isinstance(plan, TripPlan):
            return plan
        raise RuntimeError("trip graph returned no plan")

    def _recover_from_checkpoint(self, initial: TripPlanningState) -> TripPlan:
        """Finish safely after a late LangGraph/framework failure.

        The primary model is never called here. A response already produced
        by that model may still be parsed, grounded and enriched exactly once.
        """
        self._check_cancelled(initial)
        runtime = initial.get("runtime") or {}
        checkpoint = runtime.get("checkpoint")
        completed = runtime.get("completed")
        state = dict(initial)
        if isinstance(checkpoint, dict):
            state.update(checkpoint)
        completed_stages = completed if isinstance(completed, set) else set()

        plan = state.get("trip_plan")
        if (
            "quality" in completed_stages
            and isinstance(plan, TripPlan)
            and isinstance(plan.quality, TripPlanQualityResult)
        ):
            return self._select_final_plan(state)

        if not state.get("parsed") and state.get("model_ready"):
            state.update(self._parse_node(state))
            if not state.get("parsed") and state.get("repairable"):
                state.update(self._repair_node(state))

        if state.get("parsed") and not state.get("grounded"):
            state.update(self._ground_node(state))

        if not state.get("grounded") or not isinstance(state.get("trip_plan"), TripPlan):
            state.update(self._fallback_node(state))

        if "enrich" not in completed_stages:
            state.update(self._enrich_node(state))
        if "quality" not in completed_stages:
            state.update(self._quality_node(state))
        return self._select_final_plan(state)

    def _discovery_node(self, state: TripPlanningState) -> dict:
        request = state["request"]
        self._progress(state, "initialized", 5, "正在理解旅行需求")
        discovery_errors: list[str] = []
        attraction_pois: list = []
        source_weather: list = []
        attraction_ready = False

        # Weather is independent from attraction discovery. Running it in one
        # helper thread overlaps network latency without multiplying the
        # already-parallel attraction-search worker pool.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="trip-weather") as executor:
            weather_future = executor.submit(
                self.planner.amap_service.get_weather,
                request.city,
                request.start_date,
                request.end_date,
            )
            try:
                attraction_pois = self.planner._search_attractions(request) or []
                self._check_cancelled(state)
                attraction_ready = True
            except TripGenerationCancelledError:
                raise
            except Exception as exc:
                logger.info(f"[trip_graph] attraction discovery failed: {type(exc).__name__}")
                discovery_errors.append(f"attractions:{type(exc).__name__}")
            try:
                source_weather = weather_future.result() or []
                self._check_cancelled(state)
            except TripGenerationCancelledError:
                raise
            except Exception as exc:
                logger.info(f"[trip_graph] weather discovery failed: {type(exc).__name__}")
                discovery_errors.append(f"weather:{type(exc).__name__}")

        try:
            self._check_cancelled(state)
            attraction_response = self.planner._format_pois_for_prompt(
                "高德景点搜索结果",
                attraction_pois,
                limit=min(20, max(12, request.travel_days * 3)),
            )
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            logger.info(f"[trip_graph] attraction formatting failed: {type(exc).__name__}")
            discovery_errors.append(f"attraction_format:{type(exc).__name__}")
            attraction_response = "高德景点搜索结果：暂不可用。"
            attraction_ready = False
        self._progress(
            state,
            "attractions",
            22,
            "已完成景点与兴趣点检索",
            f"从地图数据中筛选出 {len(attraction_pois)} 个候选地点。",
            {"candidate_count": len(attraction_pois)},
        )

        try:
            self._check_cancelled(state)
            weather_response = self.planner._format_weather_for_prompt(
                request, source_weather
            )
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            logger.info(f"[trip_graph] weather formatting failed: {type(exc).__name__}")
            discovery_errors.append(f"weather_format:{type(exc).__name__}")
            weather_response = f"天气服务暂未提供{request.city}行程日期内的数据。"
        self._progress(
            state,
            "weather",
            34,
            "已完成天气与出行条件检查",
            f"获得 {len(source_weather)} 天与行程日期匹配的天气数据。",
            {"forecast_days": len(source_weather)},
        )

        hotel_pois: list = []
        try:
            hotel_pois = self.planner._search_hotels(request, attraction_pois) or []
            self._check_cancelled(state)
            hotel_response = self.planner._format_pois_for_prompt(
                "高德酒店搜索结果", hotel_pois, limit=10
            )
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            logger.info(f"[trip_graph] hotel discovery failed: {type(exc).__name__}")
            discovery_errors.append(f"hotels:{type(exc).__name__}")
            hotel_response = "高德酒店搜索结果：暂不可用。"
        self._progress(
            state,
            "hotels",
            45,
            "已完成住宿区域与酒店检索",
            f"找到 {len(hotel_pois)} 个住宿候选。",
            {"hotel_count": len(hotel_pois)},
        )

        first_error = discovery_errors[0] if discovery_errors else ""
        failure_stage, _, failure_type = first_error.partition(":")
        updates = {
            "attraction_pois": attraction_pois,
            "attraction_response": attraction_response,
            "source_weather": source_weather,
            "weather_response": weather_response,
            "hotel_pois": hotel_pois,
            "hotel_response": hotel_response,
            # Weather and hotel data enrich the plan but are not prerequisites
            # for the single primary planning call. Attraction discovery is.
            "context_ready": attraction_ready,
            "discovery_errors": discovery_errors,
            "failure_stage": failure_stage,
            "failure_type": failure_type,
        }
        return self._record_result(state, "discovery", updates)

    def _compose_node(self, state: TripPlanningState) -> dict:
        self._progress(state, "compose", 54, "正在综合偏好生成行程")
        try:
            response = self.planner._run_primary_planner(
                state["request"],
                state.get("attraction_response", ""),
                state.get("weather_response", ""),
                state.get("hotel_response", ""),
            )
            self._check_cancelled(state)
            if not isinstance(response, str):
                raise TypeError("primary planner returned a non-text response")
            logger.info(f"[trip_graph] primary response chars={len(response)}")
            updates = {
                "planner_response": response,
                "model_ready": bool(response.strip()),
                "repairable": False,
                "failure_stage": "",
                "failure_type": "",
            }
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            logger.info(f"[trip_graph] primary planner failed: {type(exc).__name__}")
            updates = {
                "model_ready": False,
                "repairable": False,
                "failure_stage": "compose",
                "failure_type": type(exc).__name__,
            }
        return self._record_result(state, "compose", updates)

    def _parse_node(self, state: TripPlanningState) -> dict:
        self._check_cancelled(state)
        response = state.get("planner_response", "")
        try:
            plan = self.planner._parse_response(response, state["request"])
            self._check_cancelled(state)
            if not isinstance(plan, TripPlan):
                raise TypeError("primary parser returned no TripPlan")
        except ValueError as exc:
            logger.info(f"[trip_graph] primary parse failed: {type(exc).__name__}")
            updates = {
                "parsed": False,
                "repairable": bool(response and response.strip()),
                "failure_stage": "parse",
                "failure_type": type(exc).__name__,
            }
            return self._record_result(state, "parse", updates)
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            # Programming/service failures are not repairable by another LLM.
            logger.info(f"[trip_graph] parser execution failed: {type(exc).__name__}")
            updates = {
                "parsed": False,
                "repairable": False,
                "failure_stage": "parse",
                "failure_type": type(exc).__name__,
            }
            return self._record_result(state, "parse", updates)

        try:
            self._check_cancelled(state)
            plan = self.planner._normalize_plan_dates_and_weather(
                state["request"],
                plan,
                state.get("weather_response", ""),
                state.get("source_weather", []),
            )
            if not isinstance(plan, TripPlan):
                raise TypeError("normalizer returned no TripPlan")
            plan.generation_mode = "primary"
            updates = {
                "trip_plan": plan,
                "parsed": True,
                "repairable": False,
                "failure_stage": "",
                "failure_type": "",
            }
        except ValueError as exc:
            # Date/day-count validation is part of model structure and can be
            # repaired once without replaying the primary planning call.
            logger.info(f"[trip_graph] primary structure invalid: {type(exc).__name__}")
            updates = {
                "parsed": False,
                "repairable": bool(response and response.strip()),
                "failure_stage": "normalize",
                "failure_type": type(exc).__name__,
            }
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            logger.info(f"[trip_graph] normalization failed: {type(exc).__name__}")
            updates = {
                "parsed": False,
                "repairable": False,
                "failure_stage": "normalize",
                "failure_type": type(exc).__name__,
            }
        return self._record_result(state, "parse", updates)

    def _repair_node(self, state: TripPlanningState) -> dict:
        self._progress(
            state,
            "repair",
            59,
            "主规划结构需要校正，正在执行一次轻量修复",
        )
        try:
            repaired_response = self.planner._repair_planner_response(
                state["request"],
                state.get("planner_response", ""),
            )
            self._check_cancelled(state)
            if not isinstance(repaired_response, str) or not repaired_response.strip():
                raise ValueError("repair agent returned an empty response")
            plan = self.planner._parse_response(repaired_response, state["request"])
            self._check_cancelled(state)
            if not isinstance(plan, TripPlan):
                raise TypeError("repair parser returned no TripPlan")
            self._check_cancelled(state)
            plan = self.planner._normalize_plan_dates_and_weather(
                state["request"],
                plan,
                state.get("weather_response", ""),
                state.get("source_weather", []),
            )
            if not isinstance(plan, TripPlan):
                raise TypeError("repair normalizer returned no TripPlan")
            plan.generation_mode = "repaired"
            updates = {
                "planner_response": repaired_response,
                "trip_plan": plan,
                "parsed": True,
                "repairable": False,
                "failure_stage": "",
                "failure_type": "",
            }
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            logger.info(f"[trip_graph] repair failed: {type(exc).__name__}")
            updates = {
                "parsed": False,
                "repairable": False,
                "failure_stage": "repair",
                "failure_type": type(exc).__name__,
            }
        return self._record_result(state, "repair", updates)

    def _ground_node(self, state: TripPlanningState) -> dict:
        self._progress(state, "grounding", 64, "正在校准日期、地点与地图坐标")
        expected_mode = state["trip_plan"].generation_mode
        try:
            plan = self.planner._ground_trip_plan(
                state["request"],
                state["trip_plan"],
                state.get("attraction_pois", []),
                state.get("hotel_pois", []),
            )
            self._check_cancelled(state)
            if not isinstance(plan, TripPlan):
                raise TypeError("grounder returned no TripPlan")
            plan = self.planner._finalize_generated_content(state["request"], plan)
            self._check_cancelled(state)
            if not isinstance(plan, TripPlan):
                raise TypeError("content finalizer returned no TripPlan")
            # Grounding may transparently upgrade a primary plan to
            # ``repaired`` when it creates trusted POIs for fully empty days.
            # It may never downgrade a fallback or erase an earlier repair.
            if expected_mode == "map_fallback":
                plan.generation_mode = "map_fallback"
            elif expected_mode == "repaired" or plan.generation_mode == "repaired":
                plan.generation_mode = "repaired"
            else:
                plan.generation_mode = "primary"
            grounded_pois = sum(
                attraction.coordinate_source == "amap_poi"
                for day in plan.days
                for attraction in day.attractions
            )
            self._progress(
                state,
                "grounding",
                68,
                "地点与地图事实已校准",
                f"已将 {grounded_pois} 个景点匹配到真实地图 POI。",
                {"verified_pois": grounded_pois},
            )
            updates = {
                "trip_plan": plan,
                "grounded": True,
                "failure_stage": "",
                "failure_type": "",
            }
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            logger.info(f"[trip_graph] grounding failed: {type(exc).__name__}")
            updates = {
                "grounded": False,
                "failure_stage": "grounding",
                "failure_type": type(exc).__name__,
            }
        return self._record_result(state, "ground", updates)

    def _fallback_node(self, state: TripPlanningState) -> dict:
        self._progress(
            state,
            "recovery",
            60,
            "主规划暂不可用，正在生成地图校验备选方案",
        )
        request = state["request"]
        try:
            plan = self.planner._create_fallback_plan(
                request, state.get("attraction_pois", [])
            )
            self._check_cancelled(state)
            if not isinstance(plan, TripPlan):
                raise TypeError("fallback builder returned no TripPlan")
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            logger.info(f"[trip_graph] fallback builder failed: {type(exc).__name__}")
            plan = self._build_emergency_fallback(request)
        plan.generation_mode = "map_fallback"
        try:
            self._check_cancelled(state)
            candidate = self.planner._ground_trip_plan(
                request,
                plan,
                state.get("attraction_pois", []),
                state.get("hotel_pois", []),
            )
            self._check_cancelled(state)
            if not isinstance(candidate, TripPlan):
                raise TypeError("fallback grounder returned no TripPlan")
            plan = candidate
            candidate = self.planner._finalize_generated_content(request, plan)
            self._check_cancelled(state)
            if not isinstance(candidate, TripPlan):
                raise TypeError("fallback finalizer returned no TripPlan")
            plan = candidate
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            logger.info(f"[trip_graph] fallback grounding failed: {type(exc).__name__}")
        try:
            self._check_cancelled(state)
            candidate = self.planner._normalize_plan_dates_and_weather(
                request,
                plan,
                state.get("weather_response", ""),
                state.get("source_weather", []),
            )
            if not isinstance(candidate, TripPlan):
                raise TypeError("fallback normalizer returned no TripPlan")
            plan = candidate
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            # The deterministic builder already aligns dates. Preserve that
            # usable fallback if weather normalization alone is unavailable.
            logger.info(f"[trip_graph] fallback normalization failed: {type(exc).__name__}")
        plan.generation_mode = "map_fallback"
        updates = {
            "trip_plan": plan,
            "parsed": False,
            "repairable": False,
            "grounded": True,
        }
        return self._record_result(state, "fallback", updates)

    def _build_emergency_fallback(self, request: TripRequest) -> TripPlan:
        """Return a valid, intentionally blocked plan if fallback code fails."""
        start = datetime.strptime(request.start_date, "%Y-%m-%d")
        days = [
            DayPlan(
                date=(start + timedelta(days=index)).strftime("%Y-%m-%d"),
                day_index=index,
                description=f"第{index + 1}天待重新生成",
                transportation=request.transportation,
                accommodation=request.accommodation,
                attractions=[],
                meals=[
                    Meal(type="breakfast", name="早餐待确认"),
                    Meal(type="lunch", name="午餐待确认"),
                    Meal(type="dinner", name="晚餐待确认"),
                ],
            )
            for index in range(request.travel_days)
        ]
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            generation_mode="map_fallback",
            days=days,
            weather_info=[],
            overall_suggestions=(
                "主规划和地图备选构建均未完整执行，当前空白日期仅用于明确阻止"
                "自动保存与发送；请稍后重新生成。"
            ),
        )

    def _enrich_node(self, state: TripPlanningState) -> dict:
        self._check_cancelled(state)
        request = state["request"]
        plan = state["trip_plan"]
        runtime = state.get("runtime") or {}
        checkpoint = runtime.get("checkpoint")
        completed = runtime.get("completed")
        completed_stages = completed if isinstance(completed, set) else set()
        if isinstance(checkpoint, dict):
            checkpoint_plan = checkpoint.get("trip_plan")
            if (
                isinstance(checkpoint_plan, TripPlan)
                and completed_stages.intersection(
                    {"enrich_routes", "enrich_budget", "enrich_web"}
                )
            ):
                plan = checkpoint_plan.model_copy(deep=True)
            checkpoint_errors = checkpoint.get("enrichment_errors")
            enrichment_errors = (
                list(checkpoint_errors)
                if isinstance(checkpoint_errors, list)
                else []
            )
        else:
            enrichment_errors = []
        expected_mode = plan.generation_mode

        if "enrich_routes" not in completed_stages:
            # Route evidence is owned by the deterministic map stage. Never let
            # model-authored or partially mutated routes survive a failed call.
            for day in plan.days:
                day.routes = []
            before_stage = plan.model_copy(deep=True)
            try:
                candidate = self.planner._apply_route_planning(request, plan)
                self._check_cancelled(state)
                if not isinstance(candidate, TripPlan):
                    raise TypeError("route planner returned no TripPlan")
                plan = candidate
            except TripGenerationCancelledError:
                raise
            except Exception as exc:
                plan = before_stage
                logger.info(
                    f"[trip_graph] route enrichment failed: {type(exc).__name__}"
                )
                enrichment_errors.append(f"routes:{type(exc).__name__}")
            self._checkpoint_enrichment_stage(
                state, "enrich_routes", plan, enrichment_errors
            )
        route_count = sum(len(day.routes) for day in plan.days)
        self._progress(
            state,
            "routes",
            76,
            "已完成景点间路线规划",
            f"已生成 {route_count} 段景点衔接路线。",
            {"route_count": route_count},
        )

        if "enrich_budget" not in completed_stages:
            # Budget provenance must come from the deterministic estimator.
            plan.budget = None
            before_stage = plan.model_copy(deep=True)
            try:
                candidate = self.planner._apply_budget_estimate(request, plan)
                self._check_cancelled(state)
                if not isinstance(candidate, TripPlan):
                    raise TypeError("budget estimator returned no TripPlan")
                plan = candidate
            except TripGenerationCancelledError:
                raise
            except Exception as exc:
                plan = before_stage
                logger.info(
                    "[trip_graph] budget enrichment failed: %s: %s",
                    type(exc).__name__, exc,
                )
                enrichment_errors.append(f"budget:{type(exc).__name__}")
            self._checkpoint_enrichment_stage(
                state, "enrich_budget", plan, enrichment_errors
            )
        budget_total = plan.budget.total if plan.budget else 0
        self._progress(
            state,
            "budget",
            86,
            "已完成预算测算",
            f"当前方案参考总预算约 ¥{budget_total}。" if budget_total else "已完成费用结构检查。",
            {"budget_total": budget_total},
        )

        if "enrich_web" not in completed_stages:
            # Search citations and audit status cannot be self-attested by the
            # primary model when the web enrichment stage is unavailable.
            plan.web_guide = None
            plan.web_references = []
            plan.agent_audit = None
            before_stage = plan.model_copy(deep=True)
            try:
                candidate = self.planner._apply_web_guide(request, plan)
                self._check_cancelled(state)
                if not isinstance(candidate, TripPlan):
                    raise TypeError("web guide returned no TripPlan")
                plan = candidate
            except TripGenerationCancelledError:
                raise
            except Exception as exc:
                plan = before_stage
                logger.info(
                    f"[trip_graph] web enrichment failed: {type(exc).__name__}"
                )
                enrichment_errors.append(f"web_guide:{type(exc).__name__}")
            self._checkpoint_enrichment_stage(
                state, "enrich_web", plan, enrichment_errors
            )
        audit_status = plan.agent_audit.status if plan.agent_audit else "unavailable"
        self._progress(
            state,
            "audit",
            93,
            "已完成联网信息复核",
            "已检查攻略补充、公开来源与潜在出行风险。",
            {"audit_status": audit_status},
        )
        plan.generation_mode = expected_mode
        updates = {
            "trip_plan": plan,
            "enrichment_errors": enrichment_errors,
        }
        return self._record_result(state, "enrich", updates)

    def _checkpoint_enrichment_stage(
        self,
        state: TripPlanningState,
        stage: str,
        plan: TripPlan,
        enrichment_errors: list[str],
    ) -> None:
        """Persist an immutable substage snapshot for safe graph recovery."""
        runtime = state.get("runtime")
        if not isinstance(runtime, dict):
            return
        checkpoint = runtime.setdefault("checkpoint", {})
        completed = runtime.setdefault("completed", set())
        if isinstance(checkpoint, dict):
            checkpoint["trip_plan"] = plan.model_copy(deep=True)
            checkpoint["enrichment_errors"] = list(enrichment_errors)
        if isinstance(completed, set):
            completed.add(stage)

    def _quality_node(self, state: TripPlanningState) -> dict:
        self._check_cancelled(state)
        request = state["request"]
        plan = state["trip_plan"]
        try:
            quality = get_trip_plan_quality_service().evaluate(request, plan)
            self._check_cancelled(state)
            if not isinstance(quality, TripPlanQualityResult):
                raise TypeError("quality service returned no result")
            plan.quality = quality

            def add_warning(
                code: str,
                path: str,
                message: str,
                suggestion: str,
                penalty: int,
            ) -> None:
                if any(issue.code == code for issue in plan.quality.issues):
                    return
                plan.quality.issues.append(
                    TripPlanQualityIssue(
                        code=code,
                        severity="warning",
                        path=path,
                        message=message,
                        suggestion=suggestion,
                    )
                )
                plan.quality.score = max(0, plan.quality.score - penalty)
                if plan.quality.status == "passed":
                    plan.quality.status = "warning"

            enrichment_errors = state.get("enrichment_errors", [])
            partial_stages = [
                item.partition(":")[0]
                for item in enrichment_errors
            ]
            if partial_stages:
                add_warning(
                    "PIPELINE_ENRICHMENT_PARTIAL",
                    "enrichment",
                    f"{'、'.join(partial_stages)}阶段未能完整执行，相关结果需要人工复核。",
                    "重新生成或在出发前使用官方地图、票务和公开来源复核。",
                    6,
                )
                if "routes" in partial_stages:
                    plan.quality.executability_score = max(
                        0, plan.quality.executability_score - 6
                    )
                if any(
                    stage in {"budget", "web_guide"}
                    for stage in partial_stages
                ):
                    plan.quality.readiness_score = max(
                        0, plan.quality.readiness_score - 6
                    )
            # Recompute the unified gate triple after the score/issue
            # mutations above; partial enrichment demotes the plan without
            # fabricating an error issue.
            refresh_quality_gate(
                plan.quality,
                generation_mode=plan.generation_mode,
                force_review=bool(enrichment_errors),
            )
            logger.info(
                "[trip_graph] quality evaluation complete: "
                "score=%d, publishable=%s, review_required=%s, "
                "total_issues=%d, status=%s",
                plan.quality.score,
                plan.quality.publishable,
                plan.quality.review_required,
                len(plan.quality.issues),
                plan.quality.status,
                
            )
            if has_blocking:
                plan.quality.publishable = False
                plan.quality.review_required = True
                plan.quality.status = "failed"
            elif not plan.quality.publishable:
                # Keep service decision; ensure review flag for non-clean plans.
                plan.quality.review_required = True
        except TripGenerationCancelledError:
            raise
        except Exception as exc:
            logger.info(f"[trip_graph] quality gate failed: {type(exc).__name__}")
            plan.quality = TripPlanQualityResult(
                status="failed",
                score=0,
                checked_items=["质量门执行状态"],
                issues=[
                    TripPlanQualityIssue(
                        code="QUALITY_GATE_UNAVAILABLE",
                        severity="error",
                        path="quality",
                        message="质量检查未能完整执行，方案已阻止自动保存。",
                        suggestion="稍后重新生成，或人工复核全部行程事实。",
                    )
                ],
            )
        self._progress(
            state,
            "quality",
            98,
            "已完成可执行性与质量检查",
            f"方案评分 {plan.quality.score}/100，发现 {len(plan.quality.issues)} 项需留意内容。",
            {
                "quality_score": plan.quality.score,
                "issue_count": len(plan.quality.issues),
                "generation_mode": plan.generation_mode,
            },
        )
        updates: dict[str, Any] = {"trip_plan": plan}
        # Track the best plan across repair rounds via the node return value
        # (edge predicates cannot persist state), so the loop can never end on
        # a worse plan than an earlier round. Ranked by deliverability first:
        # a blocked round must never displace a deliverable snapshot, however
        # much its raw score improved.
        best_plan = state.get("best_trip_plan")
        if not isinstance(best_plan, TripPlan) or self._plan_rank(
            plan
        ) > self._plan_rank(best_plan):
            updates["best_quality_score"] = plan.quality.score if plan.quality else 0
            updates["best_trip_plan"] = plan.model_copy(deep=True)
        return self._record_result(state, "quality", updates)

    # ── quality repair loop ────────────────────────────────────────────

    # Issues that can be auto-repaired in the deterministic repair node.
    _REPAIRABLE_ISSUES: frozenset[str] = frozenset({
        "DAY_SCHEDULE_OVERLOAD",
        "DAY_UNDERFILLED",
        "TOO_MANY_MUSEUMS",
        "TOO_MANY_PARKS",
        "ATTRACTION_TYPE_CONCENTRATION",
    })

    # Hard bound for quality-repair rounds, shared by the LangGraph edge
    # predicate and the sequential fallback loop.
    _MAX_QUALITY_REPAIRS: int = 2

    def _after_quality(self, state: TripPlanningState) -> str:
        """Route: repair if score < 75 with no blocking issues, else end.

        This is a conditional-edge predicate: it must stay read-only.
        LangGraph never merges ``state[...] = ...`` writes made here back
        into the graph channels, so any tracking belongs in node returns.
        """
        plan = state.get("trip_plan")
        quality = getattr(plan, "quality", None) if isinstance(plan, TripPlan) else None
        if quality is None:
            return "end"

        quality_status = getattr(quality, "quality_status", "blocked")
        repair_count = int(state.get("repair_count", 0))
        max_repairs = self._MAX_QUALITY_REPAIRS

        if quality_status != "needs_review":
            logger.info(
                "[trip_graph] quality gate: status=%s, score=%d, "
                "repair_count=%d — skipping repair",
                quality_status, quality.score, repair_count,
            )
            return "end"

        if repair_count >= max_repairs:
            logger.info(
                "[trip_graph] quality repair: max rounds (%d) reached, "
                "score=%d",
                max_repairs, quality.score,
            )
            return "end"

        repairable = [
            i for i in quality.issues
            if i.code in self._REPAIRABLE_ISSUES
        ]
        if not repairable:
            logger.info(
                "[trip_graph] quality repair: score=%d < 75 but no "
                "repairable issues found",
                quality.score,
            )
            return "end"

        logger.info(
            "[trip_graph] quality repair round %d: score=%d, "
            "repairable_issues=%s",
            repair_count + 1, quality.score,
            sorted({i.code for i in repairable}),
        )
        return "repair_quality"

    def _repair_quality_node(self, state: TripPlanningState) -> dict:
        """Deterministic quality repair — no LLM calls.

        All loop-tracking state (``repair_count``, ``repaired_issue_codes``,
        ``dirty_enrichments``) is communicated exclusively through the
        returned update dict so it merges into the LangGraph channels.
        """
        self._check_cancelled(state)
        plan = state["trip_plan"]
        quality = plan.quality
        repair_count = int(state.get("repair_count", 0))
        repaired = set(state.get("repaired_issue_codes") or ())
        dirty: set[str] = set()
        actions: list[str] = []

        self._progress(
            state, "repairing", 94,
            f"正在优化行程质量（第{repair_count + 1}轮）…",
            "自动调整景点安排、时间分配和类型平衡。",
            {},
        )

        # Repair DAY_SCHEDULE_OVERLOAD: drop lowest-priority attraction
        # from the most overloaded day.
        overload_issues = [
            i for i in quality.issues
            if i.code == "DAY_SCHEDULE_OVERLOAD"
            and i.code not in repaired
        ]
        if overload_issues:
            plan = self._repair_overload(plan, actions)
            dirty.add("routes")

        # Repair DAY_UNDERFILLED: add an attraction from the verified
        # discovery pool on an underfilled non-edge day.
        underfill_issues = [
            i for i in quality.issues
            if i.code == "DAY_UNDERFILLED"
            and i.code not in repaired
        ]
        if underfill_issues:
            plan = self._repair_underfill(state, plan, actions)
            dirty.add("routes")

        # Repair museum/park caps.
        for code in ("TOO_MANY_MUSEUMS", "TOO_MANY_PARKS"):
            cap_issues = [
                i for i in quality.issues
                if i.code == code and i.code not in repaired
            ]
            if cap_issues:
                plan = self._repair_cap(plan, code, actions)
                dirty.add("routes")

        # Repair concentration when it conflicts with preferences.
        conc_issues = [
            i for i in quality.issues
            if i.code == "ATTRACTION_TYPE_CONCENTRATION"
            and i.code not in repaired
        ]
        if conc_issues:
            plan = self._repair_concentration(state, plan, actions)
            dirty.add("routes")

        if not actions:
            logger.info(
                "[trip_graph] repair: no action taken — "
                "all repairable issues already attempted"
            )
            return {"trip_plan": plan, "repair_count": repair_count + 1}

        for code in {i.code for i in quality.issues
                     if i.code in self._REPAIRABLE_ISSUES}:
            repaired.add(code)
        dirty.add("budget")
        # Hotel distance may change when attractions change.
        dirty.add("hotel_distance")
        # Web guide consistency may be affected by attraction changes.
        dirty.add("web_guide")

        logger.info(
            "[trip_graph] repair actions: %s, dirty=%s",
            "; ".join(actions), sorted(dirty),
        )
        return {
            "trip_plan": plan,
            "repair_count": repair_count + 1,
            "repaired_issue_codes": repaired,
            "dirty_enrichments": dirty,
        }

    @staticmethod
    def _build_attraction_from_poi(poi: Any, visit_duration: int = 120) -> Attraction:
        """Build an Attraction from a discovered POI with admin metadata."""
        return Attraction(
            name=str(getattr(poi, "name", "") or ""),
            address=str(getattr(poi, "address", "") or ""),
            location=getattr(poi, "location", None),
            visit_duration=visit_duration,
            description=str(getattr(poi, "type", "") or ""),
            category=str(getattr(poi, "type", "") or ""),
            poi_id=str(getattr(poi, "id", "") or ""),
            coordinate_source="amap_poi",
            verification=VerificationMeta(
                cityname=str(getattr(poi, "cityname", "") or ""),
                citycode=str(getattr(poi, "citycode", "") or ""),
                adname=str(getattr(poi, "district", "") or ""),
                adcode=str(getattr(poi, "adcode", "") or ""),
            ),
        )

    def _repair_overload(
        self, plan: TripPlan, actions: list[str],
    ) -> TripPlan:
        """Remove the lowest-priority attraction from the most overloaded day."""
        target_day = None
        max_duration = 0
        for day in plan.days:
            total = sum(
                max(0, a.visit_duration or 0)
                for a in (day.attractions or [])
            )
            if total > max_duration and len(day.attractions or []) > 1:
                max_duration = total
                target_day = day
        if target_day is None or not target_day.attractions:
            return plan

        # Drop the attraction with the lowest visit_duration (least impact).
        dropped = min(
            target_day.attractions,
            key=lambda a: max(0, a.visit_duration or 0),
        )
        target_day.attractions = [
            a for a in target_day.attractions if a is not dropped
        ]
        actions.append(
            f"第{target_day.day_index + 1}天移除低优先级景点「{dropped.name}」"
            "以缓解日程过载"
        )
        return plan

    def _repair_underfill(
        self, state: TripPlanningState, plan: TripPlan, actions: list[str],
    ) -> TripPlan:
        """Try to add an attraction from the verified pool to an underfilled day."""
        request = state["request"]
        attraction_pool: list = state.get("attraction_pois") or []
        if not attraction_pool:
            return plan

        # Find the most underfilled non-edge day (or edge day if only edge days).
        target = None
        max_gap = 0
        for day in plan.days:
            current = sum(
                max(0, a.visit_duration or 0)
                for a in (day.attractions or [])
            )
            # Edge days with cross-city travel have lower underfill threshold
            # set by the quality service — target the biggest absolute gap.
            gap = max(0, 180 - current)
            if gap > max_gap:
                max_gap = gap
                target = day
        if target is None or max_gap <= 0:
            return plan

        existing_names = {
            a.name for a in (target.attractions or [])
        }
        for poi in attraction_pool:
            poi_name = getattr(poi, "name", "") or ""
            if not poi_name or poi_name in existing_names:
                continue
            if not self._poi_matches_destination(request, poi):
                continue
            target.attractions.append(
                self._build_attraction_from_poi(poi, visit_duration=120)
            )
            actions.append(
                f"第{target.day_index + 1}天补充景点「{poi_name}」"
                "以丰富当日行程"
            )
            break
        return plan

    def _repair_cap(
        self, plan: TripPlan, code: str, actions: list[str],
    ) -> TripPlan:
        """Reduce museum/park count by dropping one excess item."""
        marker_map = {
            "TOO_MANY_MUSEUMS": ("博物馆", "美术馆", "艺术馆", "纪念馆", "科技馆", "展览馆"),
            "TOO_MANY_PARKS": ("公园", "绿道", "湿地", "植物园"),
        }
        markers = marker_map.get(code, ())
        cap_map = {"TOO_MANY_MUSEUMS": 3, "TOO_MANY_PARKS": 4}

        all_attrs = [(day, a) for day in plan.days for a in (day.attractions or [])]
        matches = [
            (day, a) for day, a in all_attrs
            if any(m in f"{a.name} {a.category or ''}" for m in markers)
        ]
        limit = cap_map.get(code, 99)
        if len(matches) <= limit:
            return plan

        # Drop the last excess item that is not the only thing on its day —
        # emptying a day raises EMPTY_DAY, a blocking issue worse than the
        # cap violation this repair is meant to relieve.
        droppable = [
            (day, attraction)
            for day, attraction in matches
            if len(day.attractions or []) > 1
        ]
        if not droppable:
            return plan
        day, dropped = droppable[-1]
        day.attractions = [a for a in (day.attractions or []) if a is not dropped]
        label = "博物馆" if code == "TOO_MANY_MUSEUMS" else "公园"
        actions.append(
            f"第{day.day_index + 1}天移除超量{label}「{dropped.name}」"
        )
        return plan

    def _repair_concentration(
        self, state: TripPlanningState, plan: TripPlan, actions: list[str],
    ) -> TripPlan:
        """Swap one attraction to a different category."""
        request = state["request"]
        all_attrs = [(day, a) for day in plan.days for a in (day.attractions or [])]
        if len(all_attrs) < 2:
            return plan

        # Find the dominant category and swap the last item in that category.
        category_counts: dict[str, int] = {}
        for _, a in all_attrs:
            cat = self._category_for_repair(a)
            category_counts[cat] = category_counts.get(cat, 0) + 1
        dominant = max(category_counts, key=category_counts.get)
        pool = state.get("attraction_pois") or []
        existing_names = {a.name for _, a in all_attrs}

        for day, a in reversed(all_attrs):
            if self._category_for_repair(a) != dominant:
                continue
            for poi in pool:
                poi_name = getattr(poi, "name", "") or ""
                poi_type = getattr(poi, "type", "") or ""
                if poi_name in existing_names:
                    continue
                if self._category_for_repair(Attraction(
                    name=poi_name, address="", location=None,
                    visit_duration=0, description="",
                    category=poi_type,
                )) == dominant:
                    continue
                if not self._poi_matches_destination(request, poi):
                    continue
                # Found a different-category replacement.
                replacement = self._build_attraction_from_poi(
                    poi, visit_duration=a.visit_duration,
                )
                day.attractions = [
                    replacement if x is a else x
                    for x in (day.attractions or [])
                ]
                actions.append(
                    f"第{day.day_index + 1}天将「{a.name}」替换为"
                    f"「{poi_name}」以增加类型多样性"
                )
                return plan
        return plan

    def _poi_matches_destination(
        self, request: TripRequest, poi: Any,
    ) -> bool:
        """Return True if *poi* plausibly belongs to the destination city.

        Uses the shared ``poi_destination_status`` with both the POI
        address and district fields.  Only ``"matched"`` is accepted.
        ``"mismatched"`` and ``"unknown"`` are rejected.
        """
        from ...services.destination_feasibility_service import poi_destination_status

        dest = request.city
        poi_address = str(getattr(poi, "address", "") or "")
        poi_name = str(getattr(poi, "name", "") or "")
        poi_cityname = str(getattr(poi, "cityname", "") or "")
        poi_citycode = str(getattr(poi, "citycode", "") or "")
        poi_adname = str(getattr(poi, "district", "") or "")
        poi_adcode = str(getattr(poi, "adcode", "") or "")

        result = poi_destination_status(
            destination_city=dest,
            cityname=poi_cityname,
            citycode=poi_citycode,
            adname=poi_adname,
            adcode=poi_adcode,
            address=poi_address,
            name=poi_name,
        )
        if result == "matched":
            return True
        if result == "mismatched":
            return False

        logger.info(
            "[trip_graph] repair candidate rejected: "
            "poi=%r cityname=%r adname=%r address=%s — status=%s for dest=%r",
            poi_name, poi_cityname, poi_adname,
            (poi_address or "")[:40], result, dest,
        )
        return False

    @staticmethod
    def _category_for_repair(attr: Any) -> str:
        """Simplified category for repair matching."""
        text = f"{getattr(attr, 'name', '') or ''} "
        text += f"{getattr(attr, 'category', '') or ''}"
        if any(m in text for m in ("博物馆", "美术馆", "艺术馆", "纪念馆", "科技馆")):
            return "museum"
        if any(m in text for m in ("公园", "园林", "湿地", "植物园", "绿道", "山", "湖", "瀑布")):
            return "nature"
        if any(m in text for m in ("寺", "庙", "祠", "塔", "遗址", "古城", "古镇", "城墙", "文化")):
            return "culture"
        if any(m in text for m in ("街区", "街", "步行街", "购物", "广场", "商圈")):
            return "street"
        return "other"

    def _refresh_enrichment_node(self, state: TripPlanningState) -> dict:
        """Re-compute enrichment data that was invalidated by repairs."""
        self._check_cancelled(state)
        request = state["request"]
        plan = state["trip_plan"]
        dirty: set[str] = state.get("dirty_enrichments") or set()
        if not dirty:
            return {"trip_plan": plan}

        self._progress(
            state, "repairing", 96,
            "正在重新计算路线和预算…",
            "根据修改后的行程更新路线、预算和酒店校验。",
            {},
        )

        # Routes change when attraction set changes.
        if dirty.intersection({"routes", "hotel_distance"}):
            try:
                candidate = self.planner._apply_route_planning(request, plan)
                if isinstance(candidate, TripPlan):
                    plan = candidate
            except Exception as exc:
                logger.info(
                    "[trip_graph] repair route refresh failed: %s",
                    type(exc).__name__,
                )

        # Budget changes when attractions or hotel change.
        enrichment_errors = list(state.get("enrichment_errors") or [])
        if dirty.intersection({"routes", "budget", "hotel_distance"}):
            # Keep the previous budget: BUDGET_MISSING is a blocking code, so
            # clearing it and then failing would turn a transient estimator
            # outage into a hard rejection of an otherwise deliverable plan.
            before_stage = plan.model_copy(deep=True)
            plan.budget = None
            try:
                candidate = self.planner._apply_budget_estimate(request, plan)
                if isinstance(candidate, TripPlan):
                    plan = candidate
            except Exception as exc:
                plan = before_stage
                logger.info(
                    "[trip_graph] repair budget refresh failed: %s",
                    type(exc).__name__,
                )
                # Surface the degradation so the quality gate demotes the plan
                # instead of silently publishing a stale budget.
                enrichment_errors.append(f"budget:{type(exc).__name__}")

        return {
            "trip_plan": plan,
            "dirty_enrichments": set(),
            "enrichment_errors": enrichment_errors,
        }

    def _after_discovery(self, state: TripPlanningState) -> str:
        return "compose" if state.get("context_ready") else "fallback"

    def _after_compose(self, state: TripPlanningState) -> str:
        return "parse" if state.get("model_ready") else "fallback"

    def _after_parse(self, state: TripPlanningState) -> str:
        if state.get("parsed"):
            return "ground"
        return "repair" if state.get("repairable") else "fallback"

    def _after_repair(self, state: TripPlanningState) -> str:
        return "ground" if state.get("parsed") else "fallback"

    def _after_ground(self, state: TripPlanningState) -> str:
        return "enrich" if state.get("grounded") else "fallback"

    def _record_result(
        self,
        state: TripPlanningState,
        stage: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Checkpoint a node result in invocation-local mutable state."""
        runtime = state.get("runtime")
        if isinstance(runtime, dict):
            checkpoint = runtime.setdefault("checkpoint", {})
            completed = runtime.setdefault("completed", set())
            if isinstance(checkpoint, dict):
                checkpoint.update(
                    {
                        key: (
                            value.model_copy(deep=True)
                            if isinstance(value, TripPlan)
                            else value
                        )
                        for key, value in updates.items()
                    }
                )
            if isinstance(completed, set):
                completed.add(stage)
        return updates

    def _check_cancelled(self, state: TripPlanningState) -> None:
        callback = state.get("progress_callback")
        checker = getattr(callback, "raise_if_cancelled", None)
        if callable(checker):
            checker()


    def _progress(
        self,
        state: TripPlanningState,
        stage: str,
        percent: int,
        message: str,
        detail: str = "",
        meta: Optional[dict] = None,
    ) -> None:
        self._check_cancelled(state)
        callback = state.get("progress_callback")
        if callback is None:
            return
        try:
            callback(
                stage=stage,
                progress=percent,
                message=message,
                detail=detail,
                meta=meta or {},
            )
        except TripGenerationCancelledError:
            raise
        except Exception:
            # Progress reporting must never change business execution.
            pass
