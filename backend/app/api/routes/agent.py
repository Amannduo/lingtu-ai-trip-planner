"""Natural language multi-agent analysis routes."""

from __future__ import annotations

import os
import tempfile
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ...agents.graph.travel_agent_graph import get_travel_agent_graph

router = APIRouter(prefix="/agent", tags=["多智能体数据分析"])


# ── Chat models ──────────────────────────────────────────────────────────

class AgentChatRequest(BaseModel):
    user_id: str = Field(default="u_current", description="当前用户 ID")
    role: str = Field(default="user", description="用户角色: guest/user/manager/admin")
    message: str = Field(..., description="自然语言问题")
    email: Optional[str] = Field(default=None, description="邮件发送目标")


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


# ── File analysis models ─────────────────────────────────────────────────

class FileAnalysisResponse(BaseModel):
    success: bool
    summary: str = ""
    suggestions: list[str] = Field(default_factory=list)
    extracted_info: dict[str, Any] = Field(default_factory=dict)
    table: list[dict[str, Any]] = Field(default_factory=list)
    file_type: str = ""


# ── Chat endpoint ────────────────────────────────────────────────────────

@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(request: AgentChatRequest):
    try:
        graph = get_travel_agent_graph()
        return await run_in_threadpool(
            graph.run,
            request.user_id,
            request.role,
            request.message,
            request.email,
        )
    except Exception as exc:
        print(f"[agent] chat failed: {exc}")
        raise HTTPException(status_code=500, detail=f"多智能体分析失败: {exc}")


# ── File analysis endpoint ───────────────────────────────────────────────

@router.post("/analyze-file", response_model=FileAnalysisResponse)
async def analyze_file(
    file: UploadFile = File(..., description="上传文件（支持 TXT/MD/PDF/DOCX/XLSX）"),
    question: str = Form(default="", description="额外的分析问题（可选）"),
    user_id: str = Form(default="u_current"),
    role: str = Form(default="user"),
):
    """Upload a travel document and get AI analysis."""
    suffix = ""
    if file.filename and "." in file.filename:
        suffix = os.path.splitext(file.filename)[1].lower()

    allowed = {".txt", ".md", ".pdf", ".docx", ".xlsx", ".xls"}
    if suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {suffix}。支持: {', '.join(allowed)}",
        )

    # Save to temp file
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix or ".txt")
    try:
        content = await file.read()
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(content)

        from ...tools.file_analysis_tool import process_uploaded_file

        result = await run_in_threadpool(
            process_uploaded_file, tmp_path, question or None
        )

        # Log the file analysis query
        try:
            from ...services.travel_plan_data_service import get_travel_plan_data_service

            data_service = get_travel_plan_data_service()
            data_service.log_query(
                user_id=user_id,
                user_role=role,
                question=f"[文件分析] {file.filename} {question}".strip(),
                intent="file_analysis",
                result_summary=result.get("summary", ""),
            )
        except Exception:
            pass

        return result
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[agent] file analysis failed: {exc}")
        raise HTTPException(status_code=500, detail=f"文件分析失败: {exc}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Health endpoint ──────────────────────────────────────────────────────

@router.get("/health")
async def agent_health():
    graph = get_travel_agent_graph()
    return {
        "status": "healthy",
        "graph_available": graph.graph_available,
        "agents": [
            "SecurityAgent",
            "RoleAgent",
            "RouterAgent",
            "SQLAgent",
            "ChartAgent",
            "ProfileAgent",
            "RecommendationAgent",
            "PredictAgent",
            "EmailAgent",
            "ReportAgent",
            "FileAnalysisAgent",
        ],
    }
