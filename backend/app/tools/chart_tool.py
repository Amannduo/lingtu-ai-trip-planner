"""Build ECharts options from the exact tabular analytics result."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

# Brand palette aligned with the frontend (teal / slate / soft accents).
PALETTE = [
    "#0f766e",
    "#2563eb",
    "#14b8a6",
    "#6366f1",
    "#0ea5e9",
    "#059669",
    "#8b5cf6",
    "#0284c7",
]


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


def _axis_label_rotate(categories: list[str]) -> int:
    if not categories:
        return 0
    longest = max(len(str(item)) for item in categories)
    if len(categories) > 8 or longest > 6:
        return 28
    if len(categories) > 5 or longest > 4:
        return 18
    return 0


def _base(title: str) -> dict:
    return {
        "color": PALETTE,
        "backgroundColor": "transparent",
        "textStyle": {
            "fontFamily": "Segoe UI, PingFang SC, Microsoft YaHei, sans-serif",
            "color": "#334155",
        },
        # Title is rendered by the frontend shell; keep a light in-chart title off
        # so the plot area is not vertically compressed.
        "title": {
            "show": False,
            "text": title or "旅行数据分析",
        },
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": "rgba(15, 23, 42, 0.92)",
            "borderWidth": 0,
            "padding": [10, 12],
            "textStyle": {"color": "#f8fafc", "fontSize": 12},
            "axisPointer": {
                "type": "shadow",
                "shadowStyle": {"color": "rgba(15, 118, 110, 0.08)"},
            },
            "extraCssText": "border-radius:10px;box-shadow:0 12px 28px rgba(15,23,42,0.18);",
        },
        "legend": {
            "top": 8,
            "right": 12,
            "icon": "roundRect",
            "itemWidth": 10,
            "itemHeight": 10,
            "itemGap": 14,
            "textStyle": {"color": "#64748b", "fontSize": 12},
        },
        "grid": {
            "left": 18,
            "right": 18,
            "top": 40,
            "bottom": 28,
            "containLabel": True,
        },
        "toolbox": {
            "right": 10,
            "top": 6,
            "itemSize": 14,
            "feature": {
                "saveAsImage": {
                    "title": "保存",
                    "pixelRatio": 2,
                    "backgroundColor": "#ffffff",
                }
            },
            "iconStyle": {"borderColor": "#94a3b8"},
        },
    }


def _category_axis(categories: list[str]) -> dict:
    return {
        "type": "category",
        "data": categories,
        "axisTick": {"show": False},
        "axisLine": {"lineStyle": {"color": "#e2e8f0"}},
        "axisLabel": {
            "color": "#64748b",
            "fontSize": 11,
            "interval": 0,
            "rotate": _axis_label_rotate(categories),
            "hideOverlap": True,
        },
    }


def _value_axis(name: str) -> dict:
    return {
        "type": "value",
        "name": name,
        "nameTextStyle": {"color": "#94a3b8", "fontSize": 11, "padding": [0, 0, 0, 8]},
        "splitLine": {
            "lineStyle": {
                "color": "#f1f5f9",
                "type": "dashed",
            }
        },
        "axisLine": {"show": False},
        "axisTick": {"show": False},
        "axisLabel": {"color": "#94a3b8", "fontSize": 11},
    }


def _bar_series(name: str, data: list[float], *, color: str | None = None) -> dict:
    item: dict[str, Any] = {
        "name": name,
        "type": "bar",
        "barMaxWidth": 28,
        "barCategoryGap": "42%",
        "data": data,
        "itemStyle": {
            "borderRadius": [8, 8, 2, 2],
            "color": color
            or {
                "type": "linear",
                "x": 0,
                "y": 0,
                "x2": 0,
                "y2": 1,
                "colorStops": [
                    {"offset": 0, "color": "#14b8a6"},
                    {"offset": 1, "color": "#0f766e"},
                ],
            },
        },
        "emphasis": {
            "itemStyle": {
                "shadowBlur": 12,
                "shadowColor": "rgba(15, 118, 110, 0.28)",
            }
        },
    }
    return item


def _line_series(name: str, data: list[float]) -> dict:
    return {
        "name": name,
        "type": "line",
        "smooth": True,
        "symbol": "circle",
        "symbolSize": 8,
        "showSymbol": len(data) <= 12,
        "data": data,
        "lineStyle": {"width": 3, "color": "#0f766e"},
        "itemStyle": {
            "color": "#0f766e",
            "borderColor": "#ffffff",
            "borderWidth": 2,
        },
        "areaStyle": {
            "color": {
                "type": "linear",
                "x": 0,
                "y": 0,
                "x2": 0,
                "y2": 1,
                "colorStops": [
                    {"offset": 0, "color": "rgba(20, 184, 166, 0.28)"},
                    {"offset": 1, "color": "rgba(20, 184, 166, 0.02)"},
                ],
            }
        },
    }


def build_chart(intent: str, table: list[dict], title: str = "") -> dict | None:
    if not table or not isinstance(table[0], dict) or intent in {"all_plan_detail", "audit_log", "profile"}:
        return None

    first = table[0]
    value_key = _preferred_value_key(intent, first)
    if not value_key:
        return None
    chart_title = title or "旅行数据分析"

    # Grouped bar chart when period comparison is present.
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
                "tooltip": {
                    **option["tooltip"],
                    "axisPointer": {
                        "type": "shadow",
                        "shadowStyle": {"color": "rgba(37, 99, 235, 0.06)"},
                    },
                },
                "xAxis": _category_axis(categories),
                "yAxis": _value_axis(value_key),
                "series": [
                    _bar_series(
                        period,
                        [values[period].get(category, 0) for category in categories],
                        color=PALETTE[index % len(PALETTE)],
                    )
                    for index, period in enumerate(periods)
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
            "color": PALETTE,
            "backgroundColor": "transparent",
            "textStyle": {
                "fontFamily": "Segoe UI, PingFang SC, Microsoft YaHei, sans-serif",
                "color": "#334155",
            },
            "title": {
                "show": False,
                "text": chart_title,
            },
            "tooltip": {
                "trigger": "item",
                "backgroundColor": "rgba(15, 23, 42, 0.92)",
                "borderWidth": 0,
                "padding": [10, 12],
                "textStyle": {"color": "#f8fafc", "fontSize": 12},
                "formatter": "{b}<br/>{c}（{d}%）",
                "extraCssText": "border-radius:10px;box-shadow:0 12px 28px rgba(15,23,42,0.18);",
            },
            "legend": {
                "bottom": 10,
                "left": "center",
                "icon": "circle",
                "itemWidth": 8,
                "itemHeight": 8,
                "textStyle": {"color": "#64748b", "fontSize": 12},
            },
            "series": [
                {
                    "name": value_key,
                    "type": "pie",
                    "radius": ["44%", "70%"],
                    "center": ["50%", "46%"],
                    "avoidLabelOverlap": True,
                    "itemStyle": {
                        "borderRadius": 8,
                        "borderColor": "#ffffff",
                        "borderWidth": 2,
                    },
                    "label": {
                        "color": "#475569",
                        "fontSize": 11,
                        "formatter": "{b}\n{d}%",
                    },
                    "labelLine": {
                        "length": 10,
                        "length2": 8,
                        "lineStyle": {"color": "#cbd5e1"},
                    },
                    "emphasis": {
                        "scale": True,
                        "scaleSize": 6,
                        "itemStyle": {
                            "shadowBlur": 16,
                            "shadowColor": "rgba(15, 118, 110, 0.22)",
                        },
                    },
                    "data": [
                        {"name": category, "value": value}
                        for category, value in zip(categories, values)
                    ],
                }
            ],
        }

    chart_type = "line" if intent in {"budget_trend", "prediction"} else "bar"
    option = _base(chart_title)
    series = (
        _line_series(value_key, values)
        if chart_type == "line"
        else _bar_series(value_key, values)
    )
    option.update(
        {
            "legend": {"show": False},
            "xAxis": _category_axis(categories),
            "yAxis": _value_axis(value_key),
            "series": [series],
        }
    )
    return option
