"""Multi-agent workflow for travel profile analysis.

Uses LangGraph StateGraph for real graph orchestration when langgraph is
installed; falls back to a deterministic sequential runner otherwise.
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from ...services.travel_plan_data_service import get_travel_plan_data_service
from ...tools.chart_tool import build_chart
from ...tools.permission_tool import check_permission, normalize_role
from ...tools.predict_tool import predict_next_month_hot_destinations
from ...tools.profile_tool import recommend_by_profile
from ...tools.report_tool import summarize_table
from ...tools.send_email_tool import send_email
from ...tools.sensitive_filter_tool import check_sensitive_text
from ...tools.sql_agent_tool import build_sql_plan, classify_sql_intent, run_sql_plan


class AgentState(TypedDict, total=False):
    user_id: str
    role: str
    message: str
    email: str | None
    intent: str
    agent: str
    tool: str
    allowed: bool
    permission_reason: str
    sensitive: dict
    table: list[dict]
    chart: dict | None
    result: str
    sql: str
    extra: dict
    __final__: dict  # serialised output payload injected by _finish_node


# ---------------------------------------------------------------------------
# Node functions – each returns a partial AgentState dict (LangGraph style)
# ---------------------------------------------------------------------------

def _security_node(state: AgentState) -> dict:
    sensitive = check_sensitive_text(state["message"])
    if sensitive["hits"] and not _is_email_report_request(state["message"]):
        return {
            "sensitive": sensitive,
            "allowed": False,
            "agent": "SecurityAgent",
            "tool": "sensitive_filter_tool",
            "permission_reason": sensitive["message"],
            "result": "请求包含手机号、邮箱、联系人等敏感字段，系统已拒绝查询。",
        }
    if sensitive["has_dangerous_sql"]:
        return {
            "sensitive": sensitive,
            "allowed": False,
            "agent": "SecurityAgent",
            "tool": "sensitive_filter_tool",
            "permission_reason": sensitive["message"],
            "result": "请求包含危险 SQL 操作，系统已拒绝执行。",
        }
    return {"sensitive": sensitive, "allowed": True}


def _is_email_report_request(message: str) -> bool:
    text = message or ""
    has_email_action = any(word in text for word in ("邮件", "发送", "发给", "发到", "寄", "寄给"))
    has_report_context = any(
        word in text for word in ("报告", "画像", "分析结果", "邮箱", "自己", "我自己")
    )
    return has_email_action and has_report_context


def _is_analysis_request(message: str) -> bool:
    text = message or ""
    return any(
        word in text
        for word in (
            "统计",
            "查询",
            "分析",
            "画像",
            "兴趣",
            "偏好",
            "相似",
            "推荐",
            "预测",
            "趋势",
            "预算",
            "消费",
            "花费",
            "热门",
            "目的地",
            "城市",
            "行程",
            "计划",
            "明细",
            "详情",
            "全部",
            "列表",
            "所有",
            "分类",
            "分布",
            "图表",
            "表格",
            "数据",
        )
    )


def _resolve_email_recipient(state: AgentState) -> str:
    explicit_email = (state.get("email") or "").strip()
    if explicit_email:
        return explicit_email
    return (
        os.getenv("SMTP_FROM")
        or os.getenv("SMTP_USERNAME")
        or os.getenv("EMAIL_FROM")
        or os.getenv("EMAIL_USER")
        or "demo@example.com"
    )


def _router_node(state: AgentState) -> dict:
    message = state["message"]
    if _is_email_report_request(message):
        intent = "email_report"
        agent = "EmailAgent"
    elif any(word in message for word in ("分析文件", "上传", "文件")):
        intent = "file_analysis"
        agent = "FileAnalysisAgent"
    elif not _is_analysis_request(message):
        intent = "assistant_chat"
        agent = "AssistantAgent"
    else:
        intent = classify_sql_intent(message)
        agent = {
            "profile": "ProfileAgent",
            "recommendation": "RecommendationAgent",
            "prediction": "PredictAgent",
        }.get(intent, "SQLAgent")
    return {"intent": intent, "agent": agent}


def _role_node(state: AgentState) -> dict:
    permission = check_permission(state["role"], state["intent"], state["message"])
    if not permission["allowed"]:
        return {
            "allowed": False,
            "permission_reason": permission["reason"],
            "agent": "RoleAgent",
            "tool": "permission_tool",
            "result": permission["reason"],
        }
    return {"allowed": True, "permission_reason": ""}


def _dispatch_node(state: AgentState) -> dict:
    intent = state["intent"]
    if intent == "assistant_chat":
        return {
            "table": [],
            "tool": "router_tool",
            "result": (
                "我能听懂自然语言问题，但我会先判断它是不是旅行数据分析任务。"
                "你可以问我旅行画像、热门目的地、平均预算、趋势预测、相似用户推荐，"
                "也可以让我把画像报告发送到邮箱。"
            ),
        }

    if intent == "recommendation":
        payload = recommend_by_profile(state["user_id"])
        table = [
            {"目的地": item["city"], "相似用户计划数": item["count"], "平均预算": item["avg_budget"]}
            for item in payload["recommendations"]
        ]
        return {"table": table, "extra": payload, "tool": "profile_tool"}

    if intent == "prediction":
        return {"table": predict_next_month_hot_destinations(), "tool": "predict_tool"}

    if intent == "email_report":
        profile_payload = recommend_by_profile(state["user_id"], limit=5)
        lines = [
            "灵途用户旅行画像报告",
            "",
            profile_payload["reason"],
            "",
            "推荐目的地:",
        ]
        for item in profile_payload["recommendations"]:
            lines.append(
                f"- {item['city']}: 相似用户计划数 {item['count']}, 平均预算 {item['avg_budget']} 元"
            )
        recipient = _resolve_email_recipient(state)
        email_result = send_email(
            recipient,
            "灵途旅行画像与推荐报告",
            "\n".join(lines),
        )
        return {
            "table": [
                {
                    "邮件状态": email_result["message"],
                    "收件人": email_result.get("to", recipient),
                }
            ],
            "extra": {"email": email_result},
            "tool": "send_email_tool",
        }

    # ---- LLM-powered SQL path (try first, fall back to rule-based) ----
    table, plan_intent, plan_agent, plan_sql, plan_title, plan_tool = _execute_sql_intent(state)
    return {
        "table": table,
        "intent": plan_intent,
        "agent": plan_agent,
        "tool": plan_tool,
        "sql": plan_sql,
        "extra": {"title": plan_title},
    }


def _execute_sql_intent(state: AgentState) -> tuple:
    """Try LLM-generated SQL first, fall back to rule-based templates."""
    stable_intent = classify_sql_intent(state["message"])
    if stable_intent in {
        "city_rank",
        "avg_budget",
        "budget_trend",
        "profile",
        "traveler_type_distribution",
        "all_plan_detail",
    }:
        plan = build_sql_plan(state["message"], state["user_id"], state["role"])
        table = run_sql_plan(plan, state["role"])
        return (
            table,
            plan.intent,
            plan.agent,
            " ".join(plan.sql.split()),
            plan.title,
            "sql_agent_tool",
        )

    try:
        from ...tools.llm_sql_agent_tool import build_sql_plan_with_llm, run_llm_sql_plan

        plan = build_sql_plan_with_llm(state["message"], state["user_id"], state["role"])
        if plan is not None:
            table = run_llm_sql_plan(plan, state["role"])
            return (
                table,
                plan.intent,
                plan.agent,
                " ".join(plan.sql.split()),
                plan.title,
                "llm_sql_agent_tool",
            )
    except Exception as exc:
        print(f"[dispatch] LLM SQL failed, falling back to rule-based: {exc}")

    # fallback to rule-based
    plan = build_sql_plan(state["message"], state["user_id"], state["role"])
    table = run_sql_plan(plan, state["role"])
    return (
        table,
        plan.intent,
        plan.agent,
        " ".join(plan.sql.split()),
        plan.title,
        "sql_agent_tool",
    )


def _chart_node(state: AgentState) -> dict:
    title = (state.get("extra") or {}).get("title") or {
        "recommendation": "相似用户目的地推荐",
        "prediction": "下月热门目的地预测",
        "email_report": "邮件发送结果",
        "file_analysis": "文件分析结果",
    }.get(state.get("intent"), "旅行计划数据分析")
    return {"chart": build_chart(state.get("intent", ""), state.get("table", []), title)}


def _report_node(state: AgentState) -> dict:
    extra = state.get("extra") or {}
    reason = extra.get("reason", "")
    if state.get("intent") == "assistant_chat":
        return {"result": state.get("result", "我可以帮你做旅行数据分析。")}
    if state.get("intent") == "email_report":
        email_payload = extra.get("email", {})
        return {"result": email_payload.get("message", "邮件工具已执行。")}
    return {"result": summarize_table(state.get("intent", ""), state.get("table", []), reason)}


def _reject_node(state: AgentState) -> dict:
    """Terminal node when security or role check fails."""
    return {"allowed": False}


def _finish_node(state: AgentState) -> dict:
    """Persist audit trails and return the final payload (via __final__ key)."""
    data_service = get_travel_plan_data_service()
    allowed = bool(state.get("allowed", True))
    sensitive = state.get("sensitive", {})
    try:
        data_service.log_audit(
            user_id=state.get("user_id", ""),
            user_role=state.get("role", "guest"),
            message=state.get("message", ""),
            routed_agent=state.get("agent", ""),
            tool_name=state.get("tool", ""),
            permission_allowed=allowed,
            sensitive_hit=bool(sensitive.get("hits") or sensitive.get("has_dangerous_sql")),
            audit_detail={
                "intent": state.get("intent", ""),
                "graph_available": True,
                "permission_reason": state.get("permission_reason", ""),
                "sensitive": sensitive,
            },
        )
        data_service.log_query(
            user_id=state.get("user_id", ""),
            user_role=state.get("role", "guest"),
            question=state.get("message", ""),
            intent=state.get("intent", ""),
            sql_text=state.get("sql", ""),
            result_summary=state.get("result", ""),
        )
    except Exception as exc:
        print(f"[agent_graph] audit logging failed: {exc}")

    permission = {
        "role": state.get("role", "guest"),
        "allowed": allowed,
        "reason": state.get("permission_reason", ""),
    }
    final_payload = {
        "success": allowed,
        "intent": state.get("intent", ""),
        "agent": state.get("agent", ""),
        "tool": state.get("tool", ""),
        "table": state.get("table", []),
        "chart": state.get("chart"),
        "result": state.get("result", permission["reason"]),
        "permission": permission,
        "sensitive": sensitive,
        "extra": state.get("extra", {}),
    }
    state["__final__"] = final_payload  # type: ignore
    return state


# ---------------------------------------------------------------------------
# Conditional edge guards
# ---------------------------------------------------------------------------

def _after_security(state: AgentState) -> str:
    return "reject" if not state.get("allowed", True) else "safe"


def _after_role(state: AgentState) -> str:
    return "reject" if not state.get("allowed", True) else "safe"


# ---------------------------------------------------------------------------
# Orchestrator class
# ---------------------------------------------------------------------------

class TravelAgentGraph:
    """Coordinator for Router/Security/Role/SQL/Chart/Profile/Report agents.

    Uses a real LangGraph StateGraph when langgraph is installed; otherwise
    falls back to a deterministic sequential pipeline.
    """

    def __init__(self) -> None:
        self.data_service = get_travel_plan_data_service()
        self.graph_available, self._compiled_graph = self._try_build_langgraph()

    # ── LangGraph builder ──────────────────────────────────────────────

    def _try_build_langgraph(self):
        """Build and compile a real StateGraph.  Returns (True, graph) on success
        or (False, None) when langgraph is unavailable."""
        try:
            from langgraph.graph import StateGraph, END  # noqa: F811

            graph = StateGraph(AgentState)

            # register nodes
            graph.add_node("security", _security_node)
            graph.add_node("router", _router_node)
            graph.add_node("role_check", _role_node)
            graph.add_node("dispatch", _dispatch_node)
            graph.add_node("chart", _chart_node)
            graph.add_node("report", _report_node)
            graph.add_node("reject", _reject_node)
            graph.add_node("finish", _finish_node)

            # wiring
            graph.set_entry_point("security")
            graph.add_conditional_edges(
                "security",
                _after_security,
                {"safe": "router", "reject": "reject"},
            )
            graph.add_edge("router", "role_check")
            graph.add_conditional_edges(
                "role_check",
                _after_role,
                {"safe": "dispatch", "reject": "reject"},
            )
            graph.add_edge("dispatch", "chart")
            graph.add_edge("chart", "report")
            graph.add_edge("report", "finish")
            graph.add_edge("reject", "finish")
            graph.add_edge("finish", END)

            compiled = graph.compile()
            print("[agent_graph] LangGraph StateGraph compiled successfully")
            return True, compiled
        except Exception as exc:
            print(f"[agent_graph] ⚠️ LangGraph unavailable, using sequential fallback: {exc}")
            return False, None

    # ── Public API ─────────────────────────────────────────────────────

    def run(self, user_id: str, role: str, message: str, email: str | None = None) -> dict:
        """Execute the agent pipeline.

        Uses the compiled LangGraph when available; otherwise walks the
        nodes sequentially to keep the API identical.
        """
        initial: AgentState = {
            "user_id": user_id or "",
            "role": normalize_role(role),
            "message": message,
            "email": email,
            "allowed": True,
        }

        if self._compiled_graph is not None:
            return self._run_with_langgraph(initial)

        return self._run_sequential(initial)

    def _run_with_langgraph(self, state: AgentState) -> dict:
        result = self._compiled_graph.invoke(state)  # type: ignore[union-attr]
        final = result.get("__final__")
        if isinstance(final, dict):
            return final
        # defensive: reconstruct from raw state
        return _finish_node(result)

    def _run_sequential(self, state: AgentState) -> dict:
        """Deterministic fallback — merge each node's partial update into state."""
        state = {**state, **_security_node(state)}  # type: ignore[arg-type,operator]
        if not state.get("allowed", True):
            return _finish_node(state)  # type: ignore[arg-type]
        state = {**state, **_router_node(state)}
        state = {**state, **_role_node(state)}
        if not state.get("allowed", True):
            return _finish_node(state)  # type: ignore[arg-type]
        state = {**state, **_dispatch_node(state)}
        state = {**state, **_chart_node(state)}
        state = {**state, **_report_node(state)}
        return _finish_node(state)  # type: ignore[arg-type]


_travel_agent_graph: TravelAgentGraph | None = None


def get_travel_agent_graph() -> TravelAgentGraph:
    global _travel_agent_graph
    if _travel_agent_graph is None:
        _travel_agent_graph = TravelAgentGraph()
    return _travel_agent_graph
