"""Generation capacity release guarantees for sync + progressive jobs."""

from __future__ import annotations

import threading
import time

import pytest

from app.services.trip_generation_errors import (
    TripGenerationCancelledError,
    TripPlanQualityRejectedError,
)
from app.services.trip_generation_job_service import (
    TripGenerationCapacityError,
    TripGenerationJobService,
    generation_capacity_snapshot,
    reset_generation_capacity_for_tests,
    run_with_generation_capacity,
)
from app.models.schemas import TripPlanQualityIssue, TripPlanQualityResult


@pytest.fixture(autouse=True)
def _isolate_generation_capacity():
    """Isolate process-wide slots without hiding production release bugs.

    Each test starts from a full semaphore. After the test, held slots must
    return to zero once workers finish; otherwise the test fails explicitly.
    """
    reset_generation_capacity_for_tests()
    yield
    # Give short-lived worker threads a moment to hit finally.
    deadline = time.time() + 2.0
    while generation_capacity_snapshot()["held"] > 0 and time.time() < deadline:
        time.sleep(0.02)
    assert generation_capacity_snapshot()["held"] == 0, generation_capacity_snapshot()
    reset_generation_capacity_for_tests()


def test_capacity_released_after_successful_work() -> None:
    assert generation_capacity_snapshot()["held"] == 0

    def work():
        assert generation_capacity_snapshot()["held"] == 1
        return "ok"

    assert run_with_generation_capacity(work) == "ok"
    assert generation_capacity_snapshot()["held"] == 0

    # Second acquisition must succeed after release.
    assert run_with_generation_capacity(lambda: "again") == "again"
    assert generation_capacity_snapshot()["held"] == 0


def test_capacity_released_after_quality_rejection() -> None:
    quality = TripPlanQualityResult(
        status="failed",
        score=0,
        publishable=False,
        issues=[
            TripPlanQualityIssue(
                code="DAY_COUNT_MISMATCH",
                severity="error",
                message="生成计划天数与请求天数不一致",
                suggestion="请重新生成或确认出行天数",
            )
        ],
    )

    def work():
        raise TripPlanQualityRejectedError(quality=quality, plan=None)

    with pytest.raises(TripPlanQualityRejectedError):
        run_with_generation_capacity(work)
    assert generation_capacity_snapshot()["held"] == 0
    assert run_with_generation_capacity(lambda: "next") == "next"


def test_capacity_released_after_cancel_error() -> None:
    def work():
        raise TripGenerationCancelledError("generation_cancelled")

    with pytest.raises(TripGenerationCancelledError):
        run_with_generation_capacity(work)
    assert generation_capacity_snapshot()["held"] == 0


def test_capacity_released_after_timeout_style_cancel() -> None:
    def work():
        raise TripGenerationCancelledError("generation_timeout")

    with pytest.raises(TripGenerationCancelledError):
        run_with_generation_capacity(work)
    assert generation_capacity_snapshot()["held"] == 0
    assert run_with_generation_capacity(lambda: 1) == 1


def test_capacity_released_after_unexpected_exception() -> None:
    def work():
        raise RuntimeError("unexpected secret token")

    with pytest.raises(RuntimeError):
        run_with_generation_capacity(work)
    assert generation_capacity_snapshot()["held"] == 0


def test_concurrent_capacity_bound_and_release() -> None:
    initial = generation_capacity_snapshot()["initial"]
    gate = threading.Event()
    entered = threading.Event()
    holders: list[threading.Thread] = []

    def hold():
        def work():
            entered.set()
            gate.wait(timeout=2)
            return True

        run_with_generation_capacity(work)

    for _ in range(initial):
        thread = threading.Thread(target=hold)
        holders.append(thread)
        thread.start()

    assert entered.wait(timeout=1)
    # Wait until all slots are held.
    deadline = time.time() + 1
    while generation_capacity_snapshot()["held"] < initial and time.time() < deadline:
        time.sleep(0.01)
    assert generation_capacity_snapshot()["held"] == initial

    with pytest.raises(TripGenerationCapacityError):
        run_with_generation_capacity(lambda: "overflow")

    gate.set()
    for thread in holders:
        thread.join(timeout=2)
    assert generation_capacity_snapshot()["held"] == 0
    assert run_with_generation_capacity(lambda: "after") == "after"


def test_job_success_releases_capacity_for_next_job() -> None:
    service = TripGenerationJobService(ttl_seconds=30, max_workers=2)
    try:
        first = service.start(
            "user:a",
            lambda progress: run_with_generation_capacity(lambda: {"ok": 1}),
        )
        events = [e for e in service.events(first, heartbeat_seconds=0.01) if e]
        assert events[-1]["type"] == "result"
        assert generation_capacity_snapshot()["held"] == 0

        second = service.start(
            "user:b",
            lambda progress: run_with_generation_capacity(lambda: {"ok": 2}),
        )
        events2 = [e for e in service.events(second, heartbeat_seconds=0.01) if e]
        assert events2[-1]["type"] == "result"
    finally:
        service.shutdown()


def test_job_quality_rejection_releases_capacity() -> None:
    service = TripGenerationJobService(ttl_seconds=30, max_workers=2)
    quality = TripPlanQualityResult(
        status="failed",
        publishable=False,
        score=0,
        issues=[
            TripPlanQualityIssue(
                code="DAY_COUNT_MISMATCH",
                severity="error",
                message="生成计划天数与请求天数不一致",
            )
        ],
    )

    def worker(_progress):
        def work():
            raise TripPlanQualityRejectedError(quality=quality)

        return run_with_generation_capacity(work)

    try:
        job = service.start("user:q", worker)
        events = [e for e in service.events(job, heartbeat_seconds=0.01) if e]
        assert events[-1]["type"] == "error"
        assert events[-1]["error_type"] == "quality_rejected"
        assert events[-1]["issues"][0]["code"] == "DAY_COUNT_MISMATCH"
        assert job.status == "failed"
        assert generation_capacity_snapshot()["held"] == 0

        follow = service.start(
            "user:q2",
            lambda p: run_with_generation_capacity(lambda: {"ok": True}),
        )
        follow_events = [
            e for e in service.events(follow, heartbeat_seconds=0.01) if e
        ]
        assert follow_events[-1]["type"] == "result"
    finally:
        service.shutdown()


def test_job_cancel_releases_capacity() -> None:
    service = TripGenerationJobService(ttl_seconds=30, max_workers=1)
    started = threading.Event()
    release = threading.Event()

    def worker(_progress):
        def work():
            started.set()
            if not release.wait(timeout=2):
                raise TripGenerationCancelledError("generation_cancelled")
            raise TripGenerationCancelledError("generation_cancelled")

        return run_with_generation_capacity(work)

    try:
        job = service.start("user:c", worker)
        assert started.wait(timeout=1)
        assert generation_capacity_snapshot()["held"] == 1
        assert service.cancel(job, reason="generation_cancelled")
        # Worker must leave the capacity section; cancel alone does not
        # preempt a blocked thread until it observes the token.
        release.set()
        with job.condition:
            assert job.condition.wait_for(
                lambda: job.worker_finished, timeout=2
            )
        events = [e for e in service.events(job, heartbeat_seconds=0.01) if e]
        assert events[-1]["type"] == "error"
        assert generation_capacity_snapshot()["held"] == 0
    finally:
        release.set()
        service.shutdown()


def test_job_timeout_releases_capacity() -> None:
    service = TripGenerationJobService(
        ttl_seconds=30,
        max_workers=1,
        max_runtime_seconds=0.05,
    )
    started = threading.Event()
    release = threading.Event()

    def worker(progress):
        def work():
            started.set()
            release.wait(timeout=2)
            progress.raise_if_cancelled()
            return {"late": True}

        return run_with_generation_capacity(work)

    try:
        job = service.start("user:t", worker)
        assert started.wait(timeout=1)
        events = [e for e in service.events(job, heartbeat_seconds=0.01) if e]
        assert events[-1]["type"] == "error"
        assert events[-1]["error_type"] == "generation_timeout"
        release.set()
        with job.condition:
            job.condition.wait_for(lambda: job.worker_finished, timeout=2)
        assert generation_capacity_snapshot()["held"] == 0
    finally:
        release.set()
        service.shutdown()
