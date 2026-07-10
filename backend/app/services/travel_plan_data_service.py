"""Travel-plan persistence — saves generated plans to SQLite."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from ..models.schemas import TripPlan, TripRequest
from .database_service import execute, fetch_all, fetch_one, get_db_connection
from .schema import init_db


class TravelPlanDataService:
    """Persist trip plans and maintain user profile summaries."""

    def save_trip_plan(
        self,
        request: TripRequest,
        trip_plan: TripPlan,
        *,
        user_id: str = "u_current",
        user_role: str = "user",
        source: str = "generated",
    ) -> str:
        init_db()
        plan_no = f"P{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
        summary = _summary_from_plan(trip_plan)
        budget = trip_plan.budget

        execute(
            """INSERT INTO travel_plans
               (plan_no, user_id, user_role, origin_city, destination,
                start_date, end_date, travel_days, travelers,
                budget, actual_cost, transportation, accommodation,
                preferences, free_text, summary, status, source)
               VALUES
               (:plan_no, :user_id, :user_role, :origin_city, :destination,
                :start_date, :end_date, :travel_days, :travelers,
                :budget, :actual_cost, :transportation, :accommodation,
                :preferences, :free_text, :summary, :status, :source)""",
            {
                "plan_no": plan_no,
                "user_id": user_id,
                "user_role": user_role,
                "origin_city": request.origin_city or "",
                "destination": request.city,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "travel_days": request.travel_days,
                "travelers": request.travelers,
                "budget": budget.total if budget else request.budget,
                "actual_cost": None,
                "transportation": request.transportation,
                "accommodation": request.accommodation,
                "preferences": json.dumps(request.preferences, ensure_ascii=False),
                "free_text": request.free_text_input or "",
                "summary": summary,
                "status": "completed",
                "source": source,
            },
        )
        self._refresh_profile(user_id)
        return plan_no

    def _refresh_profile(self, user_id: str) -> None:
        rows = fetch_all(
            "SELECT * FROM travel_plans WHERE user_id = :uid ORDER BY created_at",
            {"uid": user_id},
        )
        if not rows:
            return
        budgets = [r["budget"] for r in rows if r["budget"]]
        days = [r["travel_days"] for r in rows if r["travel_days"]]
        cities = [r["destination"] for r in rows]
        tags: list[str] = []
        for r in rows:
            try:
                tags.extend(json.loads(r.get("preferences", "[]")))
            except (json.JSONDecodeError, TypeError):
                pass

        top_tags = _top_n(tags, 5)
        fav_cities = _top_n(cities, 5)

        execute(
            """INSERT OR REPLACE INTO user_profiles
               (user_id, plan_count, top_tags, fav_cities, avg_budget, avg_days, traveler_type, updated_at)
               VALUES (:uid, :cnt, :tags, :cities, :avg_b, :avg_d, :ttype, datetime('now'))""",
            {
                "uid": user_id,
                "cnt": len(rows),
                "tags": json.dumps(top_tags, ensure_ascii=False),
                "cities": json.dumps(fav_cities, ensure_ascii=False),
                "avg_b": sum(budgets) / len(budgets) if budgets else 0,
                "avg_d": sum(days) / len(days) if days else 0,
                "ttype": _classify_traveler(top_tags, sum(budgets) / len(budgets) if budgets else 0, sum(days) / len(days) if days else 0),
            },
        )

    def log_audit(self, **kwargs: Any) -> None:
        init_db()
        detail = kwargs.pop("audit_detail", {})
        execute(
            """INSERT INTO audit_logs (user_id, user_role, message, agent, tool, allowed, sensitive_hit, detail)
               VALUES (:user_id, :user_role, :message, :agent, :tool, :allowed, :sensitive_hit, :detail)""",
            {
                "user_id": kwargs.get("user_id", ""),
                "user_role": kwargs.get("user_role", "guest"),
                "message": kwargs.get("message", ""),
                "agent": kwargs.get("routed_agent", ""),
                "tool": kwargs.get("tool_name", ""),
                "allowed": 1 if kwargs.get("permission_allowed", True) else 0,
                "sensitive_hit": 1 if kwargs.get("sensitive_hit", False) else 0,
                "detail": json.dumps(detail, ensure_ascii=False),
            },
        )

    def log_query(self, **kwargs: Any) -> None:
        init_db()
        execute(
            """INSERT INTO query_logs (user_id, user_role, question, intent, sql_text, result_summary)
               VALUES (:user_id, :user_role, :question, :intent, :sql_text, :result_summary)""",
            {
                "user_id": kwargs.get("user_id", ""),
                "user_role": kwargs.get("user_role", "guest"),
                "question": kwargs.get("question", ""),
                "intent": kwargs.get("intent", ""),
                "sql_text": kwargs.get("sql_text", ""),
                "result_summary": kwargs.get("result_summary", ""),
            },
        )


# ── helpers ──────────────────────────────────────────────────────────────

def _summary_from_plan(plan: TripPlan) -> str:
    names: list[str] = []
    for day in plan.days or []:
        for attr in day.attractions or []:
            if attr.name:
                names.append(attr.name)
    return f"{plan.city} {plan.start_date}-{plan.end_date}: {', '.join(names[:6])}"


def _top_n(items: list[str], n: int) -> list[str]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return sorted(counts, key=counts.get, reverse=True)[:n]


def _classify_traveler(tags: list[str], avg_budget: float, avg_days: float) -> str:
    tag_str = " ".join(tags).lower()
    if "美食" in tag_str:
        return "美食探索型"
    if "自然风光" in tag_str:
        return "自然风光型"
    if "历史文化" in tag_str:
        return "文化历史型"
    if "购物" in tag_str:
        return "购物休闲型"
    if avg_budget > 5000:
        return "品质享受型"
    if avg_days < 3:
        return "短途周末型"
    return "综合探索型"


_travel_plan_data_service: TravelPlanDataService | None = None


def get_travel_plan_data_service() -> TravelPlanDataService:
    global _travel_plan_data_service
    if _travel_plan_data_service is None:
        _travel_plan_data_service = TravelPlanDataService()
    return _travel_plan_data_service
