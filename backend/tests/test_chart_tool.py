"""Restricted chart payload builder and sanitizer — critical boundary coverage."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.api.routes.agent import ChartPayloadModel
from app.tools.chart_tool import (
    MAX_CATEGORIES,
    MAX_SERIES,
    build_chart,
    sanitize_chart_payload,
)


def _bar_table(n: int = 2):
    return [{"目的地": f"城{i}", "计划数": i + 1} for i in range(n)]


def test_legal_single_series_bar() -> None:
    chart = build_chart("city_rank", _bar_table(2), title="热门目的地")
    assert chart is not None
    assert chart["kind"] == "bar"
    assert chart["categories"] == ["城0", "城1"]
    assert chart["series"][0]["values"] == [1.0, 2.0]
    assert set(chart.keys()) <= {
        "kind",
        "title",
        "x_label",
        "y_label",
        "categories",
        "series",
        "truncated",
        "note",
    }
    assert "xAxis" not in chart and "tooltip" not in chart


def test_legal_multi_series_bar_period_destination() -> None:
    table = [
        {"周期": "本月", "目的地": "甲", "计划数": 3},
        {"周期": "本月", "目的地": "乙", "计划数": 4},
        {"周期": "上月", "目的地": "甲", "计划数": 1},
        {"周期": "上月", "目的地": "乙", "计划数": 2},
    ]
    chart = build_chart("city_rank", table)
    assert chart is not None
    assert chart["kind"] == "bar"
    assert len(chart["series"]) == 2
    assert len(chart["series"][0]["values"]) == len(chart["categories"])


def test_legal_line_and_pie() -> None:
    line = build_chart(
        "budget_trend",
        [{"月份": "2026-01", "平均预算": 1200}, {"月份": "2026-02", "平均预算": 1500}],
    )
    assert line is not None and line["kind"] == "line"

    pie = build_chart(
        "traveler_type_distribution",
        [{"类型": "家庭", "用户数": 3}, {"类型": "独自", "用户数": 5}],
    )
    assert pie is not None and pie["kind"] == "pie"


def test_chart_none_and_empty_inputs() -> None:
    assert build_chart("city_rank", []) is None
    assert sanitize_chart_payload(None) is None
    assert sanitize_chart_payload("not-a-dict") is None


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "radar", "categories": ["a"], "series": [{"name": "s", "values": [1]}]},
        {"kind": "bar", "categories": ["a"], "series": [{"name": "s", "values": [1, 2]}]},
        {"kind": "line", "categories": ["a", "b"], "series": [{"name": "s", "values": [1]}]},
        {"kind": "pie", "categories": ["a"], "series": [{"name": "s", "values": [1, 2]}]},
        {"kind": "bar", "categories": [], "series": [{"name": "s", "values": []}]},
        {"kind": "bar", "categories": ["a"], "series": []},
    ],
)
def test_sanitize_rejects_structure_errors(raw: dict) -> None:
    assert sanitize_chart_payload(raw) is None


def test_sanitize_rejects_extra_library_and_nested_fields() -> None:
    assert sanitize_chart_payload(
        {
            "kind": "bar",
            "title": "ok",
            "categories": ["a"],
            "series": [{"name": "s", "values": [1]}],
            "xAxis": {"type": "category"},
        }
    ) is None
    assert sanitize_chart_payload(
        {
            "kind": "bar",
            "categories": ["a"],
            "series": [{"name": "s", "values": [1], "formatter": "alert(1)"}],
        }
    ) is None
    assert sanitize_chart_payload(
        {
            "kind": "bar",
            "categories": ["a"],
            "series": [{"name": "s", "values": [1]}],
            "__proto__": {"x": 1},
        }
    ) is None
    assert sanitize_chart_payload(
        {
            "kind": "bar",
            "categories": ["a"],
            "series": [{"name": "s", "values": [1]}],
            "constructor": {},
        }
    ) is None


def test_sanitize_returns_new_object_not_original_reference() -> None:
    raw = {
        "kind": "bar",
        "title": "测试",
        "categories": ["甲", "乙"],
        "series": [{"name": "数量", "values": [1.5, 2.5]}],
        "evil": "drop-me",
    }
    clean = sanitize_chart_payload(raw)
    assert clean is not None
    assert clean is not raw
    assert "evil" not in clean
    assert clean["series"][0] is not raw["series"][0]


@pytest.mark.parametrize(
    "value",
    [math.nan, math.inf, -math.inf, True, False, "12", None, "not-a-number"],
)
def test_sanitize_rejects_non_finite_and_non_number_values(value) -> None:
    raw = {
        "kind": "bar",
        "categories": ["a"],
        "series": [{"name": "s", "values": [value]}],
    }
    assert sanitize_chart_payload(raw) is None


def test_build_chart_rejects_nan_inf_bool_and_string_numbers() -> None:
    assert build_chart("city_rank", [{"目的地": "A", "计划数": math.nan}]) is None
    assert build_chart("city_rank", [{"目的地": "A", "计划数": math.inf}]) is None
    assert build_chart("city_rank", [{"目的地": "A", "计划数": -math.inf}]) is None
    assert build_chart("city_rank", [{"目的地": "A", "计划数": True}]) is None
    assert build_chart("city_rank", [{"目的地": "A", "计划数": "9"}]) is None


def test_pie_rejects_negative_and_all_zero() -> None:
    assert (
        build_chart(
            "traveler_type_distribution",
            [{"类型": "甲", "用户数": -1}, {"类型": "乙", "用户数": 2}],
        )
        is None
    )
    assert (
        build_chart(
            "traveler_type_distribution",
            [{"类型": "甲", "用户数": 0}, {"类型": "乙", "用户数": 0}],
        )
        is None
    )


def test_detail_intents_never_chart() -> None:
    table = [{"id": 1, "city": "X", "email": "a@b.com", "token": "secret"}]
    for intent in ("all_plan_detail", "audit_log", "profile"):
        assert build_chart(intent, table) is None


def test_size_limits_categories_series_points_pie() -> None:
    chart = build_chart("city_rank", _bar_table(80))
    assert chart is not None
    assert len(chart["categories"]) <= MAX_CATEGORIES
    assert chart["truncated"] is True
    assert "部分结果" in chart.get("note", "")

    # Many series via period×destination.
    periods = [f"P{i}" for i in range(12)]
    dests = [f"D{j}" for j in range(5)]
    table = [
        {"周期": p, "目的地": d, "计划数": 1}
        for p in periods
        for d in dests
    ]
    multi = build_chart("city_rank", table)
    assert multi is not None
    assert len(multi["series"]) <= MAX_SERIES


def test_text_control_and_markup_are_neutralized() -> None:
    table = [{"目的地": "A\x00B<script>", "计划数": 2}]
    chart = build_chart("city_rank", table, title="<img src=x onerror=alert(1)>")
    assert chart is not None
    assert "\x00" not in chart["title"]
    assert "<" not in chart["title"] and ">" not in chart["title"]
    assert "<" not in chart["categories"][0]
    # Angle brackets replaced for pure-text safety
    assert "＜" in chart["title"] or "script" in chart["title"].lower() or "img" in chart["title"].lower()


def test_sqlish_title_is_replaced() -> None:
    chart = build_chart(
        "city_rank",
        _bar_table(1),
        title="SELECT * FROM users WHERE password",
    )
    assert chart is not None
    assert "select" not in chart["title"].lower()
    assert "password" not in chart["title"].lower()


def test_pydantic_model_forbids_extra_and_nan() -> None:
    with pytest.raises(ValidationError):
        ChartPayloadModel.model_validate(
            {
                "kind": "bar",
                "categories": ["a"],
                "series": [{"name": "s", "values": [1.0]}],
                "xAxis": {},
            }
        )
    with pytest.raises(ValidationError):
        ChartPayloadModel.model_validate(
            {
                "kind": "bar",
                "categories": ["a"],
                "series": [{"name": "s", "values": [float("nan")]}],
            }
        )
    with pytest.raises(ValidationError):
        ChartPayloadModel.model_validate(
            {
                "kind": "bar",
                "categories": ["a"],
                "series": [{"name": "s", "values": [float("inf")]}],
            }
        )


def test_pydantic_accepts_clean_payload() -> None:
    model = ChartPayloadModel.model_validate(
        {
            "kind": "bar",
            "title": "测试",
            "categories": ["甲", "乙"],
            "series": [{"name": "数量", "values": [1.0, 2.0]}],
            "x_label": "类",
            "y_label": "值",
            "truncated": False,
            "note": "",
        }
    )
    assert model.kind == "bar"
    assert model.series[0].values == [1.0, 2.0]


def test_agent_chat_invalid_chart_does_not_500(monkeypatch) -> None:
    """Outbound boundary: bad chart is dropped; text/table still return 200."""
    from fastapi.testclient import TestClient

    from app.api.main import app
    from app.api.auth import get_current_user
    from app.services.auth_service import AuthenticatedUser

    def fake_user():
        return AuthenticatedUser(
            user_id="u-chart-test",
            username="chart-user",
            email="chart@example.com",
            role="user",
            token_version=1,
        )

    class FakeGraph:
        def run(self, *args, **kwargs):
            return {
                "success": True,
                "intent": "city_rank",
                "agent": "test",
                "tool": "test",
                "table": [{"目的地": "甲", "计划数": 1}],
                "chart": {
                    "title": {"text": "legacy-option"},
                    "tooltip": {"formatter": "function(){}"},
                    "series": [{"type": "bar", "data": [1]}],
                },
                "result": "分析完成：虚构目的地甲。",
                "permission": {"role": "user", "allowed": True, "reason": ""},
                "sensitive": {},
                "extra": {},
            }

    app.dependency_overrides[get_current_user] = fake_user
    monkeypatch.setattr(
        "app.api.routes.agent.get_travel_agent_graph",
        lambda: FakeGraph(),
    )
    try:
        client = TestClient(app)
        response = client.post(
            "/api/agent/chat",
            json={"message": "统计目的地"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["result"] == "分析完成：虚构目的地甲。"
        assert body["table"] == [{"目的地": "甲", "计划数": 1}]
        assert body["chart"] is None
    finally:
        app.dependency_overrides.clear()


def test_agent_chat_missing_chart_compatible(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.api.main import app
    from app.api.auth import get_current_user
    from app.services.auth_service import AuthenticatedUser

    def fake_user():
        return AuthenticatedUser(
            user_id="u-chart-test-2",
            username="chart-user-2",
            email="chart2@example.com",
            role="user",
            token_version=1,
        )

    class FakeGraph:
        def run(self, *args, **kwargs):
            return {
                "success": True,
                "intent": "assistant_chat",
                "agent": "test",
                "tool": "test",
                "table": [],
                "result": "纯文本回答。",
                "permission": {"role": "user", "allowed": True, "reason": ""},
                "sensitive": {},
                "extra": {},
            }

    app.dependency_overrides[get_current_user] = fake_user
    monkeypatch.setattr(
        "app.api.routes.agent.get_travel_agent_graph",
        lambda: FakeGraph(),
    )
    try:
        client = TestClient(app)
        response = client.post("/api/agent/chat", json={"message": "你好"})
        assert response.status_code == 200
        body = response.json()
        assert body["result"] == "纯文本回答。"
        assert body.get("chart") is None
        assert body["table"] == []
    finally:
        app.dependency_overrides.clear()
