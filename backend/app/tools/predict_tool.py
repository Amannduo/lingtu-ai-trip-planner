"""Simple forecast tool for destination popularity (SQLite)."""

from __future__ import annotations

from ..services.database_service import fetch_all


def predict_next_month_hot_destinations(limit: int = 8) -> list[dict]:
    """Score destinations by weighted historical popularity."""
    rows = fetch_all(
        """WITH monthly AS (
             SELECT destination,
                    strftime('%Y-%m', start_date) AS month,
                    COUNT(*) AS plan_count
             FROM travel_plans
             GROUP BY destination, strftime('%Y-%m', start_date)
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
        {"lim": limit},
    )
    return rows
