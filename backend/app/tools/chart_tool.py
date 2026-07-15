"""Build ECharts options from the exact tabular analytics result."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _is_number(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return True
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _preferred_value_key(intent: str, row: dict) -> str | None:
    preferred = {
        "city_rank": ("计划数", "count"),
        "avg_budget": ("平均预算", "avg_budget"),
        "budget_trend": ("平均预算", "avg_budget"),
        "prediction": ("预测热度", "score"),
        "traveler_type_distribution": ("用户数", "count"),
        "data_quality": ("计划数", "count"),
        "recommendation": ("相似用户计划数", "计划数", "count"),
    }
    for key in preferred.get(intent, ()):
        if key in row and _is_number(row.get(key)):
            return key
    for key, value in row.items():
        if _is_number(value):
            return key
    return None


def _base(title: str) -> dict:
    return {
        "title": {"text": title or "旅行计划数据分析", "left": "center"},
        "tooltip": {"trigger": "axis"},
        "legend": {"top": 30},
        "grid": {"left": 48, "right": 24, "top": 72, "bottom": 48, "containLabel": True},
        "toolbox": {"feature": {"saveAsImage": {"title": "保存图表"}}},
    }


def build_chart(intent: str, table: list[dict], title: str = "") -> dict | None:
    if not table or not isinstance(table[0], dict) or intent in {"all_plan_detail", "audit_log", "profile"}:
        return None

    first = table[0]
    value_key = _preferred_value_key(intent, first)
    if not value_key:
        return None
    chart_title = title or "旅行计划数据分析"

    if "周期" in first and "目的地" in first:
        categories = list(dict.fromkeys(str(row.get("目的地", "")) for row in table))
        periods = list(dict.fromkeys(str(row.get("周期", "")) for row in table))
        values: dict[str, dict[str, float]] = defaultdict(dict)
        for row in table:
            values[str(row.get("周期", ""))][str(row.get("目的地", ""))] = float(
                row.get(value_key) or 0
            )
        option = _base(chart_title)
        option.update(
            {
                "xAxis": {"type": "category", "data": categories, "axisLabel": {"rotate": 20}},
                "yAxis": {"type": "value", "name": value_key},
                "series": [
                    {
                        "name": period,
                        "type": "bar",
                        "data": [values[period].get(category, 0) for category in categories],
                    }
                    for period in periods
                ],
            }
        )
        return option

    category_key = next(
        (key for key in first if key != value_key and not _is_number(first.get(key))),
        next(iter(first), ""),
    )
    categories = [str(row.get(category_key, "")) for row in table]
    values = [float(row.get(value_key) or 0) for row in table]

    if intent in {"traveler_type_distribution", "data_quality"}:
        return {
            "title": {"text": chart_title, "left": "center"},
            "tooltip": {"trigger": "item"},
            "legend": {"bottom": 0},
            "series": [
                {
                    "name": value_key,
                    "type": "pie",
                    "radius": ["38%", "66%"],
                    "data": [
                        {"name": category, "value": value}
                        for category, value in zip(categories, values)
                    ],
                }
            ],
        }

    chart_type = "line" if intent in {"budget_trend", "prediction"} else "bar"
    option = _base(chart_title)
    option.update(
        {
            "xAxis": {"type": "category", "data": categories, "axisLabel": {"rotate": 20}},
            "yAxis": {"type": "value", "name": value_key},
            "series": [
                {
                    "name": value_key,
                    "type": chart_type,
                    "smooth": chart_type == "line",
                    "data": values,
                }
            ],
        }
    )
    return option
