"""Render and deliver generated trip plans by email."""

from __future__ import annotations

from ..models.schemas import TripPlan
from ..tools.send_email_tool import send_email


def render_trip_plan_text(plan: TripPlan, plan_no: str | None = None) -> str:
    lines = [
        f"灵途旅行计划：{plan.city}",
        f"日期：{plan.start_date} 至 {plan.end_date}",
    ]
    if plan_no:
        lines.append(f"计划编号：{plan_no}")

    weather_by_date = {item.date: item for item in plan.weather_info}
    for day in plan.days:
        lines.extend(["", f"第 {day.day_index + 1} 天 · {day.date}", day.description])
        weather = weather_by_date.get(day.date)
        if weather:
            lines.append(
                f"天气：{weather.day_weather} {weather.day_temp}℃ / "
                f"{weather.night_weather} {weather.night_temp}℃"
            )
        lines.append(f"交通：{day.transportation}")

        for index, attraction in enumerate(day.attractions, start=1):
            duration = f"，建议 {attraction.visit_duration} 分钟" if attraction.visit_duration else ""
            lines.append(f"{index}. {attraction.name}（{attraction.address}{duration}）")
            if attraction.description:
                lines.append(f"   {attraction.description}")

        if day.meals:
            meals = "；".join(
                f"{meal.name}（约 {meal.estimated_cost} 元）" for meal in day.meals
            )
            lines.append(f"餐饮：{meals}")
        if day.hotel:
            hotel = day.hotel
            lines.append(
                f"住宿：{hotel.name}（{hotel.address or hotel.price_range}，"
                f"约 {hotel.estimated_cost} 元/晚）"
            )
            if hotel.selection_reason:
                lines.append(f"   选址说明：{hotel.selection_reason}")
        elif day.accommodation:
            lines.append(f"住宿：{day.accommodation}")

    if plan.budget:
        budget = plan.budget
        lines.extend([
            "",
            "预算汇总",
            f"景点 {budget.total_attractions} 元，住宿 {budget.total_hotels} 元，"
            f"餐饮 {budget.total_meals} 元，交通 {budget.total_transportation} 元",
            f"预计总计：{budget.total} 元",
        ])
        lines.extend(f"- {note}" for note in budget.budget_notes)

    lines.extend(["", "总体建议", plan.overall_suggestions])
    if plan.web_guide:
        lines.extend(["", "补充攻略", plan.web_guide])
    lines.extend(["", "地图坐标和营业信息可能变化，出发前请使用地图应用再次确认。"])
    return "\n".join(lines)


def deliver_trip_plan_email(
    recipient: str,
    plan: TripPlan,
    plan_no: str | None = None,
    *,
    user_id: str | None = None,
    client_ip: str | None = None,
) -> dict[str, object]:
    result = send_email(
        recipient,
        f"灵途旅行计划｜{plan.city} {plan.start_date}",
        render_trip_plan_text(plan, plan_no),
        user_id=user_id,
        client_ip=client_ip,
        email_type="trip_plan",
    )
    return {
        "requested": True,
        "sent": bool(result.get("sent")),
        "dry_run": bool(result.get("dry_run")),
        "blocked": bool(result.get("blocked")),
        "to": result.get("to") or recipient,
        "message": str(result.get("message") or "邮件投递失败"),
    }
