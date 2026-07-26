"""Role-scoped multi-agent workflow for travel data analysis."""

from __future__ import annotations

import os
from typing import Any, TypedDict

from ...services.travel_plan_data_service import get_travel_plan_data_service
from ...tools.analytics_context_tool import (
    AnalysisPeriod,
    get_analysis_metadata,
    get_data_status,
    get_role_capabilities,
    parse_analysis_period,
)
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
    client_ip: str | None
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
    period: AnalysisPeriod
    extra: dict
    __final__: dict


def _is_email_report_request(message: str) -> bool:
    text = message or ""
    return any(word in text for word in ("发送", "发给", "发到", "寄给", "邮件")) and any(
        word in text for word in ("报告", "画像", "分析结果", "邮箱")
    )


def _is_analysis_request(message: str) -> bool:
    text = (message or "").lower()
    return any(
        word in text
        for word in (
            "统计", "查询", "分析", "画像", "兴趣", "偏好", "相似", "推荐",
            "预测", "趋势", "同比", "环比", "预算", "消费", "花费", "热门",
            "目的地", "城市", "行程", "计划", "明细", "列表", "分布", "图表",
            "数据", "季度", "本月", "去年", "审计", "audit", "forecast",
        )
    )


def _security_node(state: AgentState) -> dict:
    sensitive = check_sensitive_text(state["message"])
    email_request = _is_email_report_request(state["message"])
    non_email_hits = [hit for hit in sensitive.get("hits", []) if hit not in {"邮箱", "contact_email"}]
    if (non_email_hits or (sensitive.get("hits") and not email_request)):
        return {
            "sensitive": sensitive,
            "allowed": False,
            "agent": "SecurityAgent",
            "tool": "sensitive_filter_tool",
            "permission_reason": "请求包含不允许进入分析链路的敏感字段。",
            "result": "智能分析不会查询或返回联系人、手机号、邮箱或认证秘密。",
        }
    if sensitive.get("has_dangerous_sql"):
        return {
            "sensitive": sensitive,
            "allowed": False,
            "agent": "SecurityAgent",
            "tool": "sensitive_filter_tool",
            "permission_reason": "请求包含危险 SQL 操作。",
            "result": "分析接口只执行服务端白名单中的只读查询。",
        }
    return {"sensitive": sensitive, "allowed": True}


def _router_node(state: AgentState) -> dict:
    message = state["message"]
    if _is_email_report_request(message):
        return {"intent": "email_report", "agent": "EmailAgent"}
    if any(word in message for word in ("分析文件", "上传文件", "文档分析")):
        return {"intent": "file_analysis", "agent": "FileAnalysisAgent"}
    if not _is_analysis_request(message):
        return {"intent": "assistant_chat", "agent": "AssistantAgent"}
    intent = classify_sql_intent(message)
    agent = {
        "profile": "ProfileAgent",
        "recommendation": "RecommendationAgent",
        "prediction": "PredictAgent",
        "data_quality": "QualityAgent",
        "audit_log": "AuditAgent",
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


def _resolve_email_recipient(state: AgentState) -> str:
    explicit = (state.get("email") or "").strip()
    return explicit or os.getenv("SMTP_FROM") or os.getenv("SMTP_USERNAME") or ""


def _assistant_response(state: AgentState) -> dict:
    capabilities = get_role_capabilities(state["role"])
    status = get_data_status(state["user_id"], state["role"])
    prompts = "、".join(capabilities["quick_prompts"][:3])
    result = (
        f"当前角色的数据范围是“{capabilities['scope_label']}”。"
        f"可见 {status['visible_plans']} 条计划、{status['destinations']} 个目的地。"
        f"你可以继续问：{prompts}。"
    )
    if status["warnings"]:
        result += f" 数据提示：{status['warnings'][0]}"
    return {
        "table": [],
        "tool": "capability_tool",
        "result": result,
        "extra": {"capabilities": capabilities, "data_status": status},
        "period": parse_analysis_period(state["message"]),
    }


def _recommendation_payload(state: AgentState, limit: int = 5) -> dict:
    if normalize_role(state.get("role")) == "user":
        plan = build_sql_plan("统计全部历史目的地", state["user_id"], "user")
        rows = run_sql_plan(plan, "user")[:limit]
        recommendations = [
            {
                "city": row.get("目的地"),
                "count": row.get("计划数"),
                "avg_budget": row.get("平均预算"),
            }
            for row in rows
        ]
        return {
            "recommendations": recommendations,
            "reason": "仅根据当前账号自己的历史计划生成，不读取其他用户或全站汇总。",
        }
    return recommend_by_profile(state["user_id"], limit=limit)


def _recommendation_response(state: AgentState) -> dict:
    payload = _recommendation_payload(state)
    table = [
        {
            "目的地": item.get("city"),
            "计划数": item.get("count"),
            "平均预算": item.get("avg_budget"),
        }
        for item in payload.get("recommendations", [])
    ]
    return {
        "table": table,
        "extra": payload,
        "tool": "personal_history_tool" if normalize_role(state.get("role")) == "user" else "profile_tool",
        "period": AnalysisPeriod("all", "全部历史"),
    }


def _email_response(state: AgentState) -> dict:
    payload = _recommendation_payload(state, limit=5)
    lines = ["灵途用户旅行画像报告", "", payload.get("reason", ""), "", "推荐目的地："]
    for item in payload.get("recommendations", []):
        lines.append(
            f"- {item.get('city')}: 匿名聚合计划数 {item.get('count')}, "
            f"平均预算 {item.get('avg_budget')} 元"
        )
    recipient = _resolve_email_recipient(state)
    if not recipient:
        email_result = {"sent": False, "dry_run": True, "message": "请先填写收件邮箱。", "to": ""}
    else:
        email_result = send_email(
            recipient,
            "灵途旅行画像与推荐报告",
            "\n".join(lines),
            user_id=state.get("user_id"),
            client_ip=state.get("client_ip"),
        )
    return {
        "table": [{"邮件状态": email_result.get("message", ""), "收件人": email_result.get("to", recipient)}],
        "extra": {"email": email_result},
        "tool": "send_email_tool",
        "period": AnalysisPeriod("all", "全部历史"),
    }


def _dispatch_node(state: AgentState) -> dict:
    intent = state["intent"]
    if intent == "assistant_chat":
        return _assistant_response(state)
    if intent == "file_analysis":
        return {
            "table": [],
            "tool": "file_analysis_tool",
            "result": "请使用文件分析入口上传 TXT、PDF、DOCX 或 XLSX 文档。",
            "period": parse_analysis_period(state["message"]),
        }
    if intent == "recommendation":
        return _recommendation_response(state)
    if intent == "prediction":
        return {
            "table": predict_next_month_hot_destinations(
                user_id=state["user_id"], role=state["role"]
            ),
            "tool": "predict_tool",
            "period": AnalysisPeriod("all", "全部历史"),
        }
    if intent == "email_report":
        return _email_response(state)

    # Only deterministic allow-list plans reach the database.  LLM-generated
    # SQL is deliberately not executed in this authorization boundary.
    plan = build_sql_plan(state["message"], state["user_id"], state["role"])
    resolved_period = parse_analysis_period(state["message"])
    if plan.intent in {"profile", "traveler_type_distribution", "audit_log"}:
        resolved_period = AnalysisPeriod("all", "全部历史")

    table = run_sql_plan(plan, state["role"])
    extra: dict[str, Any] = {"title": plan.title}
    if intent == "data_quality":
        extra["data_status"] = get_data_status(state["user_id"], state["role"])
    return {
        "table": table,
        "intent": plan.intent,
        "agent": plan.agent,
        "tool": "sql_agent_tool",
        "sql": " ".join(plan.sql.split()),
        "period": resolved_period,
        "extra": extra,
    }


def _quality_node(state: AgentState) -> dict:
    period = state.get("period") or parse_analysis_period(state.get("message", ""))
    table = state.get("table", [])
    intent = state.get("intent", "")
    if intent == "audit_log":
        status = get_data_status(state.get("user_id", ""), state.get("role", "admin"))
        analysis = {
            "scope": status["scope"],
            "scope_label": "管理员可见的智能分析审计记录",
            "period": AnalysisPeriod("all", "最近审计记录").to_dict(),
            "sample_size": len(table),
            "row_count": len(table),
            "data_quality": {},
            "sufficient_for": {"facts": bool(table)},
            "warnings": [],
        }
    else:
        analysis = get_analysis_metadata(
            state.get("user_id", ""),
            state.get("role", "user"),
            period,
            table,
        )
        if intent == "traveler_type_distribution":
            analysis["sample_size"] = sum(
                int(row.get("用户数") or row.get("count") or 0)
                for row in table
            )
    extra = dict(state.get("extra") or {})
    extra["analysis"] = analysis
    return {"extra": extra, "period": period}


def _chart_node(state: AgentState) -> dict:
    title = (state.get("extra") or {}).get("title") or {
        "recommendation": "画像推荐目的地",
        "prediction": "下月热门目的地预测",
        "data_quality": "旅行计划数据来源",
    }.get(state.get("intent"), "旅行计划数据分析")
    return {"chart": build_chart(state.get("intent", ""), state.get("table", []), title)}


def _report_node(state: AgentState) -> dict:
    extra = state.get("extra") or {}
    if state.get("intent") in {"assistant_chat", "file_analysis"}:
        return {"result": state.get("result", "")}
    if state.get("intent") == "email_report":
        return {"result": (extra.get("email") or {}).get("message", "邮件工具已执行。")}
    return {
        "result": summarize_table(
            state.get("intent", ""),
            state.get("table", []),
            extra.get("reason", ""),
            extra.get("analysis") or {},
        )
    }


def _reject_node(_state: AgentState) -> dict:
    return {"allowed": False}


def _finish_node(state: AgentState) -> dict:
    allowed = bool(state.get("allowed", True))
    sensitive = state.get("sensitive", {})
    sensitive_hit = bool(sensitive.get("hits") or sensitive.get("has_dangerous_sql"))
    logged_message = "[已脱敏的敏感请求]" if sensitive_hit else state.get("message", "")
    try:
        service = get_travel_plan_data_service()
        service.log_audit(
            user_id=state.get("user_id", ""),
            user_role=state.get("role", "guest"),
            message=logged_message,
            routed_agent=state.get("agent", ""),
            tool_name=state.get("tool", ""),
            permission_allowed=allowed,
            sensitive_hit=sensitive_hit,
            audit_detail={
                "intent": state.get("intent", ""),
                "scope": ((state.get("extra") or {}).get("analysis") or {}).get("scope", ""),
                "permission_reason": state.get("permission_reason", ""),
                "sensitive": sensitive,
            },
        )
        service.log_query(
            user_id=state.get("user_id", ""),
            user_role=state.get("role", "guest"),
            question=logged_message,
            intent=state.get("intent", ""),
            sql_text=state.get("sql", ""),
            result_summary=state.get("result", ""),
        )
    except Exception as exc:
        print(f"[agent_graph] audit logging failed: {type(exc).__name__}")

    permission = {
        "role": state.get("role", "guest"),
        "allowed": allowed,
        "reason": state.get("permission_reason", ""),
    }
    payload = {
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
    return {"__final__": payload}


def _after_security(state: AgentState) -> str:
    return "reject" if not state.get("allowed", True) else "safe"


def _after_role(state: AgentState) -> str:
    return "reject" if not state.get("allowed", True) else "safe"


class TravelAgentGraph:
    """Coordinator with optional LangGraph execution and deterministic fallback."""

    def __init__(self) -> None:
        self.data_service = get_travel_plan_data_service()
        self.graph_available, self._compiled_graph = self._try_build_langgraph()

    def _try_build_langgraph(self):
        try:
            from langgraph.graph import END, StateGraph

            graph = StateGraph(AgentState)
            graph.add_node("security", _security_node)
            graph.add_node("router", _router_node)
            graph.add_node("role_check", _role_node)
            graph.add_node("dispatch", _dispatch_node)
            graph.add_node("quality", _quality_node)
            graph.add_node("chart", _chart_node)
            graph.add_node("report", _report_node)
            graph.add_node("reject", _reject_node)
            graph.add_node("finish", _finish_node)
            graph.set_entry_point("security")
            graph.add_conditional_edges("security", _after_security, {"safe": "router", "reject": "reject"})
            graph.add_edge("router", "role_check")
            graph.add_conditional_edges("role_check", _after_role, {"safe": "dispatch", "reject": "reject"})
            graph.add_edge("dispatch", "quality")
            graph.add_edge("quality", "chart")
            graph.add_edge("chart", "report")
            graph.add_edge("report", "finish")
            graph.add_edge("reject", "finish")
            graph.add_edge("finish", END)
            return True, graph.compile()
        except Exception as exc:
            print(f"[agent_graph] LangGraph unavailable, using sequential fallback: {type(exc).__name__}")
            return False, None

    def run(
        self,
        user_id: str,
        role: str,
        message: str,
        email: str | None = None,
        client_ip: str | None = None,
    ) -> dict:
        initial: AgentState = {
            "user_id": user_id or "",
            "role": normalize_role(role),
            "message": message,
            "email": email,
            "client_ip": client_ip,
            "allowed": True,
        }
        if self._compiled_graph is not None:
            result = self._compiled_graph.invoke(initial)
            if isinstance(result.get("__final__"), dict):
                return result["__final__"]
        return self._run_sequential(initial)

    def _run_sequential(self, state: AgentState) -> dict:
        state = {**state, **_security_node(state)}
        if not state.get("allowed", True):
            return _finish_node(state)["__final__"]
        state = {**state, **_router_node(state)}
        state = {**state, **_role_node(state)}
        if not state.get("allowed", True):
            return _finish_node(state)["__final__"]
        state = {**state, **_dispatch_node(state)}
        state = {**state, **_quality_node(state)}
        state = {**state, **_chart_node(state)}
        state = {**state, **_report_node(state)}
        return _finish_node(state)["__final__"]


_travel_agent_graph: TravelAgentGraph | None = None


def get_travel_agent_graph() -> TravelAgentGraph:
    global _travel_agent_graph
    if _travel_agent_graph is None:
        _travel_agent_graph = TravelAgentGraph()
    return _travel_agent_graph
