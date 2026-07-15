"""Simple, role-scoped destination popularity forecast."""

from __future__ import annotations

from ..services.database_service import fetch_all
from .permission_tool import normalize_role, scope_user_filter


def predict_next_month_hot_destinations(
    limit: int = 8,
    *,
    user_id: str = "",
    role: str = "manager",
) -> list[dict]:
    """Score destinations from historical monthly counts.

    This is an interpretable weighted score rather than a trained predictive
    model.  The response metadata marks it unavailable when coverage is too
    small; callers must not present a low-sample score as a reliable forecast.
    """
    normalized = normalize_role(role)
    suffix, params = scope_user_filter(normalized, user_id)
    params["lim"] = max(1, min(int(limit), 20))
    rows = fetch_all(
        f"""WITH monthly AS (
             SELECT destination,
                    substr(start_date, 1, 7) AS month,
                    COUNT(*) AS plan_count
             FROM travel_plans
             WHERE 1=1 {suffix}
             GROUP BY destination, substr(start_date, 1, 7)
           ),
           scored AS (
             SELECT destination,
                    AVG(plan_count) AS avg_count,
                    MAX(plan_count) AS peak_count,
                    COUNT(*) AS active_months
             FROM monthly
             GROUP BY destination
           )
           SELECT destination AS 目的地,
                  ROUND((avg_count * 0.7 + peak_count * 0.3), 1) AS 预测热度,
                  active_months AS 活跃月份数
           FROM scored
           ORDER BY 预测热度 DESC
           LIMIT :lim""",
        params,
    )
    return rows
