"""Build ECharts options from tabular results."""

from __future__ import annotations

from typing import Any


def _value_key(category_key: str, row: dict) -> str | None:
    """Return the first numeric key that is NOT the category column."""
    for key, value in row.items():
        if key == category_key:
            continue
        if isinstance(value, (int, float)) or str(value).replace(".", "", 1).replace("-", "", 1).isdigit():
            return key
    return None


def build_chart(intent: str, table: list[dict], title: str = "") -> dict | None:
    if not table or not isinstance(table[0], dict):
        return None

    keys = list(table[0].keys())
    category_key = keys[0]
    value_key = _value_key(category_key, table[0])
    if not value_key:
        # fallback: use the first numeric column even if it's the category
        for key in keys:
            val = table[0].get(key)
            if isinstance(val, (int, float)):
                value_key = key
                break
    if not value_key:
        return None

    categories = [str(row.get(category_key, "")) for row in table]
    values = [float(row.get(value_key) or 0) for row in table]
    chart_title = title or "旅行计划数据分析"

    if intent in {"profile", "traveler_type_distribution"}:
        return {
            "title": {"text": chart_title, "left": "center"},
            "tooltip": {"trigger": "item"},
            "series": [
                {
                    "type": "pie",
                    "radius": "58%",
                    "data": [
                        {"name": str(category), "value": value}
                        for category, value in zip(categories, values)
                    ],
                }
            ],
        }

    if intent in {"budget_trend", "prediction"}:
        return {
            "title": {"text": chart_title},
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": categories},
            "yAxis": {"type": "value"},
            "series": [{"type": "line", "smooth": True, "data": values}],
        }

    return {
        "title": {"text": chart_title},
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": categories},
        "yAxis": {"type": "value"},
        "series": [{"type": "bar", "data": values}],
    }
