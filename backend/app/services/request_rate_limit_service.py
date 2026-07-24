"""Process-local sliding-window rate limits for public API abuse control.

This is intentionally process-local memory state. Multi-worker / multi-instance
deployments do not share counters; a future Redis-backed implementation can
replace the service behind the same check() surface without changing callers.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import deque
from typing import Callable


Clock = Callable[[], float]


class RequestRateLimitService:
    """Thread-safe sliding window limiter (process-local)."""

    def __init__(
        self,
        max_keys: int = 10_000,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.max_keys = max(1, int(max_keys))
        self._clock: Clock = clock or time.monotonic
        self._events: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def check(
        self,
        scope: str,
        identity: str,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> int:
        """Return 0 when allowed (and consume one token), else Retry-After seconds.

        Rejected checks do not append an event, so a 429 does not further inflate
        the bucket. ``now`` is injectable for tests (monotonic domain).
        """
        if limit <= 0:
            return 1
        window = max(1, int(window_seconds))
        current = self._clock() if now is None else float(now)
        identity_hash = hashlib.sha256(
            f"{scope}\0{identity}".encode("utf-8")
        ).hexdigest()[:24]
        key = (str(scope), identity_hash)
        cutoff = current - window
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, math.ceil(events[0] + window - current))
                self._prune_locked(cutoff)
                return retry_after
            events.append(current)
            self._prune_locked(cutoff)
        return 0

    def current_count(
        self,
        scope: str,
        identity: str,
        *,
        window_seconds: int,
        now: float | None = None,
    ) -> int:
        """Return how many events remain in the active window (read-only)."""
        window = max(1, int(window_seconds))
        current = self._clock() if now is None else float(now)
        identity_hash = hashlib.sha256(
            f"{scope}\0{identity}".encode("utf-8")
        ).hexdigest()[:24]
        key = (str(scope), identity_hash)
        cutoff = current - window
        with self._lock:
            events = self._events.get(key)
            if not events:
                return 0
            while events and events[0] <= cutoff:
                events.popleft()
            return len(events)

    def clear(self) -> None:
        """Drop all buckets (app shutdown / test isolation)."""
        with self._lock:
            self._events.clear()

    def key_count(self) -> int:
        with self._lock:
            return len(self._events)

    def _prune_locked(self, cutoff: float) -> None:
        if len(self._events) <= self.max_keys:
            return
        stale = [
            key
            for key, events in self._events.items()
            if not events or events[-1] <= cutoff
        ]
        for key in stale:
            self._events.pop(key, None)
            if len(self._events) <= self.max_keys:
                return
        overflow = len(self._events) - self.max_keys
        if overflow <= 0:
            return
        oldest = sorted(
            self._events,
            key=lambda item: self._events[item][-1] if self._events[item] else 0.0,
        )
        for key in oldest[:overflow]:
            self._events.pop(key, None)


def create_request_rate_limit_service(
    max_keys: int = 10_000,
    *,
    clock: Clock | None = None,
) -> RequestRateLimitService:
    """Factory for per-app process-local limiters."""
    return RequestRateLimitService(max_keys=max_keys, clock=clock)


# Fallback used only when no app.state binding is available (scripts / early import).
_fallback_service: RequestRateLimitService | None = None
_fallback_lock = threading.Lock()


def get_request_rate_limit_service(request=None) -> RequestRateLimitService:
    """Resolve the process-local limiter for this app, or a shared fallback.

    Prefer ``request.app.state.request_rate_limiter`` so each FastAPI app instance
    keeps isolated counters (critical for TestClient / multi-app tests).
    """
    if request is not None:
        app = getattr(request, "app", None)
        state = getattr(app, "state", None) if app is not None else None
        service = getattr(state, "request_rate_limiter", None) if state is not None else None
        if isinstance(service, RequestRateLimitService):
            return service

    global _fallback_service
    if _fallback_service is None:
        with _fallback_lock:
            if _fallback_service is None:
                _fallback_service = create_request_rate_limit_service()
    return _fallback_service


def reset_request_rate_limit_service_for_tests() -> RequestRateLimitService:
    """Replace the module fallback and return it (tests only)."""
    global _fallback_service
    with _fallback_lock:
        _fallback_service = create_request_rate_limit_service()
        return _fallback_service
