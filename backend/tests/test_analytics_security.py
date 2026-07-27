"""Travel-analytics authorization, SQL boundary, and upload safety tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes import agent as agent_routes
from app.services.auth_service import AuthenticatedUser
from app.tools.llm_sql_agent_tool import build_sql_plan_with_llm, run_llm_sql_plan
from app.tools.sql_agent_tool import SQLPlan
from app.api.auth import get_current_user


def _user(role: str = "user") -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=f"u_{role}_analytics",
        username=f"{role}_analytics",
        email=None,
        role=role,
    )


@pytest.fixture
def analytics_auth_client():
    app.dependency_overrides[get_current_user] = lambda: _user("user")
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_public_llm_sql_api_disabled_for_every_role() -> None:
    for role in ("user", "manager", "admin", "guest"):
        assert build_sql_plan_with_llm("SELECT * FROM users", "u1", role) is None

    with pytest.raises(PermissionError, match="模型生成 SQL 已禁用"):
        run_llm_sql_plan(
            SQLPlan(
                intent="city_rank",
                sql="SELECT destination FROM travel_plans",
                params={},
                agent="SQLAgent",
                title="t",
            ),
            "admin",
        )


def test_agent_chat_capacity_returns_429(monkeypatch, analytics_auth_client) -> None:
    monkeypatch.setattr(agent_routes, "_chat_slots", type("S", (), {"acquire": lambda *a, **k: False})())

    response = analytics_auth_client.post(
        "/api/agent/chat",
        json={"message": "统计本月目的地"},
    )
    assert response.status_code == 429
    assert "稍后再试" in response.json()["detail"]


def test_agent_permission_error_uses_generic_message(monkeypatch, analytics_auth_client) -> None:
    def boom(*_a, **_k):
        raise PermissionError("internal role hierarchy secret detail")

    monkeypatch.setattr(
        agent_routes,
        "get_travel_agent_graph",
        lambda: type("G", (), {"run": staticmethod(boom)})(),
    )
    response = analytics_auth_client.post(
        "/api/agent/chat",
        json={"message": "读取全部审计日志"},
    )
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail == "当前账号无权执行该分析请求。"
    assert "hierarchy" not in detail
    assert "secret" not in detail


def test_analyze_file_rejects_legacy_xls(analytics_auth_client) -> None:
    response = analytics_auth_client.post(
        "/api/agent/analyze-file",
        files={"file": ("legacy.xls", b"not-a-real-xls", "application/vnd.ms-excel")},
        data={"question": "摘要"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "不支持的文件类型。"


def test_analyze_file_rejects_oversized_upload(analytics_auth_client) -> None:
    big = b"x" * (20 * 1024 * 1024 + 1)
    response = analytics_auth_client.post(
        "/api/agent/analyze-file",
        files={"file": ("big.txt", big, "text/plain")},
        data={"question": "摘要"},
    )
    assert response.status_code == 413


def test_analyze_file_value_error_is_safe(monkeypatch, analytics_auth_client) -> None:
    def raise_value(*_a, **_k):
        raise ValueError("Office 文件压缩比异常 secret-path")

    monkeypatch.setattr(
        "app.tools.file_analysis_tool.process_uploaded_file",
        raise_value,
    )
    response = analytics_auth_client.post(
        "/api/agent/analyze-file",
        files={"file": ("doc.docx", b"PK\x03\x04fake", "application/octet-stream")},
        data={"question": "摘要"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "文件内容或压缩结构不符合安全限制。"
    assert "secret-path" not in response.json()["detail"]


def test_agent_chat_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.post("/api/agent/chat", json={"message": "统计目的地"})
    assert response.status_code == 401
