from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.agents.graph import travel_agent_graph
from app.api.auth import get_current_user
from app.api.main import app
from app.services import database_service, schema
from app.services.auth_service import AuthenticatedUser
from app.services.db_models import audit_logs, metadata, travel_plans
from app.tools import analytics_context_tool
from app.tools.analytics_context_tool import parse_analysis_period


FROZEN_TODAY = date(2026, 7, 15)


class _FrozenDate(date):
    @classmethod
    def today(cls) -> "_FrozenDate":
        return cls(2026, 7, 15)


def _plan(
    plan_no: str,
    user_id: str,
    destination: str,
    start_date: str,
    *,
    budget: int = 3000,
    actual_cost: int | None = None,
    source: str = "generated",
) -> dict:
    return {
        "plan_no": plan_no,
        "user_id": user_id,
        "user_role": "user",
        "origin_city": "测试出发地",
        "destination": destination,
        "start_date": start_date,
        "end_date": start_date,
        "travel_days": 1,
        "travelers": 1,
        "budget": budget,
        "actual_cost": actual_cost,
        "transportation": "公共交通",
        "accommodation": "测试酒店",
        "preferences": "[]",
        "free_text": f"PRIVATE-FREE-TEXT-{plan_no}",
        "summary": f"{destination} 测试计划",
        "plan_json": "{}" if plan_no.endswith("1") else '{"test": true}',
        "status": "completed",
        "source": source,
        "created_at": f"{start_date} 08:00:00",
    }


@pytest.fixture
def isolated_analytics_database(tmp_path, monkeypatch) -> Iterator[None]:
    """Move every database helper used by analytics to a temporary SQLite file."""
    database_path = tmp_path / "analytics-v1.sqlite3"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    metadata.create_all(engine)

    # database_service helpers look up their module-level engine at call time;
    # schema imported the engine directly, so both references must be replaced.
    monkeypatch.setattr(database_service, "engine", engine)
    monkeypatch.setattr(database_service, "IS_SQLITE", True)
    monkeypatch.setattr(database_service, "DIALECT_NAME", "sqlite")
    monkeypatch.setattr(schema, "engine", engine)
    monkeypatch.setattr(schema, "DIALECT_NAME", "sqlite")
    monkeypatch.setattr(schema, "_inited", True)
    monkeypatch.setattr(analytics_context_tool, "date", _FrozenDate)

    rows = [
        _plan("A-JUL-1", "u_alice", "北京", "2026-07-02", budget=1200, actual_cost=1100),
        _plan("A-JUL-2", "u_alice", "北京", "2026-07-12", budget=1600),
        _plan("A-AUG-1", "u_alice", "杭州", "2026-08-10", budget=2800),
        _plan("A-LY-1", "u_alice", "青岛", "2025-07-10", budget=2200),
        _plan("B-JUL-1", "u_bob", "上海", "2026-07-06", budget=3500, actual_cost=3300),
        _plan("B-SEP-1", "u_bob", "三亚", "2026-09-01", budget=6800),
        _plan("B-LY-1", "u_bob", "厦门", "2025-07-09", budget=2600),
        _plan("B-OLD-1", "u_bob", "成都", "2024-12-18", budget=3100),
        _plan("C-JUL-1", "u_charlie", "上海", "2026-07-20", budget=4100),
        _plan(
            "S-JUL-1",
            "u_sparse",
            "大理",
            "2026-07-08",
            budget=1800,
            source="synthetic_seed",
        ),
    ]
    with engine.begin() as connection:
        connection.execute(travel_plans.insert(), rows)
        connection.execute(
            audit_logs.insert(),
            [
                {
                    "user_id": "u_alice",
                    "user_role": "user",
                    "message": "历史分析问题",
                    "agent": "SQLAgent",
                    "tool": "sql_agent_tool",
                    "allowed": 1,
                    "sensitive_hit": 0,
                    "detail": "{}",
                    "created_at": "2026-07-01 09:00:00",
                }
            ],
        )

    # Force the deterministic sequential runner.  The analytics V1 path does
    # not need LangGraph, an LLM, or any network service for these questions.
    monkeypatch.setattr(
        travel_agent_graph.TravelAgentGraph,
        "_try_build_langgraph",
        lambda _self: (False, None),
    )
    monkeypatch.setattr(travel_agent_graph, "_travel_agent_graph", None)
    monkeypatch.setattr("app.api.main.validate_config", lambda: None)
    monkeypatch.setattr("app.api.main.print_config", lambda: None)

    try:
        yield
    finally:
        engine.dispose()


@pytest.fixture
def analytics_client(isolated_analytics_database):
    identity = {
        "current": AuthenticatedUser(
            user_id="u_alice",
            username="alice",
            email=None,
            role="user",
        )
    }

    def override_current_user() -> AuthenticatedUser:
        return identity["current"]

    app.dependency_overrides[get_current_user] = override_current_user
    try:
        with TestClient(app) as client:
            yield client, identity
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _assume_role(identity: dict, role: str, user_id: str | None = None) -> None:
    identity["current"] = AuthenticatedUser(
        user_id=user_id or f"u_{role}",
        username=role,
        email=None,
        role=role,
    )


def _chat(client: TestClient, message: str) -> dict:
    response = client.post("/api/agent/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


def _destinations(payload: dict) -> dict[str, int]:
    return {
        str(row["目的地"]): int(row["计划数"])
        for row in payload["table"]
        if "目的地" in row and "计划数" in row
    }


def _all_table_keys(payload: dict) -> set[str]:
    return {str(key) for row in payload.get("table", []) for key in row}


def test_period_parser_covers_month_quarter_and_last_year_same_period() -> None:
    month = parse_analysis_period("统计本月旅行去向", today=FROZEN_TODAY)
    assert (month.key, month.start, month.end_exclusive) == (
        "current_month",
        date(2026, 7, 1),
        date(2026, 8, 1),
    )

    quarter = parse_analysis_period("统计本季度旅行去向", today=FROZEN_TODAY)
    assert (quarter.key, quarter.start, quarter.end_exclusive) == (
        "current_quarter",
        date(2026, 7, 1),
        date(2026, 10, 1),
    )

    last_month = parse_analysis_period("统计去年同期旅行去向", today=FROZEN_TODAY)
    assert (last_month.key, last_month.start, last_month.end_exclusive) == (
        "last_year_same_month",
        date(2025, 7, 1),
        date(2025, 8, 1),
    )

    last_quarter = parse_analysis_period("统计去年同期季度旅行去向", today=FROZEN_TODAY)
    assert (last_quarter.key, last_quarter.start, last_quarter.end_exclusive) == (
        "last_year_same_quarter",
        date(2025, 7, 1),
        date(2025, 10, 1),
    )

    month_compare = parse_analysis_period("对比本月和去年同期", today=FROZEN_TODAY)
    assert (
        month_compare.key,
        month_compare.start,
        month_compare.end_exclusive,
        month_compare.comparison_start,
        month_compare.comparison_end_exclusive,
    ) == (
        "current_month", date(2026, 7, 1), date(2026, 8, 1), date(2025, 7, 1), date(2025, 8, 1)
    )

    quarter_compare = parse_analysis_period("对比本季度和去年同期", today=FROZEN_TODAY)
    assert (
        quarter_compare.key,
        quarter_compare.start,
        quarter_compare.end_exclusive,
        quarter_compare.comparison_start,
        quarter_compare.comparison_end_exclusive,
    ) == (
        "current_quarter", date(2026, 7, 1), date(2026, 10, 1), date(2025, 7, 1), date(2025, 10, 1)
    )

    last_year_quarter = parse_analysis_period("统计去年这个季度的去向", today=FROZEN_TODAY)
    assert last_year_quarter.key == "last_year_same_quarter"


def test_user_scope_is_personal_even_without_personal_wording(analytics_client) -> None:
    client, identity = analytics_client
    _assume_role(identity, "user", "u_alice")

    payload = _chat(client, "统计全部历史旅行目的地")

    assert payload["success"] is True
    assert payload["permission"] == {"role": "user", "allowed": True, "reason": ""}
    assert payload["extra"]["analysis"]["scope"] == "personal"
    assert payload["extra"]["analysis"]["sample_size"] == 4
    assert _destinations(payload) == {"北京": 2, "杭州": 1, "青岛": 1}
    assert "上海" not in str(payload["table"])
    assert "u_bob" not in str(payload)



def test_user_recommendation_never_aggregates_other_users(analytics_client) -> None:
    client, identity = analytics_client
    _assume_role(identity, "user", "u_alice")

    payload = _chat(client, "推荐适合我的目的地")

    assert payload["success"] is True
    assert payload["tool"] == "personal_history_tool"
    assert payload["extra"]["analysis"]["scope"] == "personal"
    assert "不读取其他用户" in payload["extra"]["reason"]
    cities = {row["目的地"] for row in payload["table"]}
    assert cities <= {"北京", "杭州", "青岛"}
    assert not ({"上海", "三亚", "厦门", "成都", "大理"} & cities)
def test_manager_gets_anonymous_aggregate_but_not_detail_or_audit(analytics_client) -> None:
    client, identity = analytics_client
    _assume_role(identity, "manager")

    aggregate = _chat(client, "统计本月所有人的旅行去向")
    assert aggregate["success"] is True
    assert aggregate["extra"]["analysis"]["scope"] == "global_aggregate"
    assert aggregate["extra"]["analysis"]["sample_size"] == 5
    assert _destinations(aggregate) == {"北京": 2, "上海": 2, "大理": 1}
    assert _all_table_keys(aggregate) == {"目的地", "计划数", "平均预算"}
    assert not ({"用户", "用户ID", "user_id", "编号", "plan_no"} & _all_table_keys(aggregate))

    detail = _chat(client, "列出全部旅行计划明细")
    assert detail["success"] is False
    assert detail["permission"]["allowed"] is False
    assert detail["table"] == []
    assert "manager" in detail["result"]

    audit = _chat(client, "查询最近的审计日志")
    assert audit["success"] is False
    assert audit["permission"]["allowed"] is False
    assert audit["table"] == []
    assert "admin" in audit["result"]


def test_admin_gets_global_aggregate_and_only_safe_plan_detail(analytics_client) -> None:
    client, identity = analytics_client
    _assume_role(identity, "admin")

    aggregate = _chat(client, "统计本季度所有人的旅行去向")
    assert aggregate["success"] is True
    assert aggregate["extra"]["analysis"]["scope"] == "global"
    assert aggregate["extra"]["analysis"]["sample_size"] == 7
    assert _destinations(aggregate) == {
        "北京": 2,
        "上海": 2,
        "杭州": 1,
        "三亚": 1,
        "大理": 1,
    }

    detail = _chat(client, "列出全部历史旅行计划明细")
    assert detail["success"] is True
    assert len(detail["table"]) == 10
    assert {row["目的地"] for row in detail["table"]} >= {"北京", "上海", "大理"}
    assert _all_table_keys(detail) == {
        "编号",
        "目的地",
        "出发日期",
        "天数",
        "预算",
        "交通",
        "住宿",
        "数据来源",
    }
    assert not (
        {
            "user_id",
            "用户ID",
            "free_text",
            "plan_json",
            "手机号",
            "邮箱",
            "联系人",
            "password_hash",
        }
        & _all_table_keys(detail)
    )
    assert "PRIVATE-FREE-TEXT" not in str(detail)


@pytest.mark.parametrize("role", ["user", "manager", "admin"])
def test_sensitive_field_queries_are_rejected_for_every_role(
    analytics_client,
    role: str,
) -> None:
    client, identity = analytics_client
    _assume_role(identity, role, "u_alice" if role == "user" else None)

    payload = _chat(client, "查询所有用户的手机号、邮箱和联系人")

    assert payload["success"] is False
    assert payload["permission"]["allowed"] is False
    assert payload["agent"] == "SecurityAgent"
    assert payload["table"] == []
    assert payload["sensitive"]["hits"]


def test_time_periods_filter_real_rows_end_to_end(analytics_client) -> None:
    client, identity = analytics_client
    _assume_role(identity, "manager")

    current_month = _chat(client, "统计本月所有人的旅行去向")
    current_quarter = _chat(client, "统计本季度所有人的旅行去向")
    last_year_same_month = _chat(client, "统计去年同期所有人的旅行去向")

    assert current_month["extra"]["analysis"]["period"]["start"] == "2026-07-01"
    assert current_month["extra"]["analysis"]["period"]["end"] == "2026-07-31"
    assert _destinations(current_month) == {"北京": 2, "上海": 2, "大理": 1}

    assert current_quarter["extra"]["analysis"]["period"]["start"] == "2026-07-01"
    assert current_quarter["extra"]["analysis"]["period"]["end"] == "2026-09-30"
    assert _destinations(current_quarter) == {
        "北京": 2,
        "上海": 2,
        "杭州": 1,
        "三亚": 1,
        "大理": 1,
    }

    assert last_year_same_month["extra"]["analysis"]["period"]["start"] == "2025-07-01"
    assert last_year_same_month["extra"]["analysis"]["period"]["end"] == "2025-07-31"
    assert _destinations(last_year_same_month) == {"青岛": 1, "厦门": 1}


def test_capabilities_and_data_status_follow_authenticated_scope(analytics_client) -> None:
    client, identity = analytics_client

    _assume_role(identity, "user", "u_sparse")
    capabilities = client.get("/api/agent/capabilities")
    assert capabilities.status_code == 200
    user_capabilities = capabilities.json()
    assert user_capabilities["role"] == "user"
    assert user_capabilities["scope"] == "personal"
    assert any("其他用户" in item for item in user_capabilities["restrictions"])

    status = client.get("/api/agent/data-status")
    assert status.status_code == 200
    personal_status = status.json()
    assert personal_status["scope"] == "personal"
    assert personal_status["visible_plans"] == 1
    assert personal_status["visible_users"] == 1
    assert personal_status["destinations"] == 1
    assert personal_status["sufficient_for"]["facts"] is True
    assert personal_status["sufficient_for"]["trend"] is False
    assert personal_status["sufficient_for"]["prediction"] is False
    assert any("少于 3 条" in warning for warning in personal_status["warnings"])
    assert any("模拟数据" in warning for warning in personal_status["warnings"])

    sparse_chat = _chat(client, "统计本月旅行去向")
    assert sparse_chat["extra"]["analysis"]["sample_size"] == 1
    assert any("样本仅 1 条" in warning for warning in sparse_chat["extra"]["analysis"]["warnings"])

    _assume_role(identity, "manager")
    manager_capabilities = client.get("/api/agent/capabilities").json()
    assert manager_capabilities["scope"] == "global_aggregate"
    assert any("逐条计划" in item for item in manager_capabilities["restrictions"])
    manager_status = client.get("/api/agent/data-status").json()
    assert manager_status["visible_plans"] == 10
    assert manager_status["visible_users"] == 4

    _assume_role(identity, "admin")
    admin_capabilities = client.get("/api/agent/capabilities").json()
    assert admin_capabilities["scope"] == "global"
    assert "非敏感计划明细" in admin_capabilities["permissions"]
    admin_status = client.get("/api/agent/data-status").json()
    assert admin_status["visible_plans"] == 10
    assert admin_status["visible_users"] == 4
