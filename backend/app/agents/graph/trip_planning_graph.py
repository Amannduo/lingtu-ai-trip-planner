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
    DayPlan,
    Meal,
    TripPlan,
    TripPlanQualityIssue,
    TripPlanQualityResult,
    TripRequest,
)
from ...services.trip_plan_quality_service import get_trip_plan_quality_service
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
            graph.add_edge("quality", END)
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
        }
        self._check_cancelled(initial)
        if self._compiled_graph is None:
            return self._run_sequential(initial)
        try:
            result = self._compiled_graph.invoke(initial)
            plan = result.get("trip_plan")
            if isinstance(plan, TripPlan):
                return plan
            raise RuntimeError("trip graph returned no plan")
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
        return state["trip_plan"]

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
            return plan

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
        return state["trip_plan"]

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
                    f"[trip_graph] budget enrichment failed: {type(exc).__name__}"
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
            # Align with reviewable quality service: do not re-impose score>=75.
            # Soft enrichment failures request review; only blocking issues unpublish.
            from ...services.trip_plan_quality_service import issue_disposition

            if enrichment_errors:
                plan.quality.review_required = True
            has_blocking = any(
                issue_disposition(issue) == "blocking"
                or str(getattr(issue, "severity", "")).strip().lower() == "error"
                for issue in plan.quality.issues
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
        return self._record_result(state, "quality", {"trip_plan": plan})

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
