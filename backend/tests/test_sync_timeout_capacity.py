"""Regression tests for sync-timeout cancellation and capacity hygiene (P0-4).

Cancellation of the sync ``/plan`` worker is cooperative: threads cannot be
killed, so after a 504 the generation slot is legitimately held until the
planner observes the cancelled token.  These tests pin the two guarantees
that were previously untested or broken:

1. after a sync timeout, the generation slot is released within a bounded
   time once the worker hits its next cancellation checkpoint — the 504 can
   never leak a slot permanently;
2. capacity exhaustion on the sync path returns the same retryable 429
   contract as ``/plan-jobs`` (previously a 500 leaking internal detail).
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.api.auth import get_optional_current_user
from app.api.main import app
from app.config import get_settings
from app.models.schemas import TripRequest
from app.services.auth_service import AuthenticatedUser
from app.services.trip_generation_job_service import (
    TripGenerationCapacityError,
    generation_capacity_snapshot,
)


def _request_payload() -> dict:
    return TripRequest(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-02",
        travel_days=1,
        travelers=1,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
    ).model_dump(mode="json")


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_sync_timeout_releases_capacity_after_worker_observes_cancel(
    monkeypatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class BlockingPlanner:
        @staticmethod
        def plan_trip(_request, progress_callback=None, **_kwargs):
            started.set()
            try:
                release.wait(timeout=5)
                # Next cooperative checkpoint after the client-visible 504.
                if progress_callback is not None:
                    progress_callback.raise_if_cancelled()
                raise AssertionError("cancelled token must stop the planner")
            finally:
                finished.set()

    monkeypatch.setattr(
        get_settings(), "trip_generation_max_runtime_seconds", 0.03
    )
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent",
        lambda: BlockingPlanner(),
    )
    app.dependency_overrides[get_optional_current_user] = lambda: None

    baseline_held = generation_capacity_snapshot()["held"]
    try:
        with TestClient(app) as client:
            response = client.post("/api/trip/plan", json=_request_payload())

            assert started.is_set()
            assert response.status_code == 504
            # The worker is still blocked: the slot is honestly held.
            snapshot = generation_capacity_snapshot()
            assert snapshot["held"] == baseline_held + 1

            # Unblock the worker; it must observe the cancelled token and
            # give the slot back within a bounded time.
            release.set()
            assert finished.wait(timeout=3)
            assert _wait_until(
                lambda: generation_capacity_snapshot()["held"] == baseline_held
            ), "generation slot was not released after cooperative cancel"
    finally:
        release.set()
        app.dependency_overrides.pop(get_optional_current_user, None)


def test_sync_capacity_exhaustion_returns_429_like_job_path(monkeypatch) -> None:
    def refuse_capacity(_worker):
        raise TripGenerationCapacityError("all generation slots are busy")

    monkeypatch.setattr(
        "app.api.routes.trip.run_with_generation_capacity",
        refuse_capacity,
    )
    app.dependency_overrides[get_optional_current_user] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.post("/api/trip/plan", json=_request_payload())

        assert response.status_code == 429
        detail = response.json()["detail"]
        assert "请等待" in detail
        # Internal capacity wording must not leak to the client.
        assert "slots" not in detail
    finally:
        app.dependency_overrides.pop(get_optional_current_user, None)
