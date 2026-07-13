"""Chinese result formatter for agent responses."""

from __future__ import annotations


def summarize_table(intent: str, table: list[dict], extra_reason: str = "") -> str:
    if extra_reason:
        prefix = extra_reason
    else:
        prefix = ""
    if not table:
        if intent == "profile":
            return prefix or "当前账号还没有旅行计划数据。请先生成一次旅行计划，系统会自动沉淀画像。"
        return prefix or "没有查询到符合条件的数据。"

    first = table[0]
    destination = (
        first.get("目的地")
        or first.get("城市")
        or first.get("热门城市")
        or first.get("destination")
        or first.get("city")
    )
    plan_count = first.get("计划数") or first.get("计划数量") or first.get("相似用户计划数") or first.get("count")
    avg_budget = first.get("平均预算") or first.get("avg_budget")

    if intent == "city_rank":
        return f"{prefix}当前数据中最热门的目的地是{destination}，共有{plan_count}条旅行计划。".strip()
    if intent == "avg_budget":
        return f"{prefix}平均预算最高的目的地是{destination}，平均预算约{avg_budget}元。".strip()
    if intent == "budget_trend":
        return f"{prefix}已按月份整理平均预算趋势，可结合折线图观察预算变化。".strip()
    if intent == "profile":
        return f"{prefix}该用户属于{first.get('旅行者类型', '未知')}，已有{first.get('计划数', 0)}条旅行计划记录。".strip()
    if intent == "traveler_type_distribution":
        traveler_type = first.get("旅行者类型") or first.get("类型") or first.get("traveler_type")
        return f"{prefix}用户最多的旅行者类型是{traveler_type}，共有{first.get('用户数')}人。".strip()
    if intent == "prediction":
        return f"{prefix}根据历史月度热度，预测下个月热度最高的目的地是{destination}。".strip()
    return f"{prefix}已查询到{len(table)}条结果。".strip()
