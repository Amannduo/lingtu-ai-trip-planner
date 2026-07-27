from __future__ import annotations

from datetime import date, timedelta

import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.auth import get_current_user, get_optional_current_user
from app.api.main import app
from app.api.routes.trip import _validate_generation_request
from app.config import get_settings
from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import (
    AgentAuditResult,
    Attraction,
    Budget,
    DayPlan,
    Hotel,
    Location,
    Meal,
    RouteSegment,
    TripPlan,
    TripPlanQualityResult,
    TripRequest,
    WeatherInfo,
    WebReference,
)
from app.services.auth_service import AuthenticatedUser
from app.services.request_rate_limit_service import RequestRateLimitService
from app.services.trip_generation_job_service import (
    TripGenerationCancelledError,
    TripGenerationCapacityError,
    TripGenerationJobService,
)
from app.services.trip_plan_quality_service import TripPlanQualityService
from app.services.transport_budget_service import TransportBudgetService


def make_request(city: str = "北京") -> TripRequest:
    return TripRequest(
        city=city,
        start_date="2026-08-01",
        end_date="2026-08-01",
        travel_days=1,
        travelers=1,
        transportation="公共交通",
        accommodation="舒适型酒店",
        preferences=[],
    )


def make_plan(city: str = "北京") -> TripPlan:
    return TripPlan(
        city=city,
        start_date="2026-08-01",
        end_date="2026-08-01",
        overall_suggestions="按计划出行",
        weather_info=[
            WeatherInfo(
                date="2026-08-01",
                day_weather="晴",
                night_weather="多云",
                day_temp=30,
                night_temp=22,
                wind_direction="东风",
                wind_power="1-2级",
            )
        ],
        days=[
            DayPlan(
                date="2026-08-01",
                day_index=0,
                description="城市漫游",
                transportation="公共交通",
                accommodation="舒适型酒店",
                attractions=[
                    Attraction(
                        name="故宫博物院",
                        address="北京市东城区",
                        location=Location(longitude=116.397, latitude=39.918),
                        visit_duration=180,
                        description="历史文化景点",
                        poi_id="amap-1",
                        coordinate_source="amap_poi",
                        ticket_price=50,
                    )
                ],
                meals=[
                    Meal(
                        type="breakfast",
                        name="早餐店",
                        address="北京市东城区早餐街1号",
                        location=Location(longitude=116.398, latitude=39.918),
                        estimated_cost=20,
                        poi_id="meal-breakfast",
                        coordinate_source="amap_poi",
                    ),
                    Meal(
                        type="lunch",
                        name="午餐店",
                        address="北京市东城区午餐街1号",
                        location=Location(longitude=116.399, latitude=39.918),
                        estimated_cost=40,
                        poi_id="meal-lunch",
                        coordinate_source="amap_poi",
                    ),
                    Meal(
                        type="dinner",
                        name="晚餐店",
                        address="北京市东城区晚餐街1号",
                        location=Location(longitude=116.400, latitude=39.918),
                        estimated_cost=60,
                        poi_id="meal-dinner",
                        coordinate_source="amap_poi",
                    ),
                ],
            )
        ],
        budget=Budget(
            total_attractions=50,
            total_meals=120,
            total_transportation=25,
            total=195,
            local_transportation=25,
        ),
        agent_audit=AgentAuditResult(
            status="passed",
            source="zhipu_search_pro",
            audit_level="semantic_verified",
            checked_items=["结构与动态信息"],
        ),
        web_references=[
            WebReference(
                title="北京市文化和旅游局",
                url="https://example.com/beijing-travel",
                site_name="官方来源",
            )
        ],
        quality=TripPlanQualityResult(
            status="passed",
            score=90,
            publishable=True,
        ),
    )


def test_quality_gate_passes_consistent_plan() -> None:
    result = TripPlanQualityService().evaluate(make_request(), make_plan())
    assert result.status == "passed"
    assert result.score == 100
    assert result.constraint_score == 100
    assert result.executability_score == 100
    assert result.evidence_score == 100
    assert result.readiness_score == 100
    assert result.publishable is True
    assert result.verified_facts == 5


def test_repaired_mode_explains_structure_or_trusted_map_completion() -> None:
    plan = make_plan()
    plan.generation_mode = "repaired"

    result = TripPlanQualityService().evaluate(make_request(), plan)
    issue = next(
        item for item in result.issues
        if item.code == "MODEL_OUTPUT_REPAIRED"
    )

    assert "结构校正或地图可信补全" in issue.message
    assert result.score <= 92


def test_quality_gate_summarizes_web_audit_without_copying_every_issue() -> None:
    plan = make_plan()
    plan.agent_audit = AgentAuditResult(
        status="warning",
        source="local_fallback",
        issues=["搜索不可用", "天气需复核"],
        suggestions=["查看官方页面"],
    )

    result = TripPlanQualityService().evaluate(make_request(), plan)

    web_issues = [
        issue for issue in result.issues
        if issue.code.startswith("WEB_AUDIT")
    ]
    assert len(web_issues) == 1
    assert web_issues[0].code == "WEB_AUDIT_WARNING"
    assert "2项内容需要复核" in web_issues[0].message
    assert "搜索不可用" not in web_issues[0].message


def test_planner_records_structured_audit_when_web_stage_crashes() -> None:
    class FailingWebGuide:
        def apply_to_plan(self, _request, _plan):
            raise RuntimeError("provider detail")

    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    planner.web_guide_agent = FailingWebGuide()

    result = planner._apply_web_guide(make_request(), make_plan())

    assert result.agent_audit is not None
    assert result.agent_audit.status == "warning"
    assert result.agent_audit.source == "local_fallback"
    assert "RuntimeError" in result.agent_audit.issues[0]
    assert "provider detail" not in result.agent_audit.issues[0]


def test_precise_short_trip_and_self_drive_budget_use_ground_transport_units() -> None:
    service = TransportBudgetService.__new__(TransportBudgetService)
    # __new__ path: avoid FlyAI CLI when unit-testing heuristics only.
    service.flyai_enabled = False
    base = make_request("麟游县").model_copy(
        update={
            "origin_city": "宝鸡扶风",
            "travelers": 3,
            "travel_days": 2,
            "start_date": "2026-08-01",
            "end_date": "2026-08-02",
            "intercity_transportation": "自动选择",
        }
    )

    short_haul = service._estimate_intercity_transport(base)
    self_drive = service._estimate_intercity_transport(
        base.model_copy(update={"intercity_transportation": "自驾"})
    )

    # FlyAI disabled: use ground heuristics available on this branch.
    assert short_haul.source in {"heuristic_short_haul", "heuristic_transport"}
    assert short_haul.total_price > 0
    assert self_drive.source == "heuristic_drive"
    assert self_drive.unit_price == 400
    assert self_drive.total_price == 400 * base.travelers


def test_quality_gate_fails_city_mismatch() -> None:
    result = TripPlanQualityService().evaluate(make_request("上海"), make_plan("北京"))
    assert result.status == "failed"
    assert any(issue.code == "CITY_MISMATCH" for issue in result.issues)


def test_job_events_are_ordered_and_replayable() -> None:
    service = TripGenerationJobService(ttl_seconds=60)

    def worker(progress):
        progress(stage="attractions", progress=20, message="景点完成")
        progress(stage="routes", progress=80, message="路线完成")
        return {"success": True}

    job = service.start("user:test", worker)
    events = list(service.events(job, heartbeat_seconds=0.01))
    assert [event["type"] for event in events] == ["stage", "stage", "result"]
    assert [event["id"] for event in events] == [1, 2, 3]
    replay = list(service.events(job, after_id=1, heartbeat_seconds=0.01))
    assert [event["id"] for event in replay] == [2, 3]
    assert service.get(job.job_id, "user:other", job.access_token) is None
    assert service.get(job.job_id, "user:test", "wrong-token") is None
    assert service.get(job.job_id, "user:test", job.access_token) is job
    service.shutdown()



def test_sync_generation_timeout_never_starts_persistence(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    effects: list[str] = []

    class BlockingPlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None):
            started.set()
            try:
                release.wait(timeout=2)
                if progress_callback is not None:
                    progress_callback.raise_if_cancelled()
                return make_plan()
            finally:
                finished.set()

    class ForbiddenDataService:
        @staticmethod
        def save_trip_plan(*_args, **_kwargs):
            effects.append("save")
            return "P-LATE"

    monkeypatch.setattr(
        get_settings(),
        "trip_generation_max_runtime_seconds",
        0.03,
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: BlockingPlanner(),
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_travel_plan_data_service",
        lambda: ForbiddenDataService(),
    )
    app.dependency_overrides[get_optional_current_user] = lambda: AuthenticatedUser(
        user_id="sync-timeout",
        username="sync-timeout",
        email=None,
        role="user",
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/trip/plan",
                json=make_request().model_dump(mode="json"),
            )
        assert started.is_set()
        assert response.status_code == 504
        assert effects == []
    finally:
        release.set()
        assert finished.wait(timeout=2)
        app.dependency_overrides.pop(get_optional_current_user, None)


def test_sync_finalization_boundary_returns_timeout_without_persistence(
    monkeypatch,
) -> None:
    effects: list[str] = []
    plan = make_plan()
    plan.quality = TripPlanQualityResult(
        status="passed",
        score=90,
        publishable=True,
    )

    class ExpiredBoundaryToken:
        @staticmethod
        def cancel(_reason: str) -> bool:
            return True

        @staticmethod
        def begin_finalization() -> None:
            raise TripGenerationCancelledError("generation_timeout")

    async def completed_on_deadline(*_args, **_kwargs):
        return plan, ExpiredBoundaryToken()

    class ForbiddenDataService:
        @staticmethod
        def save_trip_plan(*_args, **_kwargs):
            effects.append("save")
            return "P-LATE"

    isolated_rate_limiter = RequestRateLimitService()
    monkeypatch.setattr(
        "app.api.routes.trip.get_request_rate_limit_service",
        lambda *_args, **_kwargs: isolated_rate_limiter,
    )
    monkeypatch.setattr(
        "app.api.routes.trip._generate_sync_with_deadline",
        completed_on_deadline,
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_travel_plan_data_service",
        lambda: ForbiddenDataService(),
    )
    app.dependency_overrides[get_optional_current_user] = lambda: AuthenticatedUser(
        user_id="sync-boundary",
        username="sync-boundary",
        email=None,
        role="user",
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/trip/plan",
                json=make_request().model_dump(mode="json"),
            )
        assert response.status_code == 504
        assert effects == []
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)


def test_job_timeout_uses_monotonic_clock_during_wall_clock_rollback(
    monkeypatch,
) -> None:
    service = TripGenerationJobService(
        ttl_seconds=60,
        max_runtime_seconds=0.03,
    )
    release = threading.Event()

    def worker(_progress):
        release.wait(timeout=1)
        return {"success": True}

    job = service.start("user:clock", worker)
    monkeypatch.setattr(
        "app.services.trip_generation_job_service.time.time",
        lambda: -10_000_000.0,
    )

    events = [
        event
        for event in service.events(job, heartbeat_seconds=0.005)
        if event is not None
    ]
    release.set()

    assert [event["type"] for event in events] == ["error"]
    assert events[0]["error_type"] == "generation_timeout"
    assert job.terminal is True
    service.shutdown()


def test_finalization_lease_prevents_timeout_then_late_side_effect() -> None:
    service = TripGenerationJobService(
        ttl_seconds=60,
        max_runtime_seconds=0.03,
    )
    entered = threading.Event()
    release = threading.Event()
    effects: list[str] = []

    def worker(progress):
        progress.begin_finalization()
        effects.append("started")
        entered.set()
        release.wait(timeout=1)
        effects.append("committed")
        return {"success": True}

    job = service.start("user:finalizing", worker)
    assert entered.wait(timeout=1)
    time.sleep(0.05)

    assert job.expire_if_needed() is False
    assert job.terminal is False

    release.set()
    events = list(service.events(job, heartbeat_seconds=0.005))

    assert effects == ["started", "committed"]
    assert [event["type"] for event in events] == ["result"]
    assert job.finalizing is True
    service.shutdown()


def test_result_publication_atomically_rejects_elapsed_deadline() -> None:
    service = TripGenerationJobService(
        ttl_seconds=60,
        max_runtime_seconds=0.02,
    )

    def worker(_progress):
        time.sleep(0.04)
        return {"success": True}

    job = service.start("user:atomic-complete", worker)
    events = [
        event
        for event in service.events(job, heartbeat_seconds=0.005)
        if event is not None
    ]

    assert [event["type"] for event in events] == ["error"]
    assert events[0]["error_type"] == "generation_timeout"
    assert not any(event["type"] == "result" for event in job.events)
    service.shutdown()

def test_cancel_before_finalization_cannot_publish_result() -> None:
    service = TripGenerationJobService(
        ttl_seconds=60,
        max_runtime_seconds=60,
    )
    accepted: list[bool] = []

    def worker(progress):
        accepted.append(
            progress.cancellation_token.cancel("generation_cancelled")
        )
        return {"success": True}

    job = service.start("user:cancel-before-finalization", worker)
    events = [
        event
        for event in service.events(job, heartbeat_seconds=0.005)
        if event is not None
    ]

    assert accepted == [True]
    assert [event["type"] for event in events] == ["error"]
    assert events[0]["error_type"] == "generation_cancelled"
    assert not any(event["type"] == "result" for event in job.events)
    service.shutdown()


def test_finalization_lease_rejects_late_cancellation() -> None:
    service = TripGenerationJobService(
        ttl_seconds=60,
        max_runtime_seconds=60,
    )
    entered = threading.Event()
    release = threading.Event()

    def worker(progress):
        progress.begin_finalization()
        entered.set()
        release.wait(timeout=1)
        return {"success": True}

    job = service.start("user:cancel-after-finalization", worker)
    assert entered.wait(timeout=1)
    assert job.cancellation_token.cancel("generation_cancelled") is False
    release.set()
    events = [
        event
        for event in service.events(job, heartbeat_seconds=0.005)
        if event is not None
    ]

    assert [event["type"] for event in events] == ["result"]
    assert job.cancellation_token.is_cancelled is False
    service.shutdown()


def test_cancel_and_finalization_claim_have_exactly_one_winner() -> None:
    service = TripGenerationJobService(
        ttl_seconds=60,
        max_runtime_seconds=60,
        max_jobs=100,
    )

    for index in range(20):
        gate = threading.Barrier(2)

        def worker(progress):
            gate.wait(timeout=1)
            progress.begin_finalization()
            return {"success": True}

        job = service.start(f"user:claim-race:{index}", worker)
        gate.wait(timeout=1)
        cancellation_won = job.cancellation_token.cancel(
            "generation_cancelled"
        )
        events = [
            event
            for event in service.events(job, heartbeat_seconds=0.005)
            if event is not None
        ]
        with job.condition:
            assert job.condition.wait_for(
                lambda: job.worker_finished,
                timeout=1,
            )

        if cancellation_won:
            assert [event["type"] for event in events] == ["error"]
            assert events[0]["error_type"] == "generation_cancelled"
        else:
            assert [event["type"] for event in events] == ["result"]
            assert job.cancellation_token.is_cancelled is False

    service.shutdown()

def test_job_capacity_is_bounded_per_owner() -> None:
    service = TripGenerationJobService(
        ttl_seconds=60,
        max_workers=1,
        max_pending_jobs=2,
        max_jobs_per_owner=1,
    )
    release = threading.Event()

    def blocking_worker(_progress):
        release.wait(timeout=2)
        return {"success": True}

    first = service.start("user:test", blocking_worker)
    with pytest.raises(TripGenerationCapacityError):
        service.start("user:test", blocking_worker)

    release.set()
    list(service.events(first, heartbeat_seconds=0.01))
    service.shutdown()


def test_job_error_does_not_expose_internal_exception() -> None:
    service = TripGenerationJobService(ttl_seconds=60)

    def failing_worker(_progress):
        raise RuntimeError("secret-provider-token-and-local-path")

    job = service.start("user:test", failing_worker)
    events = list(service.events(job, heartbeat_seconds=0.01))
    error = events[-1]
    assert error["type"] == "error"
    assert error["error_type"] == "generation_failed"
    assert "secret-provider-token" not in error["message"]
    service.shutdown()


def test_plan_job_api_stream_requires_token_and_returns_events(monkeypatch) -> None:
    service = TripGenerationJobService(ttl_seconds=60)

    class FakePlanner:
        @staticmethod
        def plan_trip(request, progress_callback=None):
            if progress_callback:
                progress_callback(
                    stage="attractions",
                    progress=22,
                    message="已完成景点检索",
                    detail="找到 1 个可靠地点",
                    meta={"candidate_count": 1},
                )
            return make_plan(request.city)

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_generation_job_service",
        lambda: service,
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )

    payload = make_request().model_dump(mode="json")
    with TestClient(app, base_url="https://testserver") as client:
        created = client.post("/api/trip/plan-jobs", json=payload)
        assert created.status_code == 200
        stream_url = created.json()["stream_url"]
        job_id = created.json()["job_id"]
        leaked_token = service._jobs[job_id].access_token
        assert "token=" not in stream_url
        assert "httponly" in created.headers["set-cookie"].lower()
        assert "samesite=strict" in created.headers["set-cookie"].lower()
        assert "secure" in created.headers["set-cookie"].lower()

        with TestClient(app) as isolated_client:
            assert isolated_client.get(stream_url).status_code == 404
            assert isolated_client.get(stream_url + "?token=wrong").status_code == 404
            assert isolated_client.get(stream_url + f"?token={leaked_token}").status_code == 404

        streamed = client.get(stream_url)
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("text/event-stream")
        assert "event: stage" in streamed.text
        assert "event: result" in streamed.text
        assert "找到 1 个可靠地点" in streamed.text
        assert '"stage":"finalizing"' in streamed.text
        assert '"progress":99' in streamed.text

    service.shutdown()


def test_unpublishable_plan_never_leases_or_triggers_delivery(monkeypatch) -> None:
    service = TripGenerationJobService(ttl_seconds=60)
    effects: list[str] = []
    inconsistent_plan = make_plan()
    inconsistent_plan.quality = TripPlanQualityResult(
        status="failed",
        score=0,
        publishable=False,
        issues=[],
    )

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None):
            # Stub bypasses public hard-gate; worker defense must still fail.
            return inconsistent_plan.model_copy(deep=True)

    class FakeDataService:
        @staticmethod
        def save_trip_plan(*_args, **_kwargs):
            effects.append("save")
            return "P-UNSAFE"

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_generation_job_service",
        lambda: service,
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_travel_plan_data_service",
        lambda: FakeDataService(),
    )
    monkeypatch.setattr(
        "app.api.routes.trip.notify_trip_plan_ready",
        lambda *_args, **_kwargs: effects.append("push"),
    )
    monkeypatch.setattr(
        "app.api.routes.trip.deliver_trip_plan_email",
        lambda *_args, **_kwargs: effects.append("email"),
    )
    app.dependency_overrides[get_optional_current_user] = lambda: AuthenticatedUser(
        user_id="quality-gate-user",
        username="quality-gate-user",
        email="quality@example.com",
        role="user",
    )

    try:
        payload = make_request().model_copy(
            update={
                "email_on_completion": True,
                "delivery_email": "quality@example.com",
            }
        ).model_dump(mode="json")
        with TestClient(app, base_url="https://testserver") as client:
            created = client.post("/api/trip/plan-jobs", json=payload)
            assert created.status_code == 200
            streamed = client.get(created.json()["stream_url"])
            assert streamed.status_code == 200
            job = service._jobs[created.json()["job_id"]]

        assert "event: error" in streamed.text
        assert "event: result" not in streamed.text
        assert "TRIP_PLAN_QUALITY_REJECTED" in streamed.text or "quality_rejected" in streamed.text
        assert effects == []
        assert job.finalizing is False
        assert job.status == "failed"
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)
        service.shutdown()

def test_plan_job_cookie_honors_forced_secure_setting(monkeypatch) -> None:
    service = TripGenerationJobService(ttl_seconds=60)

    class FakePlanner:
        @staticmethod
        def plan_trip(request, progress_callback=None):
            return make_plan(request.city)

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_generation_job_service",
        lambda: service,
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: FakePlanner(),
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_settings",
        lambda: SimpleNamespace(auth_cookie_secure=True),
    )

    payload = make_request().model_dump(mode="json")
    with TestClient(app, base_url="http://testserver") as client:
        created = client.post("/api/trip/plan-jobs", json=payload)
        assert created.status_code == 200
        set_cookie = created.headers["set-cookie"].lower()
        assert "secure" in set_cookie
        assert "httponly" in set_cookie
        assert "samesite=strict" in set_cookie

    service.shutdown()


def test_update_recomputes_quality_and_restores_trusted_facts(monkeypatch) -> None:
    existing = make_plan()
    request = make_request()
    captured = {}

    class FakeDataService:
        @staticmethod
        def get_trip_plan(_plan_no, _user_id):
            return existing.model_copy(deep=True)

        @staticmethod
        def get_trip_request(_plan_no, _user_id):
            return request.model_copy(deep=True)

        @staticmethod
        def update_trip_plan(_plan_no, _user_id, plan):
            captured["plan"] = plan.model_copy(deep=True)
            return True

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id="user-test",
        username="tester",
        email="tester@example.com",
        role="user",
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_travel_plan_data_service",
        lambda: FakeDataService(),
    )

    edited = existing.model_copy(deep=True)
    edited.quality = TripPlanQualityResult(status="passed", score=100)
    edited.days[0].attractions[0].location = Location(longitude=0, latitude=0)
    edited.days[0].attractions[0].coordinate_source = "amap_poi"

    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/trip/history/P-TEST",
                json=edited.model_dump(mode="json"),
            )
        assert response.status_code == 200
        saved = captured["plan"]
        assert saved.days[0].attractions[0].location.longitude == pytest.approx(116.397)
        # Client-forged perfect quality must be recomputed from trusted facts.
        assert saved.quality is not None
        assert saved.quality is not edited.quality
        assert saved.quality.status in {"passed", "warning"}
        assert saved.quality.verified_facts == 5
        # make_plan() is a consistent fixture: recompute stays publishable/passed
        # unless the caller injects audit warnings. Coordinate restore is the
        # hard trust requirement of this case.
        assert saved.quality.publishable is True
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_update_rejects_critical_quality_failure(monkeypatch) -> None:
    existing = make_plan()
    request = make_request()
    updated = False

    class FakeDataService:
        @staticmethod
        def get_trip_plan(_plan_no, _user_id):
            return existing.model_copy(deep=True)

        @staticmethod
        def get_trip_request(_plan_no, _user_id):
            return request.model_copy(deep=True)

        @staticmethod
        def update_trip_plan(_plan_no, _user_id, _plan):
            nonlocal updated
            updated = True
            return True

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id="user-test",
        username="tester",
        email="tester@example.com",
        role="user",
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_travel_plan_data_service",
        lambda: FakeDataService(),
    )

    broken = existing.model_copy(deep=True)
    broken.days = []
    broken.quality = TripPlanQualityResult(status="passed", score=100)

    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/trip/history/P-TEST",
                json=broken.model_dump(mode="json"),
            )
        assert response.status_code == 422
        assert response.json()["detail"]["message"] == "修改后的行程存在关键问题，未保存。"
        assert updated is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)



@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("city", "上海"),
        ("start_date", "2026-08-02"),
        ("end_date", "2026-08-02"),
    ],
)
def test_update_rejects_city_or_date_change(monkeypatch, field, value) -> None:
    existing = make_plan()
    request = make_request()
    updated = False

    class FakeDataService:
        @staticmethod
        def get_trip_plan(_plan_no, _user_id):
            return existing.model_copy(deep=True)

        @staticmethod
        def get_trip_request(_plan_no, _user_id):
            return request.model_copy(deep=True)

        @staticmethod
        def update_trip_plan(_plan_no, _user_id, _plan):
            nonlocal updated
            updated = True
            return True

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id="user-test",
        username="tester",
        email="tester@example.com",
        role="user",
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_travel_plan_data_service",
        lambda: FakeDataService(),
    )
    edited = existing.model_copy(deep=True)
    setattr(edited, field, value)

    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/trip/history/P-TEST",
                json=edited.model_dump(mode="json"),
            )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["message"] == "目的地和出行日期不能直接修改，请重新生成行程。"
        assert detail["issues"][0]["code"] == "TRIP_IDENTITY_IMMUTABLE"
        assert detail["issues"][0]["path"] == field
        assert updated is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_update_restores_poi_identity_not_just_coordinates(monkeypatch) -> None:
    existing = make_plan()
    trusted = existing.days[0].attractions[0]
    trusted.category = "博物馆"
    trusted.rating = 4.9
    trusted.photos = ["https://example.com/trusted.jpg"]
    trusted.image_url = "https://example.com/cover.jpg"
    trusted.ticket_price = 60
    request = make_request()
    captured = {}

    class FakeDataService:
        @staticmethod
        def get_trip_plan(_plan_no, _user_id):
            return existing.model_copy(deep=True)

        @staticmethod
        def get_trip_request(_plan_no, _user_id):
            return request.model_copy(deep=True)

        @staticmethod
        def update_trip_plan(_plan_no, _user_id, plan):
            captured["plan"] = plan.model_copy(deep=True)
            return True

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id="user-test",
        username="tester",
        email="tester@example.com",
        role="user",
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_travel_plan_data_service",
        lambda: FakeDataService(),
    )
    edited = existing.model_copy(deep=True)
    forged = edited.days[0].attractions[0]
    forged.name = "伪造景点"
    forged.address = "伪造地址"
    forged.category = "伪造类别"
    forged.rating = 1.0
    forged.photos = ["https://evil.example/forged.jpg"]
    forged.image_url = "https://evil.example/cover.jpg"
    forged.ticket_price = 9999
    forged.description = "用户修改后的描述"
    forged.visit_duration = 120

    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/trip/history/P-TEST",
                json=edited.model_dump(mode="json"),
            )
        assert response.status_code == 200
        saved = captured["plan"].days[0].attractions[0]
        for field in (
            "name",
            "address",
            "poi_id",
            "location",
            "coordinate_source",
            "category",
            "rating",
            "photos",
            "image_url",
            "ticket_price",
        ):
            assert getattr(saved, field) == getattr(trusted, field)
        assert saved.description == "用户修改后的描述"
        assert saved.visit_duration == 120
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_update_restores_hotel_facts(monkeypatch) -> None:
    existing = make_plan()
    existing.days[0].hotel = Hotel(
        name="可信酒店",
        address="可信地址",
        location=Location(longitude=116.40, latitude=39.91),
        price_range="500-700",
        rating="4.8",
        distance="距景点1公里",
        type="舒适型",
        estimated_cost=600,
        poi_id="hotel-1",
        selection_reason="交通方便",
    )
    request = make_request()
    captured = {}

    class FakeDataService:
        @staticmethod
        def get_trip_plan(_plan_no, _user_id):
            return existing.model_copy(deep=True)

        @staticmethod
        def get_trip_request(_plan_no, _user_id):
            return request.model_copy(deep=True)

        @staticmethod
        def update_trip_plan(_plan_no, _user_id, plan):
            captured["plan"] = plan.model_copy(deep=True)
            return True

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id="user-test",
        username="tester",
        email="tester@example.com",
        role="user",
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_travel_plan_data_service",
        lambda: FakeDataService(),
    )
    edited = existing.model_copy(deep=True)
    edited.days[0].hotel = Hotel(
        name="伪造酒店",
        address="伪造地址",
        location=Location(longitude=0, latitude=0),
        rating="5.0",
        estimated_cost=1,
        poi_id="forged-hotel",
    )

    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/trip/history/P-TEST",
                json=edited.model_dump(mode="json"),
            )
        assert response.status_code == 200
        assert captured["plan"].days[0].hotel == existing.days[0].hotel
    finally:
        app.dependency_overrides.pop(get_current_user, None)



def test_quality_gate_does_not_trust_route_with_wrong_endpoints() -> None:
    plan = make_plan()
    plan.days[0].attractions.append(
        Attraction(
            name="天坛公园",
            address="北京市东城区",
            location=Location(longitude=116.417, latitude=39.883),
            visit_duration=120,
            description="历史建筑",
            poi_id="amap-2",
            coordinate_source="amap_poi",
        )
    )
    plan.days[0].routes = [
        RouteSegment(
            from_name="伪造起点",
            to_name="伪造终点",
            distance=100,
            duration=60,
            source="amap",
            verified=True,
        )
    ]

    result = TripPlanQualityService().evaluate(make_request(), plan)

    assert any(issue.code == "ROUTE_ENDPOINT_MISMATCH" for issue in result.issues)
    assert result.verified_facts == 6


def test_quality_gate_rejects_infeasible_auto_recommended_weekend_trip() -> None:
    request = TripRequest(
        origin_city="宝鸡",
        city="昆明",
        destination_source="recommendation",
        start_date="2026-08-01",
        end_date="2026-08-01",
        travel_days=1,
        travelers=1,
        transportation="公共交通",
        accommodation="舒适型酒店",
        preferences=[],
    )

    plan = make_plan("昆明")
    plan.days[0].attractions[0].address = "昆明市五华区"
    result = TripPlanQualityService().evaluate(request, plan)

    assert result.status == "failed"
    assert any(
        issue.code == "SHORT_TRIP_DESTINATION_UNREACHABLE"
        and issue.severity == "error"
        for issue in result.issues
    )


def test_quality_gate_warns_but_preserves_explicit_far_destination() -> None:
    request = TripRequest(
        origin_city="宝鸡",
        city="昆明",
        destination_source="manual",
        start_date="2026-08-01",
        end_date="2026-08-01",
        travel_days=1,
        travelers=1,
        transportation="公共交通",
        accommodation="舒适型酒店",
        preferences=[],
    )

    plan = make_plan("昆明")
    plan.days[0].attractions[0].address = "昆明市五华区"
    result = TripPlanQualityService().evaluate(request, plan)

    assert result.status == "warning"
    assert any(issue.code == "SHORT_TRIP_DESTINATION_RISK" for issue in result.issues)


def test_quality_gate_rejects_plan_level_date_range_mismatch() -> None:
    plan = make_plan()
    plan.start_date = "2026-08-02"
    plan.end_date = "2026-08-02"

    result = TripPlanQualityService().evaluate(make_request(), plan)

    assert result.status == "failed"
    assert any(issue.code == "PLAN_DATE_RANGE_MISMATCH" for issue in result.issues)


@pytest.mark.parametrize("path", ["/api/trip/plan", "/api/trip/plan-jobs"])
def test_past_trip_is_rejected_before_planner_call(monkeypatch, path) -> None:
    planner_factory_calls: list[bool] = []
    isolated_rate_limiter = RequestRateLimitService()

    monkeypatch.setattr(
        "app.api.routes.trip.get_request_rate_limit_service",
        lambda *_args, **_kwargs: isolated_rate_limiter,
    )

    def forbidden_planner_factory():
        planner_factory_calls.append(True)
        raise AssertionError("planner must not be created for a past trip")

    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        forbidden_planner_factory,
    )
    past = (date.today() - timedelta(days=1)).isoformat()
    payload = make_request().model_copy(
        update={
            "start_date": past,
            "end_date": past,
            "travel_days": 1,
        }
    ).model_dump(mode="json")

    with TestClient(app) as client:
        response = client.post(path, json=payload)

    assert response.status_code == 422
    assert "不能早于今天" in str(response.json().get("detail", ""))
    assert planner_factory_calls == []

def test_preflight_rejects_stale_infeasible_recommendation() -> None:
    request = TripRequest(
        origin_city="宝鸡",
        city="乌鲁木齐",
        destination_source="recommendation",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=1,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
    )

    with pytest.raises(Exception) as exc_info:
        _validate_generation_request(request)

    assert getattr(exc_info.value, "status_code", None) == 422
    assert "短途圈" in str(getattr(exc_info.value, "detail", ""))


def test_planner_normalizes_duplicate_model_day_dates() -> None:
    request = TripRequest(
        city="西安",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=1,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
    )
    plan = make_plan("西安")
    second_day = plan.days[0].model_copy(deep=True)
    second_day.day_index = 0
    second_day.date = "2026-08-01"
    plan.days = [plan.days[0], second_day]
    plan.start_date = "2026-08-01"
    plan.end_date = "2026-08-01"

    normalized = MultiAgentTripPlanner._normalize_plan_dates_and_weather(
        MultiAgentTripPlanner.__new__(MultiAgentTripPlanner),
        request,
        plan,
    )

    assert normalized.start_date == "2026-08-01"
    assert normalized.end_date == "2026-08-02"
    assert [day.date for day in normalized.days] == ["2026-08-01", "2026-08-02"]
    assert [day.day_index for day in normalized.days] == [0, 1]


def test_planner_rejects_missing_model_days_so_fallback_can_take_over() -> None:
    request = TripRequest(
        city="西安",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=1,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
    )
    plan = make_plan("西安")

    with pytest.raises(ValueError, match="returned 1 days"):
        MultiAgentTripPlanner._normalize_plan_dates_and_weather(
            MultiAgentTripPlanner.__new__(MultiAgentTripPlanner),
            request,
            plan,
        )


def test_planner_truncates_extra_model_days_to_confirmed_window() -> None:
    request = TripRequest(
        city="西安",
        start_date="2026-08-01",
        end_date="2026-08-01",
        travel_days=1,
        travelers=1,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
    )
    plan = make_plan("西安")
    plan.days.append(plan.days[0].model_copy(deep=True))

    normalized = MultiAgentTripPlanner._normalize_plan_dates_and_weather(
        MultiAgentTripPlanner.__new__(MultiAgentTripPlanner),
        request,
        plan,
    )

    assert len(normalized.days) == 1
    assert normalized.days[0].date == "2026-08-01"

def test_unverified_meals_and_missing_budget_cannot_score_100() -> None:
    plan = make_plan()
    plan.days[0].meals = [
        Meal(type="breakfast", name="早餐推荐"),
        Meal(type="lunch", name="午餐推荐"),
        Meal(type="dinner", name="晚餐推荐"),
    ]
    plan.budget = None

    result = TripPlanQualityService().evaluate(make_request(), plan)
    codes = {issue.code for issue in result.issues}

    assert "UNVERIFIED_MEAL" in codes
    assert "BUDGET_MISSING" in codes
    assert "FACT_COVERAGE_INCOMPLETE" in codes
    assert result.status == "warning"
    assert result.score < 100
    # Reviewable model: soft coverage/budget issues stay deliverable with review.
    assert result.publishable is True
    assert result.review_required is True


def test_ordinary_overnight_plan_requires_verified_hotel() -> None:
    request = make_request().model_copy(
        update={
            "end_date": "2026-08-02",
            "travel_days": 2,
            "accommodation": "经济型酒店",
        }
    )
    plan = make_plan().model_copy(
        update={"end_date": "2026-08-02"},
        deep=True,
    )
    second_day = plan.days[0].model_copy(deep=True)
    second_day.date = "2026-08-02"
    second_day.day_index = 1
    second_day.attractions[0].name = "天坛公园"
    second_day.attractions[0].poi_id = "amap-2"
    plan.days.append(second_day)
    plan.weather_info.append(
        WeatherInfo(
            date="2026-08-02",
            day_weather="晴",
            night_weather="多云",
            day_temp=31,
            night_temp=23,
        )
    )
    plan.budget = Budget(
        total_hotels=220,
        total_meals=240,
        total_transportation=50,
        total=510,
        hotel_nights=1,
        hotel_rooms=1,
        hotel_unit_price=220,
        local_transportation=50,
    )

    result = TripPlanQualityService().evaluate(request, plan)

    assert any(issue.code == "HOTEL_GAP" for issue in result.issues)
    assert result.score < 100
    # Reviewable model: hotel gap is advisory; plan remains deliverable.
    assert result.publishable is True
    assert result.review_required is True


def test_placeholder_weather_does_not_satisfy_date_coverage() -> None:
    plan = make_plan()
    plan.weather_info = [
        WeatherInfo(
            date="2026-08-01",
            day_weather="未知",
            night_weather="未知",
            day_temp=0,
            night_temp=0,
        )
    ]
    service = TripPlanQualityService()
    service._forecast_check_dates = lambda _dates: ["2026-08-01"]

    result = service.evaluate(make_request(), plan)

    assert any(issue.code == "WEATHER_GAP" for issue in result.issues)
    assert result.verified_facts == 4
    assert result.score < 100


def test_more_than_three_museums_triggers_quality_warning() -> None:
    plan = make_plan()
    plan.days[0].attractions = [
        Attraction(
            name=f"测试博物馆{index}",
            address="北京市东城区",
            location=Location(longitude=116.39 + index * 0.001, latitude=39.918),
            visit_duration=90,
            description="馆藏展览",
            category="博物馆",
            poi_id=f"museum-{index}",
            coordinate_source="amap_poi",
        )
        for index in range(4)
    ]

    result = TripPlanQualityService().evaluate(make_request(), plan)

    assert any(issue.code == "TOO_MANY_MUSEUMS" for issue in result.issues)
    assert result.score < 100


def test_more_than_four_parks_triggers_quality_warning() -> None:
    plan = make_plan()
    plan.days[0].attractions = [
        Attraction(
            name=f"测试公园{index}",
            address="北京市东城区",
            location=Location(longitude=116.39 + index * 0.001, latitude=39.918),
            visit_duration=90,
            description="城市绿地",
            category="公园",
            poi_id=f"park-{index}",
            coordinate_source="amap_poi",
        )
        for index in range(5)
    ]

    result = TripPlanQualityService().evaluate(make_request(), plan)

    assert any(issue.code == "TOO_MANY_PARKS" for issue in result.issues)
    assert result.score < 100

def test_passed_web_audit_without_references_cannot_score_100() -> None:
    plan = make_plan()
    plan.agent_audit = AgentAuditResult(
        status="passed",
        source="custom_search",
        checked_items=["结构检查"],
    )
    plan.web_references = []

    result = TripPlanQualityService().evaluate(make_request(), plan)

    assert any(
        issue.code == "WEB_AUDIT_NO_REFERENCES"
        for issue in result.issues
    )
    assert result.score < 100



def _retime_single_day(
    request: TripRequest,
    plan: TripPlan,
    value: str = "2030-01-01",
) -> tuple[TripRequest, TripPlan]:
    request = request.model_copy(
        update={"start_date": value, "end_date": value, "travel_days": 1}
    )
    plan = plan.model_copy(
        update={"start_date": value, "end_date": value},
        deep=True,
    )
    plan.days[0].date = value
    plan.weather_info = []
    return request, plan


def test_format_only_audit_cannot_receive_perfect_quality_score() -> None:
    request, plan = _retime_single_day(make_request(), make_plan())
    assert plan.agent_audit is not None
    plan.agent_audit.audit_level = "format_only"

    result = TripPlanQualityService().evaluate(request, plan)
    codes = {issue.code for issue in result.issues}

    assert "WEB_AUDIT_FORMAT_ONLY" in codes
    assert result.status == "warning"
    assert result.readiness_score < 100
    assert result.score < 100


def test_far_future_weather_is_not_service_failure_but_lowers_readiness() -> None:
    request, plan = _retime_single_day(make_request(), make_plan())

    result = TripPlanQualityService().evaluate(request, plan)
    codes = {issue.code for issue in result.issues}

    assert "WEATHER_NOT_YET_AVAILABLE" in codes
    assert "WEATHER_GAP" not in codes
    assert result.status == "warning"
    assert result.readiness_score < 100
    assert result.score < 100


def test_impossible_daily_schedule_fails_and_cannot_be_published() -> None:
    request, plan = _retime_single_day(make_request(), make_plan())
    plan.days[0].attractions[0].visit_duration = 800

    result = TripPlanQualityService().evaluate(request, plan)

    assert any(
        issue.code == "DAY_SCHEDULE_IMPOSSIBLE"
        and issue.severity == "error"
        for issue in result.issues
    )
    assert result.status == "failed"
    assert result.score <= 59
    assert result.publishable is False


def test_each_overnight_hotel_must_be_individually_verified() -> None:
    request = make_request().model_copy(
        update={
            "start_date": "2030-01-01",
            "end_date": "2030-01-03",
            "travel_days": 3,
            "free_text_input": "跟爸妈去避暑，不想太累，慢一点",
        }
    )
    plan = make_plan().model_copy(
        update={"start_date": "2030-01-01", "end_date": "2030-01-03"},
        deep=True,
    )
    plan.days[0].date = "2030-01-01"
    for index, value in enumerate(("2030-01-02", "2030-01-03"), start=1):
        day = plan.days[0].model_copy(deep=True)
        day.date = value
        day.day_index = index
        day.attractions[0].name = f"北京可信景点{index + 1}"
        day.attractions[0].poi_id = f"amap-{index + 1}"
        plan.days.append(day)
    plan.weather_info = []
    plan.days[0].hotel = Hotel(
        name="测试酒店A",
        address="北京市东城区酒店路1号",
        location=Location(longitude=116.398, latitude=39.918),
        estimated_cost=220,
        poi_id="hotel-a",
    )
    plan.days[1].hotel = Hotel(
        name="测试酒店B",
        address="北京市东城区酒店路2号",
        location=Location(longitude=116.399, latitude=39.918),
        estimated_cost=220,
        poi_id="",
    )
    plan.budget = Budget(
        total_attractions=150,
        total_hotels=440,
        total_meals=360,
        total_transportation=75,
        total=1025,
        hotel_nights=2,
        hotel_rooms=1,
        hotel_unit_price=220,
        local_transportation=75,
        budget_source="地图酒店参考价",
        hotel_reference="测试酒店A、测试酒店B 地图酒店参考单晚 220 元",
    )

    result = TripPlanQualityService().evaluate(request, plan)
    hotel_issue = next(
        issue for issue in result.issues if issue.code == "UNVERIFIED_HOTEL"
    )

    assert "第2天测试酒店B" in hotel_issue.message
    # Reviewable model: unverified hotel is advisory unless severity error.
    assert result.publishable is True
    assert result.review_required is True


def test_relaxed_pace_understands_natural_family_travel_wording() -> None:
    request = make_request().model_copy(
        update={"free_text_input": "跟爸妈去避暑，不想太累，慢一点"}
    )

    assert TripPlanQualityService()._prefers_relaxed_pace(request) is True
