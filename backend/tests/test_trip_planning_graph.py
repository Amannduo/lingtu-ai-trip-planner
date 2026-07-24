from __future__ import annotations

import threading
import time
import pytest

from app.agents.graph.trip_planning_graph import TripPlanningAgentGraph
from app.models.schemas import (
    AgentAuditResult,
    Attraction,
    Budget,
    DayPlan,
    Location,
    Meal,
    RouteSegment,
    TripPlan,
    TripRequest,
    WebReference,
)
from app.services.trip_generation_errors import TripGenerationCancelledError


def _request() -> TripRequest:
    return TripRequest(
        city="北京",
        start_date="2030-01-01",
        end_date="2030-01-01",
        travel_days=1,
        travelers=1,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
    )


def _plan(mode: str = "primary") -> TripPlan:
    return TripPlan(
        city="北京",
        start_date="2030-01-01",
        end_date="2030-01-01",
        generation_mode=mode,
        overall_suggestions="按计划出行。",
        days=[
            DayPlan(
                date="2030-01-01",
                day_index=0,
                description="城市漫游",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[
                    Attraction(
                        name="故宫博物院",
                        address="北京市东城区",
                        location=Location(longitude=116.397, latitude=39.918),
                        visit_duration=180,
                        description="历史文化参观",
                        category="博物馆",
                        poi_id="amap-1",
                        coordinate_source="amap_poi",
                    )
                ],
                meals=[
                    Meal(type="breakfast", name="早餐"),
                    Meal(type="lunch", name="午餐"),
                    Meal(type="dinner", name="晚餐"),
                ],
            )
        ],
    )


class _AmapStub:
    @staticmethod
    def get_weather(*_args, **_kwargs):
        return []


class _PlannerStub:
    def __init__(self, *, invalid_primary: bool = False, fail_primary: bool = False):
        self.amap_service = _AmapStub()
        self.invalid_primary = invalid_primary
        self.fail_primary = fail_primary
        self.primary_calls = 0
        self.repair_calls = 0
        self.route_calls = 0
        self.budget_calls = 0
        self.web_calls = 0

    @staticmethod
    def _search_attractions(_request):
        return []

    @staticmethod
    def _format_pois_for_prompt(*_args, **_kwargs):
        return "候选地点"

    @staticmethod
    def _format_weather_for_prompt(*_args, **_kwargs):
        return "天气未覆盖"

    @staticmethod
    def _search_hotels(*_args, **_kwargs):
        return []

    def _run_primary_planner(self, *_args, **_kwargs):
        self.primary_calls += 1
        if self.fail_primary:
            raise RuntimeError("primary unavailable")
        return "bad" if self.invalid_primary else "good"

    @staticmethod
    def _parse_response(response, _request):
        if response == "bad":
            raise ValueError("invalid json")
        return _plan()

    @staticmethod
    def _normalize_plan_dates_and_weather(_request, plan, *_args, **_kwargs):
        return plan

    @staticmethod
    def _ground_trip_plan(_request, plan, *_args, **_kwargs):
        return plan

    @staticmethod
    def _finalize_generated_content(_request, plan):
        return plan

    def _apply_route_planning(self, _request, plan):
        self.route_calls += 1
        return plan

    def _apply_budget_estimate(self, _request, plan):
        self.budget_calls += 1
        return plan

    def _apply_web_guide(self, _request, plan):
        self.web_calls += 1
        return plan

    @staticmethod
    def _create_fallback_plan(_request, _pois):
        return _plan("map_fallback")

    def _repair_planner_response(self, _request, _response):
        self.repair_calls += 1
        return "good"


def test_langgraph_normal_path_keeps_one_primary_call_and_no_repair() -> None:
    planner = _PlannerStub()
    graph = TripPlanningAgentGraph(planner)

    assert graph.graph_available is True
    result = graph.run(_request())

    assert result.generation_mode == "primary"
    assert planner.primary_calls == 1
    assert planner.repair_calls == 0


def test_repair_agent_runs_once_only_after_parse_failure() -> None:
    planner = _PlannerStub(invalid_primary=True)
    graph = TripPlanningAgentGraph(planner)

    result = graph.run(_request())

    assert result.generation_mode == "repaired"
    assert planner.primary_calls == 1
    assert planner.repair_calls == 1
    assert result.quality is not None
    assert result.quality.score <= 92


def test_primary_transport_failure_skips_repair_and_uses_fallback() -> None:
    planner = _PlannerStub(fail_primary=True)
    graph = TripPlanningAgentGraph(planner)

    result = graph.run(_request())

    assert result.generation_mode == "map_fallback"
    assert planner.primary_calls == 1
    assert planner.repair_calls == 0
    assert result.quality is not None
    assert result.quality.score <= 70


def test_graph_runtime_failure_never_replays_primary_model() -> None:
    class _BrokenCompiledGraph:
        @staticmethod
        def invoke(_state):
            raise RuntimeError("framework failure")

    planner = _PlannerStub()
    graph = TripPlanningAgentGraph(planner)
    graph._compiled_graph = _BrokenCompiledGraph()

    result = graph.run(_request())

    assert result.generation_mode == "map_fallback"
    assert planner.primary_calls == 0
    assert planner.repair_calls == 0



def test_discovery_overlaps_weather_with_attractions_before_hotel_search() -> None:
    attraction_started = threading.Event()
    weather_started = threading.Event()
    attraction_finished = threading.Event()

    class ConcurrentAmap:
        @staticmethod
        def get_weather(*_args, **_kwargs):
            weather_started.set()
            assert attraction_started.wait(1.0)
            time.sleep(0.20)
            return []

    class ConcurrentPlanner(_PlannerStub):
        def __init__(self):
            super().__init__()
            self.amap_service = ConcurrentAmap()

        @staticmethod
        def _search_attractions(_request):
            attraction_started.set()
            assert weather_started.wait(1.0)
            time.sleep(0.20)
            attraction_finished.set()
            return []

        @staticmethod
        def _search_hotels(*_args, **_kwargs):
            assert attraction_finished.is_set()
            time.sleep(0.05)
            return []

    planner = ConcurrentPlanner()
    graph = TripPlanningAgentGraph(planner)
    state = {
        "request": _request(),
        "runtime": {"checkpoint": {}, "completed": set()},
    }

    started = time.perf_counter()
    result = graph._discovery_node(state)
    elapsed = time.perf_counter() - started

    assert result["context_ready"] is True
    assert elapsed < 0.38  # sequential execution would take at least 0.45s


def test_weather_failure_does_not_discard_attractions_or_skip_primary() -> None:
    class WeatherFailureAmap:
        @staticmethod
        def get_weather(*_args, **_kwargs):
            raise TimeoutError("weather timeout")

    planner = _PlannerStub()
    planner.amap_service = WeatherFailureAmap()
    graph = TripPlanningAgentGraph(planner)

    result = graph.run(_request())

    assert result.generation_mode == "primary"
    assert planner.primary_calls == 1
    assert planner.repair_calls == 0


def test_non_structural_normalization_failure_skips_repair_agent() -> None:
    class BrokenNormalizerPlanner(_PlannerStub):
        @staticmethod
        def _normalize_plan_dates_and_weather(*_args, **_kwargs):
            raise RuntimeError("normalizer bug")

    planner = BrokenNormalizerPlanner()
    graph = TripPlanningAgentGraph(planner)

    result = graph.run(_request())

    assert result.generation_mode == "map_fallback"
    assert planner.primary_calls == 1
    assert planner.repair_calls == 0


def test_late_framework_failure_resumes_primary_plan_without_repeating_enrichment() -> None:
    planner = _PlannerStub()
    graph = TripPlanningAgentGraph(planner)

    class LateFailureGraph:
        @staticmethod
        def invoke(initial):
            state = dict(initial)
            for node in (
                graph._discovery_node,
                graph._compose_node,
                graph._parse_node,
                graph._ground_node,
                graph._enrich_node,
            ):
                state.update(node(state))
            raise RuntimeError("framework failed after enrichment")

    graph._compiled_graph = LateFailureGraph()
    result = graph.run(_request())

    assert result.generation_mode == "primary"
    assert planner.primary_calls == 1
    assert planner.repair_calls == 0
    assert planner.route_calls == 1
    assert planner.budget_calls == 1
    assert planner.web_calls == 1
    assert result.quality is not None


def test_framework_failure_after_compose_uses_checkpointed_response() -> None:
    planner = _PlannerStub()
    graph = TripPlanningAgentGraph(planner)

    class PostComposeFailureGraph:
        @staticmethod
        def invoke(initial):
            state = dict(initial)
            state.update(graph._discovery_node(state))
            state.update(graph._compose_node(state))
            raise RuntimeError("framework failed after compose")

    graph._compiled_graph = PostComposeFailureGraph()
    result = graph.run(_request())

    assert result.generation_mode == "primary"
    assert planner.primary_calls == 1
    assert planner.repair_calls == 0
    assert planner.route_calls == 1
    assert planner.budget_calls == 1
    assert planner.web_calls == 1


def test_enrichment_failure_isolated_and_visible_to_quality_gate() -> None:
    class RouteFailurePlanner(_PlannerStub):
        def _apply_route_planning(self, _request, _plan):
            self.route_calls += 1
            raise TimeoutError("route timeout")

    planner = RouteFailurePlanner()
    graph = TripPlanningAgentGraph(planner)
    result = graph.run(_request())

    assert result.generation_mode == "primary"
    assert planner.route_calls == 1
    assert planner.budget_calls == 1
    assert planner.web_calls == 1
    assert result.quality is not None
    assert result.quality.score < 100
    assert result.quality.executability_score < 100
    assert any(
        issue.code == "PIPELINE_ENRICHMENT_PARTIAL"
        for issue in result.quality.issues
    )
    assert result.quality.publishable is False


def test_budget_enrichment_failure_removes_model_budget_and_lowers_score() -> None:
    class BudgetFailurePlanner(_PlannerStub):
        def _apply_budget_estimate(self, _request, plan):
            self.budget_calls += 1
            plan.budget = None
            raise TimeoutError("budget timeout")

    planner = BudgetFailurePlanner()
    graph = TripPlanningAgentGraph(planner)
    result = graph.run(_request())

    assert result.generation_mode == "primary"
    assert result.budget is None
    assert planner.budget_calls == 1
    assert result.quality is not None
    assert result.quality.score < 100
    assert any(issue.code == "BUDGET_MISSING" for issue in result.quality.issues)
    assert result.quality.publishable is False


def test_fallback_builder_failure_returns_blocked_emergency_plan() -> None:
    class BrokenFallbackPlanner(_PlannerStub):
        fallback_calls = 0

        def _create_fallback_plan(self, _request, _pois):
            self.fallback_calls += 1
            raise RuntimeError("fallback builder bug")

    planner = BrokenFallbackPlanner(fail_primary=True)
    graph = TripPlanningAgentGraph(planner)
    result = graph.run(_request())

    assert result.generation_mode == "map_fallback"
    assert planner.fallback_calls == 1
    assert result.days[0].attractions == []
    assert result.quality is not None
    assert result.quality.status == "failed"
    assert result.quality.score <= 59


def test_hotel_discovery_failure_is_noncritical_to_primary_plan() -> None:
    class HotelFailurePlanner(_PlannerStub):
        @staticmethod
        def _search_hotels(*_args, **_kwargs):
            raise TimeoutError("hotel search timeout")

    planner = HotelFailurePlanner()
    graph = TripPlanningAgentGraph(planner)
    result = graph.run(_request())

    assert result.generation_mode == "primary"
    assert planner.primary_calls == 1
    assert planner.repair_calls == 0


def test_grounding_map_fill_upgrades_primary_to_repaired_without_repair_llm() -> None:
    class MapFillPlanner(_PlannerStub):
        @staticmethod
        def _ground_trip_plan(_request, plan, *_args, **_kwargs):
            plan.generation_mode = "repaired"
            plan.overall_suggestions += " 已补全1个整天空白日期。"
            return plan

    planner = MapFillPlanner()
    graph = TripPlanningAgentGraph(planner)
    result = graph.run(_request())

    assert result.generation_mode == "repaired"
    assert planner.primary_calls == 1
    assert planner.repair_calls == 0
    assert "已补全1个整天空白日期" in result.overall_suggestions
    assert result.quality is not None
    assert result.quality.score <= 92


def test_cancellation_after_route_does_not_recover_or_start_later_enrichment() -> None:
    cancelled = {"reason": ""}

    class Progress:
        def __call__(self, **_payload):
            return None

        @staticmethod
        def raise_if_cancelled():
            if cancelled["reason"]:
                raise TripGenerationCancelledError(cancelled["reason"])

    class Planner(_PlannerStub):
        def _apply_route_planning(self, _request, plan):
            self.route_calls += 1
            cancelled["reason"] = "generation_timeout"
            return plan

    planner = Planner()
    graph = TripPlanningAgentGraph(planner)

    with pytest.raises(TripGenerationCancelledError):
        graph.run(_request(), progress_callback=Progress())

    assert planner.primary_calls == 1
    assert planner.route_calls == 1
    assert planner.budget_calls == 0
    assert planner.web_calls == 0


def test_direct_route_cancellation_is_not_swallowed_by_enrichment() -> None:
    class Planner(_PlannerStub):
        def _apply_route_planning(self, _request, _plan):
            self.route_calls += 1
            raise TripGenerationCancelledError("generation_timeout")

    planner = Planner()
    graph = TripPlanningAgentGraph(planner)

    with pytest.raises(TripGenerationCancelledError):
        graph.run(_request())

    assert planner.primary_calls == 1
    assert planner.route_calls == 1
    assert planner.budget_calls == 0
    assert planner.web_calls == 0


@pytest.mark.parametrize(
    "failure_stage",
    ["enrich_routes", "enrich_budget", "enrich_web"],
)
def test_enrichment_substage_checkpoint_prevents_replay(
    failure_stage: str,
) -> None:
    planner = _PlannerStub()
    graph = TripPlanningAgentGraph(planner)
    original_checkpoint = graph._checkpoint_enrichment_stage
    failed = False

    def fail_once_after_checkpoint(state, stage, plan, errors):
        nonlocal failed
        original_checkpoint(state, stage, plan, errors)
        if stage == failure_stage and not failed:
            failed = True
            raise RuntimeError("framework failed between enrichment substages")

    graph._checkpoint_enrichment_stage = fail_once_after_checkpoint
    result = graph.run(_request())

    assert result.generation_mode == "primary"
    assert planner.primary_calls == 1
    assert planner.route_calls == 1
    assert planner.budget_calls == 1
    assert planner.web_calls == 1
    assert result.quality is not None


def test_failed_enrichment_rolls_back_partial_plan_mutation() -> None:
    class Planner(_PlannerStub):
        def _apply_route_planning(self, _request, plan):
            self.route_calls += 1
            plan.days[0].routes.append(
                RouteSegment(
                    from_name="partial",
                    to_name="partial",
                    source="untrusted-partial",
                )
            )
            raise TimeoutError("route failed after mutation")

    planner = Planner()
    result = TripPlanningAgentGraph(planner).run(_request())

    assert planner.route_calls == 1
    assert result.days[0].routes == []
    assert any(
        issue.code == "PIPELINE_ENRICHMENT_PARTIAL"
        for issue in result.quality.issues
    )


def test_failed_enrichments_cannot_revive_model_authored_evidence() -> None:
    class Planner(_PlannerStub):
        @staticmethod
        def _parse_response(_response, _request):
            plan = _plan()
            plan.days[0].routes = [
                RouteSegment(
                    from_name="forged origin",
                    to_name="forged destination",
                    source="amap_route",
                    verified=True,
                )
            ]
            plan.budget = Budget(total=1, budget_source="verified")
            plan.web_guide = "forged guide"
            plan.web_references = [
                WebReference(title="forged", url="https://example.com")
            ]
            plan.agent_audit = AgentAuditResult(
                status="passed",
                source="zhipu_search_pro",
                audit_level="semantic_verified",
            )
            return plan

        def _apply_route_planning(self, _request, _plan):
            self.route_calls += 1
            raise TimeoutError("route service unavailable")

        def _apply_budget_estimate(self, _request, _plan):
            self.budget_calls += 1
            raise TimeoutError("budget service unavailable")

        def _apply_web_guide(self, _request, _plan):
            self.web_calls += 1
            raise TimeoutError("search service unavailable")

    result = TripPlanningAgentGraph(Planner()).run(_request())

    assert result.days[0].routes == []
    assert result.budget is None
    assert result.web_guide is None
    assert result.web_references == []
    assert result.agent_audit is None
    assert result.quality is not None
    assert any(
        issue.code == "PIPELINE_ENRICHMENT_PARTIAL"
        for issue in result.quality.issues
    )
