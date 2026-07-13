"""SQL agent for the local travel dataset (SQLite/PostgreSQL).

Safe, read-only, template-driven SQL for common analytics intents.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..services.database_service import fetch_all
from .permission_tool import normalize_role, scope_user_filter
from .sensitive_filter_tool import mask_sensitive_row


@dataclass
class SQLPlan:
    intent: str
    sql: str
    params: dict
    agent: str = "SQLAgent"
    title: str = ""


def classify_sql_intent(message: str) -> str:
    text = message or ""
    if any(w in text for w in ("预测", "下个月", "下月", "未来", "forecast")):
        return "prediction"
    if any(w in text for w in ("画像", "兴趣", "偏好", "类型", "profile")):
        return "profile"
    if any(w in text for w in ("相似", "推荐", "适合我")):
        return "recommendation"
    if any(w in text for w in ("趋势", "每月", "月份")):
        return "budget_trend"
    if any(w in text for w in ("平均预算", "预算", "消费", "花费", "avg")):
        return "avg_budget"
    if any(w in text for w in ("明细", "详情", "全部", "列表", "所有")):
        return "all_plan_detail"
    if any(w in text for w in ("分类", "分布")):
        return "traveler_type_distribution"
    return "city_rank"


def build_sql_plan(message: str, user_id: str, role: str) -> SQLPlan:
    intent = classify_sql_intent(message)
    role = normalize_role(role)
    text = message or ""
    is_personal_query = any(word in text for word in ("我的", "我", "自己"))
    is_scoped = role in ("guest", "user") and is_personal_query

    base_filter = "WHERE user_id = :user_id" if is_scoped else "WHERE 1=1"
    params: dict = {"user_id": user_id} if is_scoped else {}

    if intent == "profile":
        return SQLPlan(
            intent=intent, title="我的旅行画像",
            sql="SELECT user_id AS 用户, plan_count AS 计划数, "
                "top_tags AS 兴趣标签, fav_cities AS 常去城市, "
                "avg_budget AS 平均预算, avg_days AS 平均天数, traveler_type AS 旅行者类型 "
                "FROM user_profiles WHERE user_id = :user_id",
            params={"user_id": user_id}, agent="ProfileAgent",
        )

    if intent == "avg_budget":
        return SQLPlan(
            intent=intent, title="各目的地平均预算",
            sql=f"SELECT destination AS 目的地, ROUND(AVG(budget), 0) AS 平均预算, COUNT(*) AS 计划数 "
                f"FROM travel_plans {base_filter} GROUP BY destination ORDER BY 平均预算 DESC LIMIT 10",
            params=params,
        )

    if intent == "budget_trend":
        return SQLPlan(
            intent=intent, title="月度预算趋势",
            sql=f"SELECT substr(start_date, 1, 7) AS 月份, "
                f"ROUND(AVG(budget), 0) AS 平均预算 "
                f"FROM travel_plans {base_filter} GROUP BY 月份 ORDER BY 月份 LIMIT 12",
            params=params,
        )

    if intent == "traveler_type_distribution":
        return SQLPlan(
            intent=intent, title="旅行者类型分布",
            sql="SELECT traveler_type AS 类型, COUNT(*) AS 用户数 "
                "FROM user_profiles GROUP BY traveler_type ORDER BY 用户数 DESC",
            params={},
        )

    if intent == "all_plan_detail":
        detail_filter = "WHERE user_id = :user_id" if role != "admin" else ""
        detail_params = {"user_id": user_id} if role != "admin" else {}
        return SQLPlan(
            intent=intent, title="旅行计划明细",
            sql=f"SELECT plan_no AS 编号, destination AS 目的地, "
                f"start_date AS 出发日期, travel_days AS 天数, "
                f"budget AS 预算, transportation AS 交通, accommodation AS 住宿 "
                f"FROM travel_plans {detail_filter} ORDER BY created_at DESC LIMIT 30",
            params=detail_params,
        )

    # default: city_rank
    return SQLPlan(
        intent="city_rank", title="热门目的地排行",
        sql=f"SELECT destination AS 目的地, COUNT(*) AS 计划数, "
            f"ROUND(AVG(budget), 0) AS 平均预算 "
            f"FROM travel_plans {base_filter} GROUP BY destination ORDER BY 计划数 DESC LIMIT 10",
        params=params,
    )


def run_sql_plan(plan: SQLPlan, role: str) -> list[dict]:
    rows = fetch_all(plan.sql, plan.params)
    if normalize_role(role) != "admin":
        rows = [mask_sensitive_row(row) for row in rows]
    return rows
