"""Chinese result formatter for agent responses."""

from __future__ import annotations


def summarize_table(intent: str, table: list[dict], extra_reason: str = "") -> str:
    if extra_reason:
        prefix = extra_reason
    else:
        prefix = ""
    if not table:
        return prefix or "没有查询到符合条件的数据。"

    first = table[0]
    if intent == "city_rank":
        return f"{prefix}当前数据中最热门的目的地是{first.get('目的地')}，共有{first.get('计划数')}条旅行计划。".strip()
    if intent == "avg_budget":
        return f"{prefix}平均预算最高的目的地是{first.get('目的地')}，平均预算约{first.get('平均预算')}元。".strip()
    if intent == "budget_trend":
        return f"{prefix}已按月份整理平均预算趋势，可结合折线图观察预算变化。".strip()
    if intent == "profile":
        return f"{prefix}该用户属于{first.get('旅行者类型', '未知')}，已有{first.get('计划数', 0)}条旅行计划记录。".strip()
    if intent == "traveler_type_distribution":
        return f"{prefix}用户最多的旅行者类型是{first.get('旅行者类型')}，共有{first.get('用户数')}人。".strip()
    if intent == "prediction":
        return f"{prefix}根据历史月度热度，预测下个月热度最高的目的地是{first.get('目的地')}。".strip()
    return f"{prefix}已查询到{len(table)}条结果。".strip()
