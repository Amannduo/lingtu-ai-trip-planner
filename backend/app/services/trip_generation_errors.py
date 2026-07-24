"""Shared cooperative and business errors for trip generation.

Kept free of FastAPI, HTTP status codes, job queues, and database types so
planning graph and future job services can share one exception surface.
"""

from __future__ import annotations

from typing import Any, Optional


class TripGenerationCancelledError(RuntimeError):
    """Raised when trip generation is cancelled or expires."""

    def __init__(self, reason: str = "generation_cancelled") -> None:
        self.reason = reason
        super().__init__(reason)


class TripPlanQualityRejectedError(RuntimeError):
    """Raised when a generated plan fails hard quality gates (not publishable)."""

    def __init__(self, quality: Any, plan: Optional[Any] = None) -> None:
        self.quality = quality
        self.plan = plan
        codes = [
            str(issue.code)
            for issue in getattr(quality, "issues", []) or []
            if str(getattr(issue, "severity", "")).strip().lower() == "error"
            and getattr(issue, "code", None)
        ]
        message = "trip_plan_quality_rejected"
        if codes:
            message += ":" + ",".join(codes)
        super().__init__(message)
