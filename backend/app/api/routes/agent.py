"""Natural-language, role-scoped travel analytics routes."""

from __future__ import annotations

import os
import tempfile
import threading
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request as HttpRequest, UploadFile
from pydantic import BaseModel, EmailStr, Field
from starlette.concurrency import run_in_threadpool

from ...agents.graph.travel_agent_graph import get_travel_agent_graph
from ...services.auth_service import AuthenticatedUser
from ...tools.analytics_context_tool import get_data_status, get_role_capabilities
from ..auth import get_current_user

router = APIRouter(prefix="/agent", tags=["多智能体数据分析"])


class AgentCapacityError(RuntimeError):
    pass


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


class AgentChatResponse(BaseModel):
    success: bool
    intent: str = ""
    agent: str = ""
    tool: str = ""
    table: list[dict[str, Any]] = Field(default_factory=list)
    chart: Optional[dict[str, Any]] = None
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
        return await run_in_threadpool(
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
    except AgentCapacityError as exc:
        raise HTTPException(
            status_code=429,
            detail="当前智能分析任务较多，请稍后再试。",
        ) from exc
    except PermissionError as exc:
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
        raise HTTPException(status_code=400, detail="文件内容或压缩结构不符合安全限制。") from exc
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
