"""Build a restricted, typed chart payload from authorized analytics tables.

The payload is intentionally NOT a raw ECharts/Chart.js option object. The
frontend alone maps this schema into a library-specific option with fixed
styling — no user/LLM formatters, HTML, URLs, or executable fields.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Literal

ChartKind = Literal["bar", "line", "pie"]

MAX_TITLE_LEN = 120
MAX_AXIS_LABEL_LEN = 64
MAX_CATEGORY_LEN = 100
MAX_SERIES_NAME_LEN = 64
MAX_NOTE_LEN = 200
MAX_CATEGORIES = 50
MAX_SERIES = 8
MAX_POINTS = 400
MAX_PIE_SLICES = 20

ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "kind",
        "title",
        "x_label",
        "y_label",
        "categories",
        "series",
        "truncated",
        "note",
    }
)
ALLOWED_SERIES_KEYS = frozenset({"name", "values"})
FORBIDDEN_NESTED_KEYS = frozenset(
    {
        "formatter",
        "renderItem",
        "encode",
        "markLine",
        "markPoint",
        "markArea",
        "tooltip",
        "graphic",
        "dataset",
        "rich",
        "html",
        "url",
        "href",
        "src",
        "onclick",
        "script",
        "__proto__",
        "prototype",
        "constructor",
    }
)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

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


def _clean_text(value: Any, max_len: int) -> str:
    """Strip controls and neutralize angle brackets for pure-text display."""
    text = _CONTROL_RE.sub("", str(value if value is not None else "")).strip()
    # Keep labels plain-text safe for ECharts canvas and Vue text nodes.
    text = text.replace("<", "＜").replace(">", "＞")
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def _is_finite_number(value: Any) -> bool:
    """True only for real int/float finite numbers (not bool, not numeric strings)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _as_finite_float(value: Any) -> float | None:
    if not _is_finite_number(value):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


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
        if key in row and _is_finite_number(row.get(key)):
            return key
    for key, value in row.items():
        if _is_finite_number(value):
            return key
    return None


def _payload(
    *,
    kind: ChartKind,
    title: str,
    categories: list[str],
    series: list[dict[str, Any]],
    x_label: str = "",
    y_label: str = "",
    truncated: bool = False,
    note: str = "",
) -> dict[str, Any] | None:
    title = _clean_text(title or "旅行计划数据分析", MAX_TITLE_LEN)
    x_label = _clean_text(x_label, MAX_AXIS_LABEL_LEN)
    y_label = _clean_text(y_label, MAX_AXIS_LABEL_LEN)
    note = _clean_text(note, MAX_NOTE_LEN)
    categories = [
        _clean_text(item, MAX_CATEGORY_LEN) or f"项{index + 1}"
        for index, item in enumerate(categories)
    ]
    clean_series: list[dict[str, Any]] = []
    for index, item in enumerate(series[:MAX_SERIES]):
        if not isinstance(item, dict):
            return None
        if set(item.keys()) - ALLOWED_SERIES_KEYS:
            # Drop unknown series keys by rebuilding; forbidden nested keys reject.
            if any(key in FORBIDDEN_NESTED_KEYS for key in item):
                return None
        name = _clean_text(item.get("name") or f"系列{index + 1}", MAX_SERIES_NAME_LEN)
        raw_values = item.get("values")
        if not isinstance(raw_values, list):
            return None
        values: list[float] = []
        for raw in raw_values:
            number = _as_finite_float(raw)
            if number is None:
                return None
            values.append(number)
        if kind in {"bar", "line"} and len(values) != len(categories):
            return None
        if kind == "pie" and any(value < 0 for value in values):
            return None
        clean_series.append({"name": name, "values": values})

    if not categories or not clean_series:
        return None

    if kind == "pie":
        # Single series only for pie.
        categories = categories[:MAX_PIE_SLICES]
        values = clean_series[0]["values"][:MAX_PIE_SLICES]
        if len(categories) != len(values):
            return None
        if not values or all(value == 0 for value in values):
            return None
        clean_series = [{"name": clean_series[0]["name"], "values": values}]
    else:
        if len(categories) > MAX_CATEGORIES:
            categories = categories[:MAX_CATEGORIES]
            clean_series = [
                {"name": item["name"], "values": item["values"][:MAX_CATEGORIES]}
                for item in clean_series
            ]
            truncated = True
        total_points = len(categories) * len(clean_series)
        if total_points > MAX_POINTS:
            max_cats = max(1, MAX_POINTS // max(1, len(clean_series)))
            categories = categories[:max_cats]
            clean_series = [
                {"name": item["name"], "values": item["values"][:max_cats]}
                for item in clean_series
            ]
            truncated = True

    payload: dict[str, Any] = {
        "kind": kind,
        "title": title,
        "x_label": x_label,
        "y_label": y_label,
        "categories": list(categories),
        "series": [{"name": s["name"], "values": list(s["values"])} for s in clean_series],
        "truncated": bool(truncated),
        "note": note or ("数据点较多，图表仅展示部分结果。" if truncated else ""),
    }
    return payload


def build_chart(intent: str, table: list[dict], title: str = "") -> dict | None:
    """Convert an authorized analytics table into a restricted chart payload."""
    if not table or not isinstance(table[0], dict):
        return None
    # Detail / audit / profile intents stay tabular only (privacy + cardinality).
    if intent in {"all_plan_detail", "audit_log", "profile"}:
        return None

    first = table[0]
    value_key = _preferred_value_key(intent, first)
    if not value_key:
        return None
    chart_title = title or "旅行计划数据分析"
    # Never embed SQL / error-looking titles.
    lowered = str(chart_title).lower()
    if any(token in lowered for token in ("select ", " from ", "password", "api_key", "traceback")):
        chart_title = "旅行计划数据分析"

    # Grouped bar chart when period comparison is present.
    if "周期" in first and "目的地" in first:
        categories = list(
            dict.fromkeys(_clean_text(row.get("目的地", ""), MAX_CATEGORY_LEN) for row in table)
        )
        periods = list(
            dict.fromkeys(_clean_text(row.get("周期", ""), MAX_SERIES_NAME_LEN) for row in table)
        )
        values: dict[str, dict[str, float]] = defaultdict(dict)
        for row in table:
            number = _as_finite_float(row.get(value_key))
            if number is None:
                continue
            values[_clean_text(row.get("周期", ""), MAX_SERIES_NAME_LEN)][
                _clean_text(row.get("目的地", ""), MAX_CATEGORY_LEN)
            ] = number
        series = [
            {
                "name": period or "周期",
                "values": [float(values[period].get(category, 0.0)) for category in categories],
            }
            for period in periods
        ]
        return _payload(
            kind="bar",
            title=chart_title,
            categories=categories,
            series=series,
            x_label="目的地",
            y_label=str(value_key),
            truncated=len(table) > MAX_CATEGORIES,
        )

    category_key = next(
        (key for key in first if key != value_key and not _is_finite_number(first.get(key))),
        next(iter(first), ""),
    )
    categories: list[str] = []
    values: list[float] = []
    for row in table:
        number = _as_finite_float(row.get(value_key))
        if number is None:
            continue
        categories.append(_clean_text(row.get(category_key, ""), MAX_CATEGORY_LEN) or "未命名")
        values.append(number)

    if not categories:
        return None

    if intent in {"traveler_type_distribution", "data_quality"}:
        return _payload(
            kind="pie",
            title=chart_title,
            categories=categories,
            series=[{"name": str(value_key), "values": values}],
            y_label=str(value_key),
            truncated=len(categories) > MAX_PIE_SLICES,
        )

    kind: ChartKind = "line" if intent in {"budget_trend", "prediction"} else "bar"
    return _payload(
        kind=kind,
        title=chart_title,
        categories=categories,
        series=[{"name": str(value_key), "values": values}],
        x_label=str(category_key),
        y_label=str(value_key),
        truncated=len(categories) > MAX_CATEGORIES,
    )


def sanitize_chart_payload(raw: Any) -> dict[str, Any] | None:
    """Defense-in-depth: accept only restricted chart objects for API responses.

    Returns a newly built payload (never the original dict reference).
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    # Reject legacy full library options and unknown top-level keys that are not
    # part of the restricted schema (except we only *read* allowlisted keys).
    if any(key in FORBIDDEN_NESTED_KEYS for key in raw):
        return None
    if "xAxis" in raw or "yAxis" in raw or "tooltip" in raw or "grid" in raw:
        return None
    kind = raw.get("kind")
    if kind not in {"bar", "line", "pie"}:
        return None
    categories = raw.get("categories")
    series = raw.get("series")
    if not isinstance(categories, list) or not isinstance(series, list):
        return None
    if len(categories) == 0 or len(series) == 0:
        return None
    clean_series: list[dict[str, Any]] = []
    for item in series:
        if not isinstance(item, dict):
            return None
        if any(key in FORBIDDEN_NESTED_KEYS for key in item):
            return None
        values = item.get("values")
        if not isinstance(values, list):
            return None
        clean_series.append({"name": item.get("name", ""), "values": values})
    # Rebuild via _payload so unknown keys and original references are dropped.
    return _payload(
        kind=kind,  # type: ignore[arg-type]
        title=str(raw.get("title") or ""),
        categories=[str(item) for item in categories],
        series=clean_series,
        x_label=str(raw.get("x_label") or ""),
        y_label=str(raw.get("y_label") or ""),
        truncated=bool(raw.get("truncated")),
        note=str(raw.get("note") or ""),
    )
