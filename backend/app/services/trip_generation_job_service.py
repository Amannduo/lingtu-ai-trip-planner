"""Bounded, thread-safe in-memory jobs for progressive trip generation."""

from __future__ import annotations

import math
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .trip_generation_errors import (
    TripGenerationCancelledError,
    TripPlanQualityRejectedError,
)


class TripGenerationCapacityError(RuntimeError):
    """Raised when the bounded generation queue cannot accept more work."""


# Job lifecycle statuses (terminal states are irreversible except cleanup→expired).
JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"
JOB_EXPIRED = "expired"

_TERMINAL_STATUSES = frozenset(
    {JOB_COMPLETED, JOB_FAILED, JOB_CANCELLED, JOB_EXPIRED}
)
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    JOB_PENDING: frozenset({JOB_RUNNING, JOB_CANCELLED, JOB_EXPIRED}),
    JOB_RUNNING: frozenset(
        {JOB_COMPLETED, JOB_FAILED, JOB_CANCELLED, JOB_EXPIRED}
    ),
    JOB_COMPLETED: frozenset({JOB_EXPIRED}),
    JOB_FAILED: frozenset({JOB_EXPIRED}),
    JOB_CANCELLED: frozenset({JOB_EXPIRED}),
    JOB_EXPIRED: frozenset(),
}


class TripGenerationCancellationToken:
    """Thread-safe monotonic deadline shared by a job and its worker."""

    def __init__(self, deadline_monotonic: float) -> None:
        self.deadline_monotonic = float(deadline_monotonic)
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = ""
        self._finalization_claimed = False
        self._completed = False

    def cancel(self, reason: str = "generation_cancelled") -> bool:
        normalized_reason = _bounded_text(reason, 80) or "generation_cancelled"
        with self._lock:
            # Finalization is the irreversible commit point. Cancellation
            # before it wins; cancellation after it is honestly rejected.
            if (
                self._event.is_set()
                or self._finalization_claimed
                or self._completed
            ):
                return False
            self._reason = normalized_reason
            self._event.set()
            return True

    def begin_finalization(self) -> None:
        """Atomically claim the irreversible post-generation phase."""
        with self._lock:
            if self._event.is_set():
                raise TripGenerationCancelledError(
                    self._reason or "generation_cancelled"
                )
            if self._completed:
                raise TripGenerationCancelledError("generation_cancelled")
            if self._finalization_claimed:
                return
            if time.monotonic() >= self.deadline_monotonic:
                self._reason = "generation_timeout"
                self._event.set()
                raise TripGenerationCancelledError(self._reason)
            self._finalization_claimed = True

    def try_complete(self) -> tuple[bool, str]:
        """Linearize completion against cancellation and the deadline."""
        with self._lock:
            if self._event.is_set():
                return False, self._reason or "generation_cancelled"
            if self._completed:
                return False, ""
            if (
                not self._finalization_claimed
                and time.monotonic() >= self.deadline_monotonic
            ):
                self._reason = "generation_timeout"
                self._event.set()
                return False, self._reason
            self._completed = True
            return True, ""

    @property
    def reason(self) -> str:
        self._cancel_if_deadline_elapsed()
        with self._lock:
            return self._reason or "generation_cancelled"

    @property
    def is_cancelled(self) -> bool:
        self._cancel_if_deadline_elapsed()
        return self._event.is_set()

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - time.monotonic())

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise TripGenerationCancelledError(self.reason)

    def _cancel_if_deadline_elapsed(self) -> None:
        with self._lock:
            if (
                self._event.is_set()
                or self._finalization_claimed
                or self._completed
            ):
                return
            if time.monotonic() >= self.deadline_monotonic:
                self._reason = "generation_timeout"
                self._event.set()


# ---------------------------------------------------------------------------
# Process-wide generation capacity (shared by sync + progressive workers)
# ---------------------------------------------------------------------------

_GENERATION_SLOT_COUNT = 4
_generation_slots_lock = threading.Lock()
_generation_slots = threading.BoundedSemaphore(_GENERATION_SLOT_COUNT)
_generation_slots_held = 0


def generation_capacity_snapshot() -> dict[str, int]:
    """Diagnostic view of process-wide generation slots."""
    with _generation_slots_lock:
        held = _generation_slots_held
        initial = _GENERATION_SLOT_COUNT
    return {
        "initial": initial,
        "held": held,
        "available": max(0, initial - held),
    }


def reset_generation_capacity_for_tests() -> None:
    """Recreate the process semaphore after verifying workers released slots.

    Production paths must release via ``run_with_generation_capacity``'s
    ``finally``. This helper only restores a clean slate for isolated tests
    when leftover threads from other suites are impossible to join.
    """
    global _generation_slots, _generation_slots_held
    with _generation_slots_lock:
        _generation_slots = threading.BoundedSemaphore(_GENERATION_SLOT_COUNT)
        _generation_slots_held = 0


def run_with_generation_capacity(worker: Callable[[], Any]) -> Any:
    """Bound expensive model generation across sync and progressive endpoints.

    Acquire is non-blocking so callers get an immediate capacity error instead
    of hanging. Release always runs in ``finally`` once acquire succeeds,
    covering success, quality rejection, cancellation, timeout, and crashes.
    """
    global _generation_slots_held
    acquired = False
    if not _generation_slots.acquire(blocking=False):
        raise TripGenerationCapacityError("all generation slots are busy")
    acquired = True
    with _generation_slots_lock:
        _generation_slots_held += 1
    try:
        return worker()
    finally:
        if acquired:
            with _generation_slots_lock:
                _generation_slots_held = max(0, _generation_slots_held - 1)
            _generation_slots.release()


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _sanitize_stage_payload(
    payload: dict[str, Any],
    *,
    last_progress: int | None,
) -> dict[str, Any]:
    progress = payload.get("progress")
    safe_progress = None
    if (
        isinstance(progress, (int, float))
        and not isinstance(progress, bool)
        and math.isfinite(progress)
    ):
        safe_progress = max(0, min(99, int(progress)))
        if last_progress is not None:
            safe_progress = max(last_progress, safe_progress)
    raw_meta = payload.get("meta")
    safe_meta: dict[str, str | int | float | bool] = {}
    if isinstance(raw_meta, dict):
        for key, value in list(raw_meta.items())[:20]:
            if not isinstance(key, str) or isinstance(
                value, (dict, list, tuple, set)
            ):
                continue
            if isinstance(value, str):
                safe_meta[key[:80]] = value[:500]
            elif isinstance(value, bool) or isinstance(value, int):
                safe_meta[key[:80]] = value
            elif isinstance(value, float) and math.isfinite(value):
                safe_meta[key[:80]] = value
    result: dict[str, Any] = {
        "stage": _bounded_text(payload.get("stage"), 80),
        "message": _bounded_text(payload.get("message"), 500),
        "detail": _bounded_text(payload.get("detail"), 2_000),
        "meta": safe_meta,
    }
    if safe_progress is not None:
        result["progress"] = safe_progress
    return result


def quality_rejection_event_payload(
    exc: TripPlanQualityRejectedError,
) -> dict[str, Any]:
    """Map quality rejection to frontend-compatible message + issues."""
    issues: list[dict[str, Any]] = []
    quality = getattr(exc, "quality", None)
    for issue in getattr(quality, "issues", None) or []:
        code = getattr(issue, "code", None)
        severity = getattr(issue, "severity", None)
        message = getattr(issue, "message", None)
        if hasattr(code, "value"):
            code = code.value
        if hasattr(severity, "value"):
            severity = severity.value
        item: dict[str, Any] = {
            "code": str(code or "TRIP_PLAN_QUALITY_REJECTED"),
            "severity": str(severity or "error"),
            "message": str(message or "生成的行程未通过质量检查"),
        }
        suggestion = getattr(issue, "suggestion", None)
        if suggestion:
            if hasattr(suggestion, "value"):
                suggestion = suggestion.value
            item["suggestion"] = str(suggestion)
        issues.append(item)

    if not issues:
        issues.append(
            {
                "code": "TRIP_PLAN_QUALITY_REJECTED",
                "severity": "error",
                "message": "生成的行程未通过质量检查",
            }
        )

    return {
        "message": "生成的行程未通过质量检查",
        "issues": issues,
        "error_type": "quality_rejected",
    }


@dataclass
class TripGenerationJob:
    job_id: str
    access_token: str
    owner_key: str
    cancellation_token: TripGenerationCancellationToken
    created_at: float = field(default_factory=time.time)
    created_monotonic: float = field(default_factory=time.monotonic)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = JOB_PENDING
    terminal: bool = False
    completed_at: float | None = None
    completed_monotonic: float | None = None
    finalizing: bool = False
    worker_finished: bool = False
    last_progress: int | None = None
    max_stage_events: int = 128
    max_runtime_seconds: float = 600.0
    condition: threading.Condition = field(default_factory=threading.Condition)

    def _transition_locked(self, new_status: str) -> bool:
        allowed = _ALLOWED_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            return False
        self.status = new_status
        if new_status in _TERMINAL_STATUSES:
            self.terminal = True
            self.completed_at = time.time()
            self.completed_monotonic = time.monotonic()
        return True

    def mark_running(self) -> None:
        with self.condition:
            self._transition_locked(JOB_RUNNING)
            self.condition.notify_all()

    def publish(self, event_type: str, **payload: Any) -> dict[str, Any] | None:
        with self.condition:
            if self.terminal:
                return None
            if event_type == "stage":
                stage_count = sum(
                    1 for existing in self.events if existing.get("type") == "stage"
                )
                if stage_count >= self.max_stage_events:
                    return None
                payload = _sanitize_stage_payload(
                    payload, last_progress=self.last_progress
                )
                if "progress" in payload:
                    self.last_progress = int(payload["progress"])
            event = {
                "id": len(self.events) + 1,
                "type": event_type,
                "job_id": self.job_id,
                "status": self.status,
                **payload,
            }
            self.events.append(event)
            if event_type == "result":
                self._transition_locked(JOB_COMPLETED)
                event["status"] = self.status
                if "progress" not in event:
                    event["progress"] = 100
            elif event_type == "error":
                error_type = str(payload.get("error_type") or "")
                if error_type in {
                    "generation_cancelled",
                    "client_disconnected",
                }:
                    self._transition_locked(JOB_CANCELLED)
                elif error_type == "generation_timeout":
                    # Timeout is a failed terminal outcome, not user cancel.
                    self._transition_locked(JOB_FAILED)
                else:
                    self._transition_locked(JOB_FAILED)
                event["status"] = self.status
            self.condition.notify_all()
            return event

    def raise_if_cancelled(self) -> None:
        self.cancellation_token.raise_if_cancelled()

    def begin_finalization(self) -> None:
        """Atomically lease save work before the deadline."""
        with self.condition:
            if self.terminal:
                raise TripGenerationCancelledError(
                    self.cancellation_token.reason
                )
            self.cancellation_token.begin_finalization()
            self.finalizing = True
            self.condition.notify_all()

    def complete_if_active(self, result: Any) -> bool:
        """Publish exactly one result, atomically with the deadline check."""
        with self.condition:
            if self.terminal:
                return False
            can_complete, reason = self.cancellation_token.try_complete()
            if not can_complete:
                if not reason:
                    return False
                timed_out = reason == "generation_timeout"
                self.publish(
                    "error",
                    message=(
                        "旅行方案生成超时，请稍后重试。"
                        if timed_out
                        else "旅行方案生成已取消。"
                    ),
                    error_type=(
                        "generation_timeout"
                        if timed_out
                        else "generation_cancelled"
                    ),
                )
                return False
            return (
                self.publish(
                    "result",
                    progress=100,
                    stage="completed",
                    message="旅行计划生成成功",
                    data=result,
                )
                is not None
            )

    def expire_if_needed(self) -> bool:
        with self.condition:
            if self.terminal:
                return False
            if not self.cancellation_token.is_cancelled:
                return False
            reason = self.cancellation_token.reason
            timed_out = reason == "generation_timeout"
            return (
                self.publish(
                    "error",
                    message=(
                        "旅行方案生成超时，请稍后重试。"
                        if timed_out
                        else "旅行方案生成已取消。"
                    ),
                    error_type=(
                        "generation_timeout"
                        if timed_out
                        else "generation_cancelled"
                    ),
                )
                is not None
            )

    def request_cancel(self, reason: str = "generation_cancelled") -> bool:
        cancelled = self.cancellation_token.cancel(reason)
        if cancelled:
            self.expire_if_needed()
        return cancelled

    def mark_worker_finished(self) -> None:
        with self.condition:
            self.worker_finished = True
            self.condition.notify_all()

    def mark_expired_for_cleanup(self) -> None:
        with self.condition:
            if self.status in {
                JOB_COMPLETED,
                JOB_FAILED,
                JOB_CANCELLED,
            }:
                self._transition_locked(JOB_EXPIRED)


class TripGenerationProgress:
    """Progress callback that also exposes cooperative cancellation checks."""

    def __init__(self, job: TripGenerationJob) -> None:
        self._job = job

    @property
    def cancellation_token(self) -> TripGenerationCancellationToken:
        return self._job.cancellation_token

    def raise_if_cancelled(self) -> None:
        self._job.raise_if_cancelled()

    def begin_finalization(self) -> None:
        self._job.begin_finalization()

    def __call__(self, **payload: Any) -> None:
        self.raise_if_cancelled()
        self._job.publish("stage", **payload)


class TripGenerationJobService:
    def __init__(
        self,
        ttl_seconds: int = 900,
        max_jobs: int = 100,
        max_workers: int = 3,
        max_pending_jobs: int = 20,
        max_jobs_per_owner: int = 2,
        max_runtime_seconds: float = 600.0,
        max_stage_events: int = 128,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_jobs = max_jobs
        self.max_pending_jobs = max_pending_jobs
        self.max_jobs_per_owner = max_jobs_per_owner
        self.max_runtime_seconds = max_runtime_seconds
        self.max_stage_events = max_stage_events
        self._jobs: dict[str, TripGenerationJob] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="trip-generation",
        )

    def start(
        self,
        owner_key: str,
        worker: Callable[[TripGenerationProgress], Any],
    ) -> TripGenerationJob:
        with self._lock:
            self._cleanup_locked()
            outstanding_jobs = [
                job for job in self._jobs.values() if not job.worker_finished
            ]
            owner_jobs = [
                job for job in outstanding_jobs if job.owner_key == owner_key
            ]
            if len(outstanding_jobs) >= self.max_pending_jobs:
                raise TripGenerationCapacityError("generation queue is full")
            if len(owner_jobs) >= self.max_jobs_per_owner:
                raise TripGenerationCapacityError(
                    "owner already has active jobs"
                )

            job = TripGenerationJob(
                job_id=uuid.uuid4().hex,
                access_token=secrets.token_urlsafe(24),
                owner_key=owner_key,
                cancellation_token=TripGenerationCancellationToken(
                    time.monotonic()
                    + max(0.001, float(self.max_runtime_seconds))
                ),
                max_runtime_seconds=self.max_runtime_seconds,
                max_stage_events=self.max_stage_events,
            )
            self._jobs[job.job_id] = job

        progress = TripGenerationProgress(job)

        def run() -> None:
            job.mark_running()
            try:
                result = worker(progress)
                job.complete_if_active(result)
            except TripPlanQualityRejectedError as exc:
                payload = quality_rejection_event_payload(exc)
                job.publish("error", **payload)
            except TripGenerationCancelledError as exc:
                job.cancellation_token.cancel(exc.reason)
                timed_out = exc.reason == "generation_timeout"
                job.publish(
                    "error",
                    message=(
                        "旅行方案生成超时，请稍后重试。"
                        if timed_out
                        else "旅行方案生成已取消。"
                    ),
                    error_type=(
                        "generation_timeout"
                        if timed_out
                        else "generation_cancelled"
                    ),
                )
            except Exception as exc:
                if not job.finalizing and job.cancellation_token.is_cancelled:
                    timed_out = (
                        job.cancellation_token.reason == "generation_timeout"
                    )
                    job.publish(
                        "error",
                        message=(
                            "旅行方案生成超时，请稍后重试。"
                            if timed_out
                            else "旅行方案生成已取消。"
                        ),
                        error_type=(
                            "generation_timeout"
                            if timed_out
                            else "generation_cancelled"
                        ),
                    )
                    return
                # Never expose provider errors, prompts, paths or secrets.
                print(f"[trip-job] generation failed: {type(exc).__name__}")
                job.publish(
                    "error",
                    message="旅行方案生成失败，请稍后重试。",
                    error_type="generation_failed",
                )
            finally:
                job.mark_worker_finished()

        try:
            self._executor.submit(run)
        except Exception:
            with self._lock:
                self._jobs.pop(job.job_id, None)
            raise
        return job

    def cancel(
        self,
        job: TripGenerationJob,
        reason: str = "generation_cancelled",
    ) -> bool:
        return job.request_cancel(reason)

    def get(
        self,
        job_id: str,
        owner_key: str,
        access_token: str,
    ) -> TripGenerationJob | None:
        with self._lock:
            self._cleanup_locked()
            job = self._jobs.get(job_id)
        if (
            job is None
            or job.owner_key != owner_key
            or not secrets.compare_digest(job.access_token, access_token or "")
        ):
            return None
        return job

    def events(
        self,
        job: TripGenerationJob,
        after_id: int = 0,
        heartbeat_seconds: float = 15.0,
    ) -> Iterator[dict[str, Any] | None]:
        cursor = max(0, after_id)
        while True:
            job.expire_if_needed()
            with job.condition:
                available = [
                    event for event in job.events if event["id"] > cursor
                ]
                if not available and not job.terminal:
                    remaining = job.cancellation_token.remaining_seconds()
                    wait_seconds = (
                        heartbeat_seconds
                        if job.finalizing
                        else min(heartbeat_seconds, max(0.001, remaining))
                    )
                    job.condition.wait(timeout=wait_seconds)
                    job.expire_if_needed()
                    available = [
                        event for event in job.events if event["id"] > cursor
                    ]
                terminal = job.terminal
            if available:
                for event in available:
                    cursor = event["id"]
                    yield event
                continue
            if terminal:
                return
            # Heartbeat: None does not mutate job status.
            yield None

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _cleanup_locked(self) -> None:
        now = time.monotonic()
        for job in self._jobs.values():
            job.expire_if_needed()
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if (
                job.terminal
                and job.worker_finished
                and (job.completed_monotonic or job.created_monotonic)
                < now - self.ttl_seconds
            )
        ]
        for job_id in expired:
            job = self._jobs.get(job_id)
            if job is not None:
                job.mark_expired_for_cleanup()
            self._jobs.pop(job_id, None)

        overflow = len(self._jobs) - self.max_jobs
        if overflow > 0:
            removable = sorted(
                (
                    job
                    for job in self._jobs.values()
                    if job.terminal and job.worker_finished
                ),
                key=lambda item: item.completed_monotonic
                or item.created_monotonic,
            )
            for job in removable[:overflow]:
                job.mark_expired_for_cleanup()
                self._jobs.pop(job.job_id, None)


_service: TripGenerationJobService | None = None
_service_lock = threading.Lock()


def get_trip_generation_job_service() -> TripGenerationJobService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                # Resolve settings lazily so tests can still instantiate the
                # service directly with deterministic short deadlines.
                try:
                    from ..config import get_settings

                    runtime = float(
                        getattr(
                            get_settings(),
                            "trip_generation_max_runtime_seconds",
                            600.0,
                        )
                    )
                except Exception:
                    runtime = 600.0
                _service = TripGenerationJobService(
                    max_runtime_seconds=runtime
                )
    return _service


def shutdown_trip_generation_job_service() -> None:
    """Release executor resources and allow a clean service recreation."""
    global _service
    with _service_lock:
        service = _service
        _service = None
    if service is not None:
        service.shutdown(wait=False)
