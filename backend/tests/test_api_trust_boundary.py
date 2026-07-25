from __future__ import annotations

import pytest
import threading

from fastapi.testclient import TestClient

from app.agents.web_travel_guide_agent import WebTravelGuideAgent
from app.api.auth import get_current_user, get_optional_current_user
from app.api.main import app
from app.api.routes.trip import (
    UntrustedTripEditError,
    _restore_verified_plan_facts,
)
from app.models.schemas import (
    Attraction,
    DayPlan,
    Location,
    Meal,
    RouteSegment,
    TripPlan,
    TripRequest,
)
from app.services.auth_service import AuthenticatedUser
from app.services.trip_generation_job_service import TripGenerationJobService


def _attraction(name: str, poi_id: str, longitude: float) -> Attraction:
    return Attraction(
        name=name,
        address=f"{name}可信地址",
        location=Location(longitude=longitude, latitude=39.9),
        visit_duration=90,
        description=f"{name}原描述",
        poi_id=poi_id,
        coordinate_source="amap_poi",
    )


def _plan() -> TripPlan:
    first = _attraction("景点甲", "poi-a", 116.3)
    second = _attraction("景点乙", "poi-b", 116.4)
    return TripPlan(
        city="北京",
        start_date="2030-01-01",
        end_date="2030-01-01",
        generation_mode="primary",
        overall_suggestions="服务端总体建议",
        days=[
            DayPlan(
                date="2030-01-01",
                day_index=0,
                description="服务端日程说明",
                transportation="公共交通",
                accommodation="舒适型酒店",
                attractions=[first, second],
                meals=[Meal(type="lunch", name="可信餐厅", poi_id="meal-1")],
                routes=[
                    RouteSegment(
                        from_name=first.name,
                        to_name=second.name,
                        description="可信路线说明",
                        verified=True,
                        source="amap_route",
                    )
                ],
            )
        ],
    )


def _request() -> TripRequest:
    return TripRequest(
        city="北京",
        start_date="2030-01-01",
        end_date="2030-01-01",
        travel_days=1,
        travelers=1,
        transportation="公共交通",
        accommodation="舒适型酒店",
    )


def test_edit_restores_all_server_owned_narrative_and_route_facts() -> None:
    existing = _plan()
    edited = existing.model_copy(deep=True)
    edited.overall_suggestions = "伪造总体结论"
    edited.days[0].description = "伪造日程"
    edited.days[0].transportation = "伪造交通"
    edited.days[0].meals[0].name = "伪造餐厅"
    edited.days[0].routes[0].description = "伪造已验证路线说明"

    _restore_verified_plan_facts(edited, existing)

    assert edited.overall_suggestions == "服务端总体建议"
    assert edited.days[0].description == "服务端日程说明"
    assert edited.days[0].transportation == "公共交通"
    assert edited.days[0].meals[0].name == "可信餐厅"
    assert edited.days[0].routes[0].description == "可信路线说明"
    assert edited.days[0].routes[0].verified is True


def test_edit_cannot_add_or_move_an_unverified_attraction() -> None:
    existing = _plan()
    edited = existing.model_copy(deep=True)
    edited.days[0].attractions.append(_attraction("伪造景点", "evil", 0))

    try:
        _restore_verified_plan_facts(edited, existing)
    except UntrustedTripEditError as exc:
        assert "不能新增" in str(exc)
    else:
        raise AssertionError("untrusted attraction was accepted")


def test_stale_if_match_is_rejected_before_update(monkeypatch) -> None:
    existing = _plan()
    updated = False

    class FakeDataService:
        @staticmethod
        def get_trip_plan_snapshot(_plan_no, _user_id):
            return existing.model_copy(deep=True), "stored-json", "current-revision"

        @staticmethod
        def get_trip_request(_plan_no, _user_id):
            return _request()

        @staticmethod
        def update_trip_plan(*_args, **_kwargs):
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
    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/trip/history/P-TEST",
                headers={"If-Match": '"stale-revision"'},
                json=existing.model_dump(mode="json"),
            )
        assert response.status_code == 409
        assert updated is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_job_stage_events_are_bounded_and_late_result_is_ignored() -> None:
    release = threading.Event()
    stages_published = threading.Event()
    service = TripGenerationJobService(
        ttl_seconds=60,
        max_workers=1,
        max_stage_events=2,
        # Long enough for the worker to emit stages; timeout still fires
        # while blocked on release so late results cannot complete.
        max_runtime_seconds=0.25,
    )

    def worker(progress):
        progress(stage="one")
        progress(stage="two")
        progress(stage="three")
        stages_published.set()
        release.wait(timeout=2)
        return {"secret": "late-result"}

    job = service.start("user:test", worker)
    assert stages_published.wait(timeout=1)
    events = [
        event
        for event in service.events(job, heartbeat_seconds=0.01)
        if event is not None
    ]
    assert [event["type"] for event in events] == ["stage", "stage", "error"]
    assert events[-1]["error_type"] == "generation_timeout"

    release.set()
    assert job.condition.acquire(timeout=1)
    try:
        job.condition.wait_for(lambda: job.worker_finished, timeout=2)
    finally:
        job.condition.release()
    assert all("late-result" not in str(event) for event in job.events)
    service.shutdown()


def test_timed_out_plan_job_has_no_late_persistence_or_delivery(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    effects: list[str] = []
    service = TripGenerationJobService(
        ttl_seconds=60,
        max_workers=1,
        max_runtime_seconds=0.03,
    )

    class FakePlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None):
            started.set()
            release.wait(timeout=2)
            return _plan()

    class FakeDataService:
        @staticmethod
        def save_trip_plan(*_args, **_kwargs):
            effects.append("save")
            return "P-LATE"

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
        user_id="user-timeout",
        username="timeout",
        email="timeout@example.com",
        role="user",
    )
    try:
        payload = _request().model_copy(
            update={
                "email_on_completion": True,
                "delivery_email": "timeout@example.com",
            }
        ).model_dump(mode="json")
        with TestClient(app, base_url="https://testserver") as client:
            created = client.post("/api/trip/plan-jobs", json=payload)
            assert created.status_code == 200
            assert started.wait(timeout=1)
            streamed = client.get(created.json()["stream_url"])
            assert streamed.status_code == 200
            assert '"error_type":"generation_timeout"' in streamed.text
            job = service._jobs[created.json()["job_id"]]
            release.set()
            with job.condition:
                assert job.condition.wait_for(
                    lambda: job.worker_finished,
                    timeout=2,
                )
        assert effects == []
    finally:
        release.set()
        app.dependency_overrides.pop(get_optional_current_user, None)
        service.shutdown()


def test_chunked_json_body_cannot_bypass_actual_one_mb_limit() -> None:
    chunks = iter([b'{"city":"', b"x" * (1024 * 1024 + 1), b'"}'])
    with TestClient(app) as client:
        response = client.post(
            "/api/trip/plan-jobs",
            content=chunks,
            headers={
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
            },
        )
    assert response.status_code == 413
    assert response.json()["detail"] == "请求体不能超过1 MB。"


def test_authenticated_api_gets_are_private_and_not_cacheable() -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id="cache-test",
        username="cache-test",
        email=None,
        role="user",
    )
    try:
        with TestClient(app, base_url="https://testserver") as client:
            response = client.get("/api/agent/capabilities")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert "cookie" in response.headers["vary"].lower()
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "max-age=31536000" in response.headers["strict-transport-security"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_unknown_zhipu_error_never_exposes_provider_message() -> None:
    error_type = type("ZhipuSearchError", (RuntimeError,), {})
    secret_message = "unexpected provider payload api_key=secret local=C:/private"
    result = WebTravelGuideAgent.__new__(WebTravelGuideAgent)._safe_provider_error(
        error_type(secret_message)
    )

    assert result == "智谱搜索暂时不可用"
    assert "secret" not in result
