"""旅行规划API路由"""

from __future__ import annotations

import asyncio
import time
from datetime import date
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Path,
    Query,
    Request as HttpRequest,
    Response,
)
from fastapi.encoders import jsonable_encoder
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
import json
from ...config import get_settings
from ...models.schemas import (
    Attraction,
    TripRequest,
    TripPlan,
    TripPlanQualityIssue,
    TripPlanResponse,
    ErrorResponse
)
from ...agents.trip_planner_agent import (
    get_trip_planner_agent,
    planner_is_initialized,
)
from ...services.trip_generation_errors import (
    TripGenerationCancelledError,
    TripPlanQualityRejectedError,
)
from ...services.auth_service import AuthenticatedUser
from ...services.destination_feasibility_service import get_destination_feasibility_service
from ...services.contract_token_service import verify_contract_token
from ...services.semantic_contract_service import (
    build_generation_contract,
    collect_hard_block_issues_for_contract,
    user_acknowledged_contract_risks,
)
from ...services.trip_email_service import deliver_trip_plan_email
from ...services.travel_plan_data_service import get_travel_plan_data_service
from ...services.trip_plan_quality_service import (
    get_trip_plan_quality_service,
    resolve_plan_quality_status,
)
from ...services.web_push_service import notify_trip_plan_ready
from ...services.trip_generation_job_service import (
    TripGenerationCancellationToken,
    TripGenerationCapacityError,
    generation_capacity_snapshot,
    get_trip_generation_job_service,
    run_with_generation_capacity,
)
from ...services.request_rate_limit_service import get_request_rate_limit_service
from ..auth import get_current_user, get_optional_current_user


class UntrustedTripEditError(ValueError):
    """Client attempted to forge server-owned plan facts or add unverified POIs."""


def _validate_generation_request(
    request: TripRequest,
    current_user: AuthenticatedUser | None = None,
) -> TripRequest:
    """Reject unusable generation inputs before agent work starts.

    Covers civil past dates, unresolved semantic hard-blocks, and auto-
    recommended destinations that violate short-trip feasibility.

    Returns the request with the server-built semantic contract attached —
    the single construction point for the whole request lifecycle; every
    downstream reader (planner, quality gate) reuses this contract.
    """
    try:
        start = date.fromisoformat(str(request.start_date))
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="出行开始日期格式无效。",
        ) from exc
    if start < date.today():
        raise HTTPException(
            status_code=422,
            detail="出行开始日期不能早于今天。",
        )

    # A verified recommendation token contributes the session contract as
    # merge base; any verification defect silently means "no token".
    session_contract = None
    raw_token = str(getattr(request, "recommendation_token", "") or "")
    if raw_token:
        session_contract = verify_contract_token(
            raw_token,
            subject=(
                f"user:{current_user.user_id}"
                if current_user is not None
                else "anon"
            ),
        )

    request, message_contract = build_generation_contract(
        request, session_contract=session_contract
    )

    settings = get_settings()
    if bool(getattr(settings, "semantic_contract_hard_block_enabled", True)):
        if not user_acknowledged_contract_risks(request):
            issues = collect_hard_block_issues_for_contract(
                request, message_contract
            )
            if issues:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "存在未确认的关键约束冲突，请先确认后再生成。",
                        "issues": issues,
                    },
                )

    if str(getattr(request, "destination_source", "") or "") == "recommendation":
        assessment = get_destination_feasibility_service().assess(
            request.origin_city,
            request.city,
            request.travel_days,
            explicit_destination=False,
        )
        if not assessment.allowed:
            raise HTTPException(
                status_code=422,
                detail=assessment.reason or "推荐目的地不在当前天数的可信短途圈内。",
            )
    return request


def _attraction_key(attraction: Attraction) -> str:
    return str(attraction.poi_id or "").strip() or str(attraction.name or "").strip()


def _merge_trusted_attraction(edited: Attraction, trusted: Attraction) -> Attraction:
    """Keep user-tunable presentation fields; restore identity and map facts."""
    merged = trusted.model_copy(deep=True)
    # User-editable presentation only.
    merged.description = edited.description
    merged.visit_duration = edited.visit_duration
    return merged


def _restore_verified_plan_facts(edited: TripPlan, existing: TripPlan) -> TripPlan:
    """Overwrite client-controlled fields with server-owned verified facts.

    Clients may tweak presentation fields such as attraction description or
    visit duration, but must not forge weather, budget, generation mode,
    narrative facts, meals, verified routes, hotels, POI identity, or
    introduce attractions that were never present on the stored plan.
    """
    edited.generation_mode = existing.generation_mode
    edited.overall_suggestions = existing.overall_suggestions
    edited.weather_info = existing.weather_info
    edited.budget = existing.budget
    edited.agent_audit = existing.agent_audit
    edited.web_references = existing.web_references
    edited.web_guide = existing.web_guide
    edited.map_context = existing.map_context

    existing_by_key: dict[str, Attraction] = {}
    for day in existing.days or []:
        for attraction in day.attractions or []:
            key = _attraction_key(attraction)
            if key:
                existing_by_key[key] = attraction

    for day in edited.days or []:
        for attraction in day.attractions or []:
            key = _attraction_key(attraction)
            if key not in existing_by_key:
                raise UntrustedTripEditError(
                    f"不能新增未经验证的景点：{attraction.name}"
                )

    existing_days = list(existing.days or [])
    for index, day in enumerate(edited.days or []):
        if index >= len(existing_days):
            break
        source = existing_days[index]
        day.description = source.description
        day.transportation = source.transportation
        day.accommodation = source.accommodation
        day.hotel = source.hotel
        day.meals = source.meals
        day.routes = source.routes
        day.date = source.date
        day.day_index = source.day_index
        restored: list[Attraction] = []
        for attraction in day.attractions or []:
            trusted = existing_by_key[_attraction_key(attraction)]
            restored.append(_merge_trusted_attraction(attraction, trusted))
        day.attractions = restored

    # Do not silently rebuild missing/extra days: day-count corruption must
    # surface through the post-edit quality gate and block the save.
    return edited


def _reject_identity_mutation(edited: TripPlan, existing: TripPlan) -> None:
    """City and travel window are immutable after generation."""
    for field in ("city", "start_date", "end_date"):
        if str(getattr(edited, field, "") or "") != str(getattr(existing, field, "") or ""):
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "目的地和出行日期不能直接修改，请重新生成行程。",
                    "issues": [
                        {
                            "code": "TRIP_IDENTITY_IMMUTABLE",
                            "severity": "error",
                            "path": field,
                            "message": "目的地和出行日期不能直接修改，请重新生成行程。",
                        }
                    ],
                },
            )


def _mark_legacy_weak_validation(quality) -> None:
    """Product rule for rows without a generation-time request snapshot.

    Editing and saving stay allowed, but the weaker context must be
    visible and can never silently upgrade the plan to publishable; the
    user is told a regenerate restores the full quality gate.
    """
    if quality is None:
        return
    quality.validation_mode = "legacy_weak"
    if not any(
        getattr(issue, "code", "") == "LEGACY_WEAK_VALIDATION"
        for issue in quality.issues
    ):
        quality.issues.append(
            TripPlanQualityIssue(
                code="LEGACY_WEAK_VALIDATION",
                severity="info",
                path="quality",
                message="该行程缺少生成时的完整请求快照，本次为弱校验。",
                suggestion="重新生成行程可获得完整质量校验。",
            )
        )
    if quality.quality_status == "publishable":
        quality.quality_status = "needs_review"
        quality.publishable = False


def can_save_user_draft(quality) -> bool:
    """Whether a user-edited plan may be stored as their own draft.

    This is a **draft-save** policy, deliberately laxer than publishability,
    and it is not a fourth ``quality_status`` derivation: publishability is
    decided solely by ``resolve_plan_quality_status``. A plan the gate calls
    ``needs_review`` is still the user's own work and stays savable; only a
    failed evaluation or an error-severity issue is refused.

    Saving a draft never implies the plan may be published, and never relaxes
    the identity, ownership or unforgeable-field checks that run before it
    (``_reject_identity_mutation``, ``_restore_verified_plan_facts``, the
    owner-scoped lookup and the If-Match revision guard).
    """
    if quality is None:
        return False
    status = str(getattr(quality, "status", "") or "").strip().lower()
    if status == "failed":
        return False
    return not any(
        str(getattr(issue, "severity", "") or "").strip().lower() == "error"
        for issue in (getattr(quality, "issues", None) or [])
    )


class _SyncGenerationProgress:
    """Callable progress callback for sync generation with cancellation token APIs."""

    def __init__(self, token: TripGenerationCancellationToken) -> None:
        self._token = token

    def raise_if_cancelled(self) -> None:
        self._token.raise_if_cancelled()

    def begin_finalization(self) -> None:
        self._token.begin_finalization()

    def cancel(self, reason: str = "generation_cancelled") -> bool:
        return self._token.cancel(reason)

    def __call__(self, **_payload) -> None:
        self.raise_if_cancelled()


async def _generate_sync_with_deadline(request: TripRequest):
    """Run synchronous planning under the shared wall-clock budget.

    Returns ``(plan, progress)``. Callers must claim finalization via the
    progress object before any persistence or delivery side effects.

    Cancellation is cooperative: ``asyncio.wait_for`` cannot kill the
    threadpool worker. On timeout we mark the token cancelled so the
    planner stops at the next checkpoint and capacity is released when
    the worker returns; the HTTP response does not wait for that join.
    """
    settings = get_settings()
    max_runtime = max(
        0.001,
        float(getattr(settings, "trip_generation_max_runtime_seconds", 600.0) or 600.0),
    )
    progress = _SyncGenerationProgress(
        TripGenerationCancellationToken(time.monotonic() + max_runtime)
    )

    def worker():
        return run_with_generation_capacity(
            lambda: get_trip_planner_agent().plan_trip(
                request,
                progress_callback=progress,
            )
        )

    try:
        plan = await asyncio.wait_for(run_in_threadpool(worker), timeout=max_runtime)
    except asyncio.TimeoutError as exc:
        # Signal cooperative cancel first; capacity remains held until worker exits.
        progress.cancel("generation_timeout")
        raise HTTPException(
            status_code=504,
            detail="行程生成超时，请稍后重试。",
        ) from exc
    except TripGenerationCancelledError as exc:
        progress.cancel(getattr(exc, "reason", None) or "generation_timeout")
        raise HTTPException(
            status_code=504,
            detail="行程生成超时，请稍后重试。",
        ) from exc
    return plan, progress


def _normalize_client_ip(host: str | None) -> str:
    value = (host or "").strip().lower()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return (value or "unknown")[:64]


def _trip_generation_rate_identity(
    current_user: AuthenticatedUser | None,
    http_request: HttpRequest,
) -> str:
    """Build a process-local rate-limit identity for trip generation creates.

    Priority: authenticated user_id → normalized client IP.
    Does not trust client-supplied user ids or X-Forwarded-For.
    """
    if current_user is not None and str(current_user.user_id or "").strip():
        return f"user:{str(current_user.user_id).strip()}"
    peer = http_request.client.host if http_request.client else None
    return f"ip:{_normalize_client_ip(peer)}"


def _enforce_trip_generation_rate_limit(
    http_request: HttpRequest,
    current_user: AuthenticatedUser | None,
) -> None:
    """Consume one trip-generation token after auth/body validation succeeded.

    Distinct from generation capacity (concurrent workers). A 429 here means
    the identity created too many generation requests in the window.
    """
    settings = get_settings()
    limit = max(1, int(getattr(settings, "trip_generation_rate_limit", 10) or 10))
    window = max(
        1,
        int(getattr(settings, "trip_generation_rate_window_seconds", 60) or 60),
    )
    identity = _trip_generation_rate_identity(current_user, http_request)
    retry_after = get_request_rate_limit_service(http_request).check(
        "trip-generation",
        identity,
        limit=limit,
        window_seconds=window,
    )
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后重试。",
            headers={"Retry-After": str(retry_after)},
        )


def _quality_rejection_detail(exc: "TripPlanQualityRejectedError") -> dict:
    """Map quality rejection to frontend-compatible detail {message, issues}."""
    issues: list[dict] = []
    quality = getattr(exc, "quality", None)
    for issue in getattr(quality, "issues", None) or []:
        code = getattr(issue, "code", None)
        severity = getattr(issue, "severity", None)
        message = getattr(issue, "message", None)
        if hasattr(code, "value"):
            code = code.value
        if hasattr(severity, "value"):
            severity = severity.value
        item = {
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
    }



def _plan_is_publishable(plan: TripPlan) -> bool:
    """Legacy helper: true only when the unified gate says publishable."""
    return _resolve_quality_status(plan) == "publishable"


def _resolve_quality_status(plan: TripPlan) -> str:
    """Delegate to the quality service's unified gate resolver."""
    return resolve_plan_quality_status(plan)


def _email_delivery_for_plan(
    request: TripRequest,
    trip_plan: TripPlan,
    plan_no: str | None,
    current_user: AuthenticatedUser | None,
    client_ip: str,
) -> dict | None:
    """Build the email delivery outcome for a generated plan.

    Shared by the sync and job paths.  Never raises: notification failures
    are reported inside the returned payload so they cannot affect the trip
    result itself.
    """
    if not request.email_on_completion:
        return None
    recipient = str(request.delivery_email or "").strip()
    if current_user is None:
        return {
            "requested": True,
            "sent": False,
            "dry_run": False,
            "to": recipient or None,
            "message": "登录后才能发送旅行计划邮件。",
        }
    recipient = recipient or (current_user.email or "")
    if not recipient:
        return {
            "requested": True,
            "sent": False,
            "dry_run": False,
            "to": None,
            "message": "请填写收件邮箱或先在账号中绑定邮箱。",
        }
    try:
        return deliver_trip_plan_email(
            recipient,
            trip_plan,
            plan_no,
            user_id=current_user.user_id,
            client_ip=client_ip,
        )
    except Exception as email_error:
        print(
            "[trip] email delivery failed (not critical): "
            f"{type(email_error).__name__}"
        )
        return {
            "requested": True,
            "sent": False,
            "dry_run": False,
            "to": recipient,
            "message": "邮件服务暂时不可用，行程已正常保存。",
        }


def _notify_push_safely(user_id: str, city: str, plan_no: str) -> None:
    """Best-effort web push; failures must never affect the trip result."""
    try:
        notify_trip_plan_ready(user_id, city, plan_no)
    except Exception as push_error:
        print(
            "[trip] push notify failed (not critical): "
            f"{type(push_error).__name__}"
        )


def _derived_quality_status(plan: TripPlan) -> str:
    """Internal compatibility label only — not a second source of truth.

    blocking → blocked
    publishable + review_required → needs_review
    publishable + not review_required → publishable
    """
    quality = getattr(plan, "quality", None)
    if quality is None or not _plan_is_publishable(plan):
        return "blocked"
    if bool(getattr(quality, "review_required", False)):
        return "needs_review"
    return "publishable"


def _job_owner(current_user: AuthenticatedUser | None, http_request: HttpRequest) -> str:
    if current_user is not None:
        return f"user:{current_user.user_id}"
    client_ip = http_request.client.host if http_request.client else "unknown"
    return f"anonymous:{client_ip}"


def _job_cookie_name(job_id: str) -> str:
    return f"lingtu_trip_job_{job_id}"


def _raise_if_generation_cancelled(progress) -> None:
    checker = getattr(progress, "raise_if_cancelled", None)
    if callable(checker):
        checker()


def _begin_generation_finalization(progress) -> None:
    begin = getattr(progress, "begin_finalization", None)
    if callable(begin):
        begin()
    else:
        _raise_if_generation_cancelled(progress)


router = APIRouter(prefix="/trip", tags=["旅行规划"])


@router.post(
    "/plan",
    response_model=TripPlanResponse,
    summary="生成旅行计划",
    description="根据用户输入的旅行需求,生成详细的旅行计划"
)
async def plan_trip(
    request: TripRequest,
    background_tasks: BackgroundTasks,
    http_request: HttpRequest,
    current_user: AuthenticatedUser | None = Depends(get_optional_current_user),
):
    """
    生成旅行计划

    Args:
        request: 旅行请求参数

    Returns:
        旅行计划响应
    """
    try:
        # Reject past dates / hard semantic conflicts before rate-limit spend
        # or expensive agent initialization.
        _validate_generation_request(request)
        # After auth + TripRequest validation: count only real generation creates.
        _enforce_trip_generation_rate_limit(http_request, current_user)

        print(f"\n{'='*60}")
        print(f"📥 收到旅行规划请求:")
        print(f"   城市: {request.city}")
        print(f"   日期: {request.start_date} - {request.end_date}")
        print(f"   天数: {request.travel_days}")
        print(f"{'='*60}\n")

        print("🚀 开始生成旅行计划...")
        trip_plan, boundary = await _generate_sync_with_deadline(request)
        try:
            _begin_generation_finalization(boundary)
        except TripGenerationCancelledError as exc:
            raise HTTPException(
                status_code=504,
                detail="行程生成超时，请稍后重试。",
            ) from exc

        print("✅ 旅行计划生成成功,准备返回响应\n")

        # Reviewable model: reject only non-publishable/blocking plans.
        if not _plan_is_publishable(trip_plan):
            raise TripPlanQualityRejectedError(
                quality=getattr(trip_plan, "quality", None),
                plan=trip_plan,
            )
        quality = getattr(trip_plan, "quality", None)
        review_required = (
            bool(getattr(quality, "review_required", False)) if quality else False
        )
        needs_review = review_required

        plan_no = None
        try:
            if current_user is None:
                print("[trip] anonymous request - generated plan will not be saved")
            elif needs_review:
                print("[trip] needs_review plan - not auto-persisted")
            else:
                # warning + clean both may persist; blocking already rejected
                plan_no = get_travel_plan_data_service().save_trip_plan(
                    request,
                    trip_plan,
                    user_id=current_user.user_id,
                    user_role=current_user.role,
                    source="generated",
                )
                print(f"[trip] travel plan saved to dataset: {plan_no}")
        except Exception as save_error:
            print(f"[trip] save travel plan skipped (not critical): {save_error}")

        if plan_no and current_user is not None:
            # Wrapped, like the job path: a raw notifier raising inside a
            # BackgroundTask surfaces as an unhandled ASGI exception after the
            # body is already written.
            background_tasks.add_task(
                _notify_push_safely,
                current_user.user_id,
                trip_plan.city,
                plan_no,
            )

        quality_status = _derived_quality_status(trip_plan)

        email_delivery = None
        if request.email_on_completion and not needs_review:
            email_delivery = await run_in_threadpool(
                _email_delivery_for_plan,
                request,
                trip_plan,
                plan_no,
                current_user,
                http_request.client.host if http_request.client else "unknown",
            )

        return TripPlanResponse(
            success=True,
            message=(
                "行程已生成，以下事项需要你确认"
                if review_required
                else "旅行计划生成成功"
            ),
            data=trip_plan,
            plan_no=plan_no,
            email_delivery=email_delivery,
            needs_review=needs_review,
            quality_status=quality_status,
        )

    except TripPlanQualityRejectedError as exc:
        raise HTTPException(
            status_code=422,
            detail=_quality_rejection_detail(exc),
        )
    except TripGenerationCapacityError:
        # Same contract as /plan-jobs: capacity pressure is a retryable 429,
        # never a 500 with internal detail.
        raise HTTPException(
            status_code=429,
            detail="当前规划任务较多，请等待已有任务完成后再试。",
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ 生成旅行计划失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"生成旅行计划失败: {str(e)}"
        )


@router.get(
    "/history",
    summary="查询个人旅行历史",
    description="返回当前用户的历史行程列表和统计数据"
)
async def trip_history(
    limit: int = 20,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """查询当前登录用户的历史行程和统计"""
    from ...services.database_service import fetch_all, fetch_one
    from ...services.schema import init_db

    init_db()
    trips = fetch_all(
        "SELECT plan_no, destination, start_date, end_date, travel_days, "
        "budget, transportation, summary, created_at, "
        "CASE WHEN plan_json IS NOT NULL AND plan_json <> '{}' THEN 1 ELSE 0 END AS has_detail "
        "FROM travel_plans WHERE user_id = :uid ORDER BY created_at DESC LIMIT :lim",
        {"uid": current_user.user_id, "lim": limit},
    )
    stats = fetch_one(
        "SELECT COUNT(*) AS total, ROUND(AVG(budget), 0) AS avg_budget, "
        "SUM(travel_days) AS total_days "
        "FROM travel_plans WHERE user_id = :uid",
        {"uid": current_user.user_id},
    )
    fav = fetch_all(
        "SELECT destination AS city, COUNT(*) AS count "
        "FROM travel_plans WHERE user_id = :uid "
        "GROUP BY destination ORDER BY count DESC LIMIT 5",
        {"uid": current_user.user_id},
    )
    return {
        "success": True,
        "user_id": current_user.user_id,
        "stats": {
            "total_trips": stats.get("total", 0) if stats else 0,
            "avg_budget": (stats.get("avg_budget") or 0) if stats else 0,
            "total_days": (stats.get("total_days") or 0) if stats else 0,
        } if stats else {"total_trips": 0, "avg_budget": 0, "total_days": 0},
        "fav_cities": [{"city": r["city"], "count": r["count"]} for r in fav],
        "trips": [
            {
                "plan_no": t["plan_no"],
                "destination": t["destination"],
                "start_date": t["start_date"],
                "end_date": t["end_date"],
                "travel_days": t["travel_days"],
                "budget": t["budget"],
                "transportation": t["transportation"],
                "summary": t["summary"],
                "created_at": t["created_at"],
                "has_detail": bool(t["has_detail"]),
            }
            for t in trips
        ],
    }


@router.get("/history/{plan_no}", response_model=TripPlanResponse, summary="读取历史旅行计划")
async def trip_history_detail(
    plan_no: str,
    response: Response,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    service = get_travel_plan_data_service()
    snapshot_fn = getattr(service, "get_trip_plan_snapshot", None)
    if callable(snapshot_fn):
        snapshot = snapshot_fn(plan_no, current_user.user_id)
        if snapshot is not None:
            plan, _raw, revision = snapshot
            response.headers["ETag"] = f'"{revision}"'
            return TripPlanResponse(
                success=True,
                message="旅行计划读取成功",
                data=plan,
                plan_no=plan_no,
            )
    plan = service.get_trip_plan(plan_no, current_user.user_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="未找到该旅行计划，或当前用户无权访问")
    revision_fn = getattr(service, "revision_for_plan", None)
    if callable(revision_fn):
        response.headers["ETag"] = f'"{revision_fn(plan)}"'
    return TripPlanResponse(success=True, message="旅行计划读取成功", data=plan, plan_no=plan_no)


@router.put("/history/{plan_no}", response_model=TripPlanResponse, summary="保存旅行计划修改")
async def update_trip_history(
    plan_no: str,
    plan: TripPlan,
    http_request: HttpRequest,
    response: Response,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    service = get_travel_plan_data_service()
    raw_json = None
    revision = None
    existing = None

    snapshot_fn = getattr(service, "get_trip_plan_snapshot", None)
    if callable(snapshot_fn):
        try:
            snapshot = snapshot_fn(plan_no, current_user.user_id)
        except TypeError:
            snapshot = None
        if snapshot is not None:
            existing, raw_json, revision = snapshot

    if existing is None:
        existing = service.get_trip_plan(plan_no, current_user.user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="未找到该旅行计划，或当前用户无权修改")

    if_match = http_request.headers.get("if-match")
    if if_match is not None and revision is not None:
        token = if_match.strip()
        if token.startswith("W/"):
            token = token[2:].strip()
        if token.startswith('"') and token.endswith('"') and len(token) >= 2:
            token = token[1:-1]
        if token != revision:
            raise HTTPException(
                status_code=409,
                detail="行程已被其他人修改，请刷新后再保存。",
            )

    _reject_identity_mutation(plan, existing)

    try:
        _restore_verified_plan_facts(plan, existing)
    except UntrustedTripEditError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    request_ctx = None
    get_request = getattr(service, "get_trip_request", None)
    if callable(get_request):
        request_ctx = get_request(plan_no, current_user.user_id)
    if request_ctx is not None:
        plan.quality = get_trip_plan_quality_service().evaluate(request_ctx, plan)
        if not can_save_user_draft(plan.quality):
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "修改后的行程存在关键问题，未保存。",
                    "issues": [
                        {
                            "code": getattr(issue, "code", "TRIP_PLAN_QUALITY_REJECTED"),
                            "severity": getattr(issue, "severity", "error"),
                            "message": getattr(issue, "message", "质量检查未通过"),
                        }
                        for issue in (plan.quality.issues if plan.quality else [])
                    ]
                    or [
                        {
                            "code": "TRIP_PLAN_QUALITY_REJECTED",
                            "severity": "error",
                            "message": "修改后的行程存在关键问题，未保存。",
                        }
                    ],
                },
            )

    update_kwargs = {}
    if if_match is not None and raw_json is not None:
        update_kwargs["expected_plan_json"] = raw_json
    try:
        updated = service.update_trip_plan(
            plan_no,
            current_user.user_id,
            plan,
            **update_kwargs,
        )
    except TypeError:
        updated = service.update_trip_plan(plan_no, current_user.user_id, plan)
    if not updated:
        raise HTTPException(
            status_code=409 if if_match is not None else 404,
            detail=(
                "行程已被其他人修改，请刷新后再保存。"
                if if_match is not None
                else "未找到该旅行计划，或当前用户无权修改"
            ),
        )
    revision_fn = getattr(service, "revision_for_plan", None)
    if callable(revision_fn):
        response.headers["ETag"] = f'"{revision_fn(plan)}"'
    return TripPlanResponse(success=True, message="旅行计划修改已保存", data=plan, plan_no=plan_no)


@router.post("/plan-jobs", summary="创建带实时进度的旅行规划任务")
def create_trip_plan_job(
    request: TripRequest,
    http_request: HttpRequest,
    response: Response,
    current_user: AuthenticatedUser | None = Depends(get_optional_current_user),
):
    _validate_generation_request(request)
    # After auth + TripRequest validation: shared create quota with /plan.
    _enforce_trip_generation_rate_limit(http_request, current_user)
    owner_key = _job_owner(current_user, http_request)
    client_ip = http_request.client.host if http_request.client else "unknown"

    def worker(progress):
        _raise_if_generation_cancelled(progress)
        trip_plan = run_with_generation_capacity(
            lambda: get_trip_planner_agent().plan_trip(
                request,
                progress_callback=progress,
            )
        )
        _raise_if_generation_cancelled(progress)

        quality_status = _resolve_quality_status(trip_plan)
        if quality_status == "blocked":
            raise TripPlanQualityRejectedError(
                quality=getattr(trip_plan, "quality", None),
                plan=trip_plan,
            )

        quality = getattr(trip_plan, "quality", None)
        review_required = (
            bool(getattr(quality, "review_required", False)) if quality else False
        )
        needs_review = review_required

        plan_no = None
        if current_user is not None:
            # Publishable includes reviewable warnings — still persist.
            _begin_generation_finalization(progress)
            progress(
                stage="finalizing",
                progress=99,
                message=(
                    "正在安全保存并准备结果"
                    if not review_required
                    else "质量提示已生成，正在保存行程"
                ),
                detail=(
                    "质量检查已完成，正在保存行程。"
                    if not review_required
                    else (
                        f"方案评分 {getattr(quality, 'score', 0)}/100，"
                        "可交付但仍需复核。"
                    )
                ),
                meta={
                    "review_required": review_required,
                    "quality_score": getattr(quality, "score", 0) if quality else 0,
                },
            )
            _raise_if_generation_cancelled(progress)
            # Deliberately unguarded, unlike the sync path: the job contract is
            # "a completed job yields a retrievable plan_no", so a save failure
            # must surface as a failed job rather than a result the user can
            # never fetch again. Pinned by test_save_failure_does_not_complete.
            plan_no = get_travel_plan_data_service().save_trip_plan(
                request,
                trip_plan,
                user_id=current_user.user_id,
                user_role=current_user.role,
                source="generated",
            )
        elif current_user is not None and needs_review:
            progress(
                stage="finalizing",
                progress=99,
                message="行程已生成，需要确认以下事项",
                detail=(
                    f"方案评分 {getattr(quality, 'score', 0)}/100，"
                    "部分信息需要你复核后再保存。"
                ),
                meta={
                    "quality_score": getattr(quality, "score", 0),
                    "needs_review": True,
                },
            )
            _raise_if_generation_cancelled(progress)
        else:
            progress(
                stage="finalizing",
                progress=99,
                message="正在整理生成结果",
                detail="匿名请求不会保存历史记录。",
                meta={},
            )

        _raise_if_generation_cancelled(progress)

        # Notifications mirror the sync path and run inside the worker
        # thread; both helpers are failure-isolated so a broken SMTP or
        # push provider can never fail the job or lose the plan result.
        email_delivery = None
        if request.email_on_completion and not needs_review:
            email_delivery = _email_delivery_for_plan(
                request, trip_plan, plan_no, current_user, client_ip
            )
        if plan_no and current_user is not None:
            _notify_push_safely(current_user.user_id, trip_plan.city, plan_no)

        return jsonable_encoder(
            TripPlanResponse(
                success=True,
                message=(
                    "行程已生成，以下事项需要你确认"
                    if review_required
                    else "旅行计划生成成功"
                ),
                data=trip_plan,
                plan_no=plan_no,
                email_delivery=email_delivery,
                needs_review=needs_review,
                quality_status=quality_status,
            )
        )

    service = get_trip_generation_job_service()
    try:
        job = service.start(owner_key, worker)
    except TripGenerationCapacityError:
        raise HTTPException(
            status_code=429,
            detail="当前规划任务较多，请等待已有任务完成后再试。",
        )
    settings = get_settings()
    response.set_cookie(
        key=_job_cookie_name(job.job_id),
        value=job.access_token,
        max_age=service.ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure or http_request.url.scheme == "https",
        samesite="strict",
        # Cookie path covers events + cancel under the same job id.
        path=f"/api/trip/plan-jobs/{job.job_id}",
    )
    return {
        "success": True,
        "job_id": job.job_id,
        "stream_url": f"/api/trip/plan-jobs/{job.job_id}/events",
    }


@router.get("/plan-jobs/{job_id}/events", summary="订阅旅行规划实时进度")
def stream_trip_plan_job(
    http_request: HttpRequest,
    job_id: str = Path(..., pattern=r"^[a-f0-9]{32}$"),
    after: int = Query(default=0, ge=0),
    current_user: AuthenticatedUser | None = Depends(get_optional_current_user),
):
    service = get_trip_generation_job_service()
    cookie_token = http_request.cookies.get(_job_cookie_name(job_id), "")
    job = service.get(
        job_id,
        _job_owner(current_user, http_request),
        access_token=cookie_token,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")

    header_id = http_request.headers.get("last-event-id", "")
    try:
        after_id = max(after, int(header_id or 0))
    except ValueError:
        after_id = after

    def event_stream():
        for event in service.events(job, after_id=after_id):
            if event is None:
                yield ": heartbeat" + "\n\n"
                continue
            payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            yield (
                f"id: {event['id']}"
                + "\n"
                + f"event: {event['type']}"
                + "\n"
                + f"data: {payload}"
                + "\n\n"
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/plan-jobs/{job_id}/cancel", summary="取消旅行规划任务")
def cancel_trip_plan_job(
    http_request: HttpRequest,
    job_id: str = Path(..., pattern=r"^[a-f0-9]{32}$"),
    current_user: AuthenticatedUser | None = Depends(get_optional_current_user),
):
    service = get_trip_generation_job_service()
    cookie_token = http_request.cookies.get(_job_cookie_name(job_id), "")
    job = service.get(
        job_id,
        _job_owner(current_user, http_request),
        access_token=cookie_token,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    cancelled = service.cancel(job, reason="client_cancelled")
    return {
        "success": True,
        "job_id": job.job_id,
        "cancelled": cancelled,
        "status": job.status,
    }


@router.get(
    "/health",
    summary="健康检查",
    description="检查旅行规划服务是否正常"
)
async def health_check(http_request: HttpRequest):
    """Liveness only: minimal, cheap, and rate limited.

    The endpoint is public, so it deliberately reveals nothing about the search
    provider, the pipeline shape or live capacity, and it never constructs the
    planner — a cold-start flood would otherwise pin every threadpool worker on
    ``_multi_agent_planner_lock`` and starve real generation requests.
    Detailed diagnostics belong on an authenticated internal endpoint.
    """
    settings = get_settings()
    retry_after = get_request_rate_limit_service(http_request).check(
        "service-health",
        f"ip:{_normalize_client_ip(http_request.client.host if http_request.client else None)}",
        limit=max(1, int(getattr(settings, "health_rate_limit", 30) or 30)),
        window_seconds=max(
            1, int(getattr(settings, "health_rate_window_seconds", 60) or 60)
        ),
    )
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后重试。",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        # Read the singleton without building it: this reports readiness, not
        # the health of a planner we would have to pay to construct.
        return {
            "status": "healthy",
            "service": "trip-planner",
            "version": str(getattr(settings, "app_version", "") or ""),
            "planner_initialized": planner_is_initialized(),
        }
    except Exception as e:
        # Never leak provider/internal details through a public endpoint.
        print(f"[trip] health check failed: {type(e).__name__}")
        raise HTTPException(status_code=503, detail="服务不可用")
