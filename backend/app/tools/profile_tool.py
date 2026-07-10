"""User profile and similar-group recommendation helpers (SQLite)."""

from __future__ import annotations

import json

from ..services.database_service import fetch_all, fetch_one


def _parse_json(raw) -> list:
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (json.JSONDecodeError, TypeError):
        return []


def get_user_profile(user_id: str) -> dict | None:
    row = fetch_one(
        "SELECT user_id, plan_count, top_tags, fav_cities, avg_budget, "
        "avg_days, traveler_type FROM user_profiles WHERE user_id = :user_id",
        {"user_id": user_id},
    )
    if not row:
        return None
    tags = _parse_json(row.get("top_tags"))
    cities = _parse_json(row.get("fav_cities"))
    return {
        **row,
        "top_tags": tags,
        "fav_cities": cities,
        "reason": f"你已有 {row['plan_count']} 条旅行记录，偏好 {', '.join(tags[:3]) or '无'}，常去 {', '.join(cities[:3]) or '无'}。",
    }


def recommend_by_profile(user_id: str, limit: int = 5) -> dict:
    profile = get_user_profile(user_id)
    if not profile:
        rows = fetch_all(
            "SELECT destination AS city, COUNT(*) AS count, ROUND(AVG(budget), 0) AS avg_budget "
            "FROM travel_plans GROUP BY destination ORDER BY count DESC LIMIT :lim",
            {"lim": limit},
        )
        return {
            "profile": None,
            "recommendations": rows,
            "reason": "暂未找到该用户画像，先基于全站热门目的地推荐。",
        }

    trav_type = profile.get("traveler_type", "")
    rows = fetch_all(
        "SELECT destination AS city, COUNT(*) AS count, ROUND(AVG(budget), 0) AS avg_budget "
        "FROM travel_plans "
        "WHERE user_id IN (SELECT user_id FROM user_profiles WHERE traveler_type = :ttype) "
        "AND user_id != :uid "
        "GROUP BY destination ORDER BY count DESC LIMIT :lim",
        {"ttype": trav_type, "uid": user_id, "lim": limit},
    )
    if not rows:
        rows = fetch_all(
            "SELECT destination AS city, COUNT(*) AS count, ROUND(AVG(budget), 0) AS avg_budget "
            "FROM travel_plans GROUP BY destination ORDER BY count DESC LIMIT :lim",
            {"lim": limit},
        )

    return {
        "profile": profile,
        "recommendations": rows,
        "reason": profile.get("reason", f"你更接近「{trav_type}」用户，优先参考同类用户的热门目的地。"),
    }
