"""Safe, role-scoped SQL plans for supported travel analytics intents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..services.database_service import fetch_all
from .analytics_context_tool import AnalysisPeriod, parse_analysis_period, period_sql_filter
from .permission_tool import normalize_role, scope_user_filter
from .sensitive_filter_tool import mask_sensitive_row


@dataclass
class SQLPlan:
    intent: str
    sql: str
    params: dict
    agent: str = "SQLAgent"
    title: str = ""
    period: dict[str, Any] = field(default_factory=dict)
    scope: str = ""


def classify_sql_intent(message: str) -> str:
    text = (message or "").lower()
    if any(word in text for word in ("审计", "查询日志", "分析日志", "audit")):
        return "audit_log"
    if any(word in text for word in ("数据质量", "数据完整", "数据来源", "多少条数据", "数据量")):
        return "data_quality"
    if any(word in text for word in ("预测", "下个月", "下月", "未来", "forecast")):
        return "prediction"
    if any(word in text for word in ("用户类型分布", "旅行者类型分布", "人群分布", "群体画像")):
        return "traveler_type_distribution"
    if any(word in text for word in ("画像", "兴趣", "偏好", "profile")):
        return "profile"
    if any(word in text for word in ("相似", "推荐", "适合我")):
        return "recommendation"
    if any(word in text for word in ("明细", "详情", "全部计划", "计划列表", "逐条")):
        return "all_plan_detail"
    if any(word in text for word in ("趋势", "每月", "月度", "月份变化")):
        return "budget_trend"
    if any(word in text for word in ("平均预算", "人均预算", "预算", "消费", "花费", "avg")):
        return "avg_budget"
    return "city_rank"


def _scoped_where(
    role: str,
    user_id: str,
    period: AnalysisPeriod,
    *,
    column: str = "user_id",
    date_column: str = "start_date",
) -> tuple[str, dict[str, Any]]:
    clauses = ["1=1"]
    suffix, params = scope_user_filter(role, user_id, column)
    if suffix:
        clauses.append(suffix.replace(" AND ", "", 1))
    time_clause, time_params = period_sql_filter(period, date_column)
    if time_clause:
        clauses.append(time_clause)
        params.update(time_params)
    return "WHERE " + " AND ".join(clauses), params


def _comparison_where(
    role: str,
    user_id: str,
    period: AnalysisPeriod,
) -> tuple[str, dict[str, Any]] | None:
    if not (
        period.start
        and period.end_exclusive
        and period.comparison_start
        and period.comparison_end_exclusive
    ):
        return None
    clauses = [
        "((start_date >= :period_start AND start_date < :period_end) "
        "OR (start_date >= :comparison_start AND start_date < :comparison_end))"
    ]
    suffix, params = scope_user_filter(role, user_id)
    if suffix:
        clauses.insert(0, suffix.replace(" AND ", "", 1))
    params.update(
        {
            "period_start": period.start.isoformat(),
            "period_end": period.end_exclusive.isoformat(),
            "comparison_start": period.comparison_start.isoformat(),
            "comparison_end": period.comparison_end_exclusive.isoformat(),
        }
    )
    return "WHERE " + " AND ".join(clauses), params


def _scope_name(role: str) -> str:
    normalized = normalize_role(role)
    if normalized == "user":
        return "personal"
    if normalized == "manager":
        return "global_aggregate"
    return "global"


def build_sql_plan(message: str, user_id: str, role: str) -> SQLPlan:
    intent = classify_sql_intent(message)
    normalized = normalize_role(role)
    period = parse_analysis_period(message)
    where, params = _scoped_where(normalized, user_id, period)
    common = {
        "period": period.to_dict(),
        "scope": _scope_name(normalized),
    }

    if intent == "profile":
        return SQLPlan(
            intent=intent,
            title="我的旅行画像",
            sql=(
                "SELECT plan_count AS 计划数, top_tags AS 兴趣标签, "
                "fav_cities AS 常去城市, avg_budget AS 平均预算, "
                "avg_days AS 平均天数, traveler_type AS 旅行者类型 "
                "FROM user_profiles WHERE user_id = :user_id"
            ),
            params={"user_id": user_id},
            agent="ProfileAgent",
            **common,
        )

    if intent == "audit_log":
        return SQLPlan(
            intent=intent,
            title="最近智能分析审计",
            sql=(
                "SELECT created_at AS 时间, user_role AS 角色, agent AS 智能体, "
                "tool AS 工具, allowed AS 是否允许, sensitive_hit AS 敏感命中 "
                "FROM audit_logs ORDER BY created_at DESC LIMIT 50"
            ),
            params={},
            agent="AuditAgent",
            **common,
        )

    if intent == "data_quality":
        return SQLPlan(
            intent=intent,
            title="旅行计划数据来源",
            sql=(
                "SELECT source AS 数据来源, COUNT(*) AS 计划数, "
                "COUNT(DISTINCT destination) AS 目的地数 "
                f"FROM travel_plans {where} GROUP BY source ORDER BY 计划数 DESC"
            ),
            params=params,
            agent="QualityAgent",
            **common,
        )

    if intent == "traveler_type_distribution":
        return SQLPlan(
            intent=intent,
            title="旅行者类型分布",
            sql=(
                "SELECT traveler_type AS 旅行者类型, COUNT(*) AS 用户数 "
                "FROM user_profiles GROUP BY traveler_type ORDER BY 用户数 DESC"
            ),
            params={},
            **common,
        )

    if intent == "all_plan_detail":
        return SQLPlan(
            intent=intent,
            title="旅行计划明细",
            sql=(
                "SELECT plan_no AS 编号, destination AS 目的地, start_date AS 出发日期, "
                "travel_days AS 天数, budget AS 预算, transportation AS 交通, "
                f"accommodation AS 住宿, source AS 数据来源 FROM travel_plans {where} "
                "ORDER BY created_at DESC LIMIT 50"
            ),
            params=params,
            **common,
        )

    comparison = _comparison_where(normalized, user_id, period)
    if intent in {"city_rank", "avg_budget"} and comparison:
        comparison_where, comparison_params = comparison
        period_label = period.label.replace("'", "")
        comparison_label = (period.comparison_label or "去年同期").replace("'", "")
        metric = (
            "ROUND(AVG(budget), 0) AS 平均预算, COUNT(*) AS 计划数"
            if intent == "avg_budget"
            else "COUNT(*) AS 计划数, ROUND(AVG(budget), 0) AS 平均预算"
        )
        return SQLPlan(
            intent=intent,
            title=f"{period_label}与{comparison_label}对比",
            sql=(
                f"SELECT CASE WHEN start_date >= :period_start AND start_date < :period_end "
                f"THEN '{period_label}' ELSE '{comparison_label}' END AS 周期, "
                f"destination AS 目的地, {metric} FROM travel_plans {comparison_where} "
                "GROUP BY 周期, destination ORDER BY 周期, 计划数 DESC LIMIT 100"
            ),
            params=comparison_params,
            **common,
        )

    if intent == "avg_budget":
        return SQLPlan(
            intent=intent,
            title=f"{period.label}各目的地平均预算",
            sql=(
                "SELECT destination AS 目的地, ROUND(AVG(budget), 0) AS 平均预算, "
                f"COUNT(*) AS 计划数 FROM travel_plans {where} "
                "GROUP BY destination ORDER BY 平均预算 DESC LIMIT 15"
            ),
            params=params,
            **common,
        )

    if intent == "budget_trend":
        return SQLPlan(
            intent=intent,
            title=f"{period.label}月度预算趋势",
            sql=(
                "SELECT substr(start_date, 1, 7) AS 月份, "
                "ROUND(AVG(budget), 0) AS 平均预算, COUNT(*) AS 计划数 "
                f"FROM travel_plans {where} GROUP BY substr(start_date, 1, 7) "
                "ORDER BY 月份 LIMIT 36"
            ),
            params=params,
            **common,
        )

    return SQLPlan(
        intent="city_rank",
        title=f"{period.label}热门目的地",
        sql=(
            "SELECT destination AS 目的地, COUNT(*) AS 计划数, "
            f"ROUND(AVG(budget), 0) AS 平均预算 FROM travel_plans {where} "
            "GROUP BY destination ORDER BY 计划数 DESC LIMIT 15"
        ),
        params=params,
        **common,
    )


_DANGEROUS_SQL = re.compile(
    r"\b(drop|delete|update|insert|alter|truncate|create|grant|revoke|copy|execute|attach|pragma)\b",
    re.IGNORECASE,
)
_ALLOWED_TABLES = {"travel_plans", "user_profiles", "audit_logs", "query_logs"}


def _validate_read_plan(plan: SQLPlan, role: str) -> None:
    sql = plan.sql.strip()
    normalized = normalize_role(role)
    if not re.match(r"^select\b", sql, re.IGNORECASE) or ";" in sql.rstrip(";"):
        raise PermissionError("分析工具只允许执行单条 SELECT 查询。")
    if _DANGEROUS_SQL.search(sql):
        raise PermissionError("查询包含非只读 SQL 操作。")
    tables = {
        match.lower()
        for match in re.findall(r"\b(?:from|join)\s+([a-zA-Z_][\w]*)", sql, re.IGNORECASE)
    }
    if not tables or not tables.issubset(_ALLOWED_TABLES):
        raise PermissionError("查询访问了分析白名单之外的数据表。")
    if normalized == "user" and "user_id = :user_id" not in sql.lower():
        raise PermissionError("普通用户查询缺少服务端本人数据范围。")
    if normalized == "manager" and plan.intent in {"all_plan_detail", "audit_log", "all_user_detail"}:
        raise PermissionError("manager 只能执行匿名汇总分析。")
    if normalized == "manager":
        select_part = sql.lower().split(" from ", 1)[0]
        if any(field in select_part for field in ("plan_no", "plan_json", "free_text", "contact_")):
            raise PermissionError("manager 查询不能返回计划或用户级明细字段。")
    if plan.intent == "audit_log" and normalized != "admin":
        raise PermissionError("只有 admin 可以读取审计数据。")


def run_sql_plan(plan: SQLPlan, role: str) -> list[dict]:
    _validate_read_plan(plan, role)
    rows = fetch_all(plan.sql, plan.params)
    if normalize_role(role) != "admin":
        rows = [mask_sensitive_row(row) for row in rows]
    return rows
