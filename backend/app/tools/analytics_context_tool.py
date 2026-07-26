"""Shared analytics context: role capabilities, periods and data quality.

This module is intentionally deterministic.  A language model may help to
understand a question, but the server remains the authority for both the time
window and the rows a role is allowed to see.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from ..services.database_service import fetch_all, fetch_one
from .permission_tool import normalize_role, scope_user_filter


@dataclass(frozen=True)
class AnalysisPeriod:
    key: str
    label: str
    start: date | None = None
    end_exclusive: date | None = None
    comparison_start: date | None = None
    comparison_end_exclusive: date | None = None
    comparison_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("start", "end_exclusive", "comparison_start", "comparison_end_exclusive"):
            value = payload.get(key)
            payload[key] = value.isoformat() if value else None
        if self.end_exclusive:
            payload["end"] = (self.end_exclusive - timedelta(days=1)).isoformat()
        else:
            payload["end"] = None
        if self.comparison_end_exclusive:
            payload["comparison_end"] = (
                self.comparison_end_exclusive - timedelta(days=1)
            ).isoformat()
        else:
            payload["comparison_end"] = None
        return payload


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        return start, date(year + 1, 1, 1)
    return start, date(year, month + 1, 1)


def _quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    if quarter == 4:
        return start, date(year + 1, 1, 1)
    return start, date(year, start_month + 3, 1)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + delta
    return index // 12, index % 12 + 1


def parse_analysis_period(message: str, today: date | None = None) -> AnalysisPeriod:
    """Parse supported natural-language periods without generating SQL."""
    current = today or date.today()
    text = (message or "").lower()
    quarter = (current.month - 1) // 3 + 1
    wants_compare = "同比" in text or (
        any(word in text for word in ("对比", "比较"))
        and any(word in text for word in ("去年", "同期"))
    )

    if wants_compare:
        if "季度" in text or "季" in text:
            start, end = _quarter_bounds(current.year, quarter)
            compare_start, compare_end = _quarter_bounds(current.year - 1, quarter)
            return AnalysisPeriod(
                "current_quarter",
                "本季度",
                start,
                end,
                compare_start,
                compare_end,
                "去年同期",
            )
        if any(word in text for word in ("全年", "年度", "今年", "本年")):
            return AnalysisPeriod(
                "current_year",
                "今年",
                date(current.year, 1, 1),
                date(current.year + 1, 1, 1),
                date(current.year - 1, 1, 1),
                date(current.year, 1, 1),
                "去年",
            )
        start, end = _month_bounds(current.year, current.month)
        compare_start, compare_end = _month_bounds(current.year - 1, current.month)
        return AnalysisPeriod(
            "current_month",
            "本月",
            start,
            end,
            compare_start,
            compare_end,
            "去年同期",
        )

    if any(word in text for word in ("去年同期", "往年同期")) or (
        any(word in text for word in ("去年", "往年")) and any(word in text for word in ("这个季度", "本季度", "这个月", "本月"))
    ):
        if any(word in text for word in ("季度", "季")):
            start, end = _quarter_bounds(current.year - 1, quarter)
            return AnalysisPeriod("last_year_same_quarter", "去年同期季度", start, end)
        start, end = _month_bounds(current.year - 1, current.month)
        return AnalysisPeriod("last_year_same_month", "去年同期月份", start, end)

    if any(word in text for word in ("上季度", "上一季度")):
        target_q = quarter - 1
        target_year = current.year
        if target_q == 0:
            target_q, target_year = 4, target_year - 1
        start, end = _quarter_bounds(target_year, target_q)
        return AnalysisPeriod("previous_quarter", "上季度", start, end)

    if any(word in text for word in ("本季度", "这个季度", "当前季度", "当季")) or (
        wants_compare and "季度" in text
    ):
        start, end = _quarter_bounds(current.year, quarter)
        compare_start, compare_end = _quarter_bounds(current.year - 1, quarter)
        return AnalysisPeriod(
            "current_quarter",
            "本季度",
            start,
            end,
            compare_start if wants_compare else None,
            compare_end if wants_compare else None,
            "去年同期" if wants_compare else "",
        )

    if any(word in text for word in ("上个月", "上月")):
        year, month = _shift_month(current.year, current.month, -1)
        start, end = _month_bounds(year, month)
        return AnalysisPeriod("previous_month", "上月", start, end)

    if any(word in text for word in ("本月", "这个月", "当前月", "当月")) or wants_compare:
        start, end = _month_bounds(current.year, current.month)
        compare_start, compare_end = _month_bounds(current.year - 1, current.month)
        return AnalysisPeriod(
            "current_month",
            "本月",
            start,
            end,
            compare_start if wants_compare else None,
            compare_end if wants_compare else None,
            "去年同期" if wants_compare else "",
        )

    if any(word in text for word in ("去年", "上一年")):
        return AnalysisPeriod(
            "previous_year",
            "去年",
            date(current.year - 1, 1, 1),
            date(current.year, 1, 1),
        )

    if any(word in text for word in ("今年", "本年", "当前年度")):
        return AnalysisPeriod(
            "current_year",
            "今年",
            date(current.year, 1, 1),
            date(current.year + 1, 1, 1),
        )

    return AnalysisPeriod("all", "全部历史")


def period_sql_filter(
    period: AnalysisPeriod,
    column: str = "start_date",
    prefix: str = "period",
) -> tuple[str, dict[str, Any]]:
    if not period.start or not period.end_exclusive:
        return "", {}
    return (
        f"{column} >= :{prefix}_start AND {column} < :{prefix}_end",
        {
            f"{prefix}_start": period.start.isoformat(),
            f"{prefix}_end": period.end_exclusive.isoformat(),
        },
    )


ROLE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "user": {
        "scope": "personal",
        "scope_label": "仅当前账号的旅行计划",
        "permissions": ["个人画像", "个人目的地统计", "个人预算趋势", "个人预测", "本人计划明细"],
        "restrictions": ["不能查看其他用户或全站汇总", "不能查看审计日志和敏感字段"],
        "quick_prompts": [
            "分析我的旅行兴趣画像",
            "统计我本季度最常去的目的地",
            "对比我的本月预算和去年同期",
            "展示我的月度预算趋势",
        ],
    },
    "manager": {
        "scope": "global_aggregate",
        "scope_label": "全站匿名汇总，不含用户或计划明细",
        "permissions": ["全站目的地汇总", "预算与月份趋势", "旅行者类型分布", "匿名热度预测"],
        "restrictions": ["不能查看逐条计划、用户标识、审计日志或敏感字段"],
        "quick_prompts": [
            "统计本季度最热门的旅行目的地",
            "对比本月和去年同期的目的地热度",
            "展示最近一年的月度预算趋势",
            "预测下个月热门目的地",
        ],
    },
    "admin": {
        "scope": "global",
        "scope_label": "全站汇总、非敏感计划明细和审计数据",
        "permissions": ["全站汇总", "非敏感计划明细", "趋势与预测", "数据质量", "审计日志"],
        "restrictions": ["智能分析仍不返回手机号、邮箱、联系人或认证秘密"],
        "quick_prompts": [
            "统计本季度所有人的旅行去向",
            "对比本月和去年同期的热门目的地",
            "检查当前数据质量和来源分布",
            "查看最近的智能分析审计日志",
        ],
    },
}


def get_role_capabilities(role: str) -> dict[str, Any]:
    normalized = normalize_role(role)
    base = ROLE_CAPABILITIES.get(normalized, ROLE_CAPABILITIES["user"])
    return {"role": normalized, **base}


def _safe_ratio(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def get_data_status(user_id: str, role: str) -> dict[str, Any]:
    """Return quality information only for the rows visible to the role."""
    normalized = normalize_role(role)
    suffix, params = scope_user_filter(normalized, user_id)
    where = "WHERE 1=1" + suffix
    summary = fetch_one(
        f"""SELECT COUNT(*) AS visible_plans,
                   COUNT(DISTINCT user_id) AS visible_users,
                   COUNT(DISTINCT destination) AS destinations,
                   MIN(start_date) AS min_date,
                   MAX(start_date) AS max_date,
                   SUM(CASE WHEN budget IS NULL OR budget = 0 THEN 1 ELSE 0 END) AS missing_budget,
                   SUM(CASE WHEN actual_cost IS NULL THEN 1 ELSE 0 END) AS missing_actual_cost,
                   SUM(CASE WHEN plan_json IS NULL OR plan_json = '{{}}' THEN 1 ELSE 0 END) AS missing_plan_json
            FROM travel_plans {where}""",
        params,
    ) or {}
    sources = fetch_all(
        f"""SELECT source, COUNT(*) AS count
            FROM travel_plans {where}
            GROUP BY source ORDER BY count DESC""",
        params,
    )

    visible_plans = int(summary.get("visible_plans") or 0)
    missing_budget = int(summary.get("missing_budget") or 0)
    missing_actual = int(summary.get("missing_actual_cost") or 0)
    missing_json = int(summary.get("missing_plan_json") or 0)
    min_date = _parse_iso_date(summary.get("min_date"))
    max_date = _parse_iso_date(summary.get("max_date"))
    span_days = (max_date - min_date).days + 1 if min_date and max_date else 0
    covered_months = max(1, (span_days + 29) // 30) if span_days else 0
    synthetic_count = sum(
        int(item.get("count") or 0)
        for item in sources
        if "synthetic" in str(item.get("source") or "").lower()
    )

    # User-facing status stays light: only empty-range hints.
    # Field completeness / synthetic-data caveats stay internal (quality metrics).
    warnings: list[str] = []
    if visible_plans == 0:
        warnings.append("当前还没有可分析的旅行计划，生成或导入行程后再试。")
    elif visible_plans < 3:
        warnings.append("数据还比较少，结论会更偏单次事实，样本增加后趋势会更稳。")

    capabilities = get_role_capabilities(normalized)
    return {
        "role": normalized,
        "scope": capabilities["scope"],
        "scope_label": capabilities["scope_label"],
        "visible_plans": visible_plans,
        "visible_users": int(summary.get("visible_users") or 0),
        "destinations": int(summary.get("destinations") or 0),
        "date_range": {
            "min": min_date.isoformat() if min_date else None,
            "max": max_date.isoformat() if max_date else None,
            "span_days": span_days,
            "covered_months": covered_months,
        },
        "sources": sources,
        "quality": {
            "budget_completeness": round(1 - _safe_ratio(missing_budget, visible_plans), 4),
            "actual_cost_completeness": round(1 - _safe_ratio(missing_actual, visible_plans), 4),
            "plan_json_completeness": round(1 - _safe_ratio(missing_json, visible_plans), 4),
            "synthetic_ratio": _safe_ratio(synthetic_count, visible_plans),
        },
        "sufficient_for": {
            "facts": visible_plans >= 1,
            "trend": visible_plans >= 3 and covered_months >= 2,
            "prediction": visible_plans >= 12 and covered_months >= 3,
            "year_over_year": span_days >= 365,
        },
        "warnings": warnings,
    }


def get_analysis_metadata(
    user_id: str,
    role: str,
    period: AnalysisPeriod,
    table: list[dict[str, Any]],
) -> dict[str, Any]:
    status = get_data_status(user_id, role)
    suffix, params = scope_user_filter(role, user_id)
    clauses = ["1=1"]
    if suffix:
        clauses.append(suffix.replace(" AND ", "", 1))
    time_clause, time_params = period_sql_filter(period)
    if time_clause:
        clauses.append(time_clause)
        params.update(time_params)
    sample = fetch_one(
        f"SELECT COUNT(*) AS count FROM travel_plans WHERE {' AND '.join(clauses)}",
        params,
    ) or {}
    sample_size = int(sample.get("count") or 0)
    warnings = list(status["warnings"])
    if period.key != "all" and sample_size == 0:
        warnings.insert(0, f"{period.label}没有符合条件的旅行计划。")
    elif period.key != "all" and sample_size < 3:
        warnings.insert(0, f"{period.label}样本仅 {sample_size} 条，结论仅供参考。")
    return {
        "scope": status["scope"],
        "scope_label": status["scope_label"],
        "period": period.to_dict(),
        "sample_size": sample_size,
        "row_count": len(table),
        "data_quality": status["quality"],
        "sufficient_for": status["sufficient_for"],
        "warnings": list(dict.fromkeys(warnings)),
    }
