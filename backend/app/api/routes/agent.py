"""Natural-language, role-scoped travel analytics routes."""

from __future__ import annotations

import os
import tempfile
import threading
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request as HttpRequest, UploadFile
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from ...agents.graph.travel_agent_graph import get_travel_agent_graph
from ...services.auth_service import AuthenticatedUser
from ...tools.analytics_context_tool import get_data_status, get_role_capabilities
from ...tools.chart_tool import sanitize_chart_payload
from ..auth import get_current_user

router = APIRouter(prefix="/agent", tags=["多智能体数据分析"])


class AgentCapacityError(RuntimeError):
    """Process-local analytics concurrency limit reached."""


# Best-effort per-process capacity. Not a cluster-wide quota; each worker
# counts independently. Prevents unbounded concurrent LLM/file work.
_chat_slots = threading.BoundedSemaphore(6)
_file_analysis_slots = threading.BoundedSemaphore(2)


def _run_with_capacity(slot: threading.BoundedSemaphore, worker):
    if not slot.acquire(blocking=False):
        raise AgentCapacityError("agent capacity exhausted")
    try:
        return worker()
    finally:
        slot.release()


class AgentChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="自然语言问题")
    email: Optional[EmailStr] = Field(default=None, description="邮件发送目标")


class PermissionResult(BaseModel):
    role: str
    allowed: bool
    reason: str = ""


class ChartSeriesModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=64)
    values: list[float] = Field(default_factory=list, max_length=50)

    @field_validator("values")
    @classmethod
    def _finite_values(cls, value: list[float]) -> list[float]:
        import math

        cleaned: list[float] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError("chart values must be finite numbers")
            number = float(item)
            if not math.isfinite(number):
                raise ValueError("chart values must be finite numbers")
            cleaned.append(number)
        return cleaned


class ChartPayloadModel(BaseModel):
    """Restricted chart schema exposed to the frontend (not a chart-library option)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["bar", "line", "pie"]
    title: str = Field(default="", max_length=120)
    x_label: str = Field(default="", max_length=64)
    y_label: str = Field(default="", max_length=64)
    categories: list[str] = Field(default_factory=list, max_length=50)
    series: list[ChartSeriesModel] = Field(default_factory=list, max_length=8)
    truncated: bool = False
    note: str = Field(default="", max_length=200)

    @field_validator("categories", mode="before")
    @classmethod
    def _clean_categories(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item)[:100] for item in value[:50]]


class AgentChatResponse(BaseModel):
    success: bool
    intent: str = ""
    agent: str = ""
    tool: str = ""
    table: list[dict[str, Any]] = Field(default_factory=list)
    chart: Optional[ChartPayloadModel] = None
    result: str = ""
    permission: PermissionResult
    sensitive: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class FileAnalysisResponse(BaseModel):
    success: bool
    summary: str = ""
    suggestions: list[str] = Field(default_factory=list)
    extracted_info: dict[str, Any] = Field(default_factory=dict)
    table: list[dict[str, Any]] = Field(default_factory=list)
    file_type: str = ""


@router.get("/capabilities")
async def agent_capabilities(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Describe what the current server-authenticated role may analyse."""
    return get_role_capabilities(current_user.role)


@router.get("/data-status")
async def agent_data_status(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Return data coverage for the current role's enforced row scope."""
    return await run_in_threadpool(get_data_status, current_user.user_id, current_user.role)


@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(
    request: AgentChatRequest,
    http_request: HttpRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        graph = get_travel_agent_graph()
        payload = await run_in_threadpool(
            _run_with_capacity,
            _chat_slots,
            lambda: graph.run(
                current_user.user_id,
                current_user.role,
                request.message,
                str(request.email) if request.email else current_user.email,
                http_request.client.host if http_request.client else "unknown",
            ),
        )
        if isinstance(payload, dict):
            # Strip any non-conforming chart object before response validation.
            # Invalid chart must never take down text/table responses.
            safe = dict(payload)
            safe["chart"] = sanitize_chart_payload(payload.get("chart"))
            try:
                return AgentChatResponse.model_validate(safe)
            except ValidationError:
                safe["chart"] = None
                return AgentChatResponse.model_validate(safe)
        return payload
    except AgentCapacityError as exc:
        raise HTTPException(
            status_code=429,
            detail="当前智能分析任务较多，请稍后再试。",
        ) from exc
    except PermissionError as exc:
        # Do not echo internal permission-tool reason strings that may reveal
        # role hierarchy details beyond the fixed client-safe message.
        raise HTTPException(
            status_code=403,
            detail="当前账号无权执行该分析请求。",
        ) from exc
    except Exception as exc:
        print(f"[agent] chat failed: {type(exc).__name__}")
        raise HTTPException(status_code=500, detail="智能分析暂时不可用，请稍后重试。") from exc


@router.post("/analyze-file", response_model=FileAnalysisResponse)
async def analyze_file(
    file: UploadFile = File(..., description="上传文件（支持 TXT/MD/PDF/DOCX/XLSX）"),
    question: str = Form(default="", max_length=2000, description="额外的分析问题（可选）"),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    safe_filename = (file.filename or "")[:255]
    suffix = os.path.splitext(safe_filename)[1].lower()[:16]
    # Legacy .xls is rejected: only modern Office Open XML is supported.
    allowed = {".txt", ".md", ".pdf", ".docx", ".xlsx"}
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail="不支持的文件类型。")

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix or ".txt")
    try:
        max_upload_bytes = 20 * 1024 * 1024
        received = 0
        with os.fdopen(tmp_fd, "wb") as handle:
            while chunk := await file.read(1024 * 1024):
                received += len(chunk)
                if received > max_upload_bytes:
                    raise HTTPException(status_code=413, detail="文件不能超过 20 MB")
                handle.write(chunk)

        from ...tools.file_analysis_tool import process_uploaded_file

        result = await run_in_threadpool(
            _run_with_capacity,
            _file_analysis_slots,
            lambda: process_uploaded_file(tmp_path, question or None),
        )
        try:
            from ...services.travel_plan_data_service import get_travel_plan_data_service

            get_travel_plan_data_service().log_query(
                user_id=current_user.user_id,
                user_role=current_user.role,
                question=f"[文件分析] {safe_filename} {question}".strip()[:4000],
                intent="file_analysis",
                result_summary=result.get("summary", ""),
            )
        except Exception:
            # Analytics/query logging is best-effort and must not fail the upload.
            pass
        return result
    except HTTPException:
        raise
    except AgentCapacityError as exc:
        raise HTTPException(
            status_code=429,
            detail="当前文件分析任务较多，请稍后再试。",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="文件内容或压缩结构不符合安全限制。",
        ) from exc
    except Exception as exc:
        print(f"[agent] file analysis failed: {type(exc).__name__}")
        raise HTTPException(status_code=500, detail="文件分析暂时不可用。") from exc
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@router.get("/health")
def agent_health():
    graph = get_travel_agent_graph()
    return {
        "status": "healthy",
        "graph_available": graph.graph_available,
        "authorization": "server_role_scoped",
        "agents": [
            "SecurityAgent",
            "IntentAgent",
            "RoleAgent",
            "SQLAgent",
            "QualityAgent",
            "ChartAgent",
            "ProfileAgent",
            "RecommendationAgent",
            "PredictAgent",
            "EmailAgent",
            "AuditAgent",
            "ReportAgent",
            "FileAnalysisAgent",
        ],
    }
