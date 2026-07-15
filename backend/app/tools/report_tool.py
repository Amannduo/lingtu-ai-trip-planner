"""Evidence-based Chinese formatter for analytics responses."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _number(row: dict, *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _display_number(value: float | None) -> str:
    if value is None:
        return "未知"
    return str(int(value)) if value.is_integer() else f"{value:.1f}"


def summarize_table(
    intent: str,
    table: list[dict],
    extra_reason: str = "",
    analysis: dict[str, Any] | None = None,
) -> str:
    meta = analysis or {}
    period = (meta.get("period") or {}).get("label", "全部历史")
    sample_size = meta.get("sample_size")
    scope_label = meta.get("scope_label", "")
    evidence = []
    if scope_label:
        evidence.append(scope_label)
    if sample_size is not None:
        evidence.append(f"{period}样本 {sample_size} 条")
    evidence_text = "；".join(evidence)

    if not table:
        base = "没有查询到符合当前权限和时间范围的数据。"
        warnings = meta.get("warnings") or []
        return " ".join(part for part in (extra_reason, base, warnings[0] if warnings else "") if part)

    if "周期" in table[0] and "目的地" in table[0]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in table:
            groups[str(row.get("周期") or "未命名周期")].append(row)
        conclusions = []
        for label, rows in groups.items():
            top = max(rows, key=lambda row: _number(row, "计划数", "count") or 0)
            conclusions.append(
                f"{label}最热门的是{top.get('目的地')}（{_display_number(_number(top, '计划数', 'count'))}条）"
            )
        base = "；".join(conclusions) + "。"
    else:
        first = table[0]
        destination = first.get("目的地") or first.get("城市") or first.get("city")
        count = _number(first, "计划数", "相似用户计划数", "用户数", "count")
        budget = _number(first, "平均预算", "avg_budget")

        if intent == "city_rank":
            base = f"{period}最热门的目的地是{destination}，共{_display_number(count)}条计划。"
        elif intent == "avg_budget":
            base = f"{period}平均预算最高的是{destination}，约{_display_number(budget)}元。"
        elif intent == "budget_trend":
            start = _number(table[0], "平均预算", "avg_budget")
            end = _number(table[-1], "平均预算", "avg_budget")
            if start is not None and end is not None and start:
                change = (end - start) / start * 100
                direction = "上升" if change >= 0 else "下降"
                base = f"月度平均预算从{_display_number(start)}元变为{_display_number(end)}元，{direction}{abs(change):.1f}%。"
            else:
                base = "已按月份整理预算趋势，当前样本不足以计算稳定变化率。"
        elif intent == "profile":
            base = (
                f"当前账号属于“{first.get('旅行者类型') or '待积累'}”类型，"
                f"已有{_display_number(_number(first, '计划数'))}条计划，"
                f"平均预算约{_display_number(_number(first, '平均预算'))}元。"
            )
        elif intent == "traveler_type_distribution":
            base = f"人数最多的旅行者类型是{first.get('旅行者类型')}，共{_display_number(count)}人。"
        elif intent == "prediction":
            sufficient = (meta.get("sufficient_for") or {}).get("prediction", False)
            if sufficient:
                base = f"按历史月度计划量加权，下月热度最高的目的地是{destination}。"
            else:
                base = f"当前加权结果首位是{destination}，但样本或月份覆盖不足，不能作为可靠预测。"
        elif intent == "recommendation":
            base = f"结合当前账号画像，优先推荐{destination}；推荐依据来自匿名聚合记录。"
        elif intent == "data_quality":
            base = f"当前可见数据以“{first.get('数据来源')}”来源为主，共{_display_number(count)}条。"
        elif intent == "all_plan_detail":
            base = f"已返回权限范围内最近 {len(table)} 条非敏感旅行计划明细。"
        elif intent == "audit_log":
            base = f"已返回最近 {len(table)} 条智能分析审计记录。"
        else:
            base = f"已从当前权限范围查询到 {len(table)} 组分析结果。"

    parts = [part for part in (extra_reason, base, f"统计口径：{evidence_text}。" if evidence_text else "") if part]
    warnings = meta.get("warnings") or []
    if warnings:
        parts.append(f"数据提示：{warnings[0]}")
    return " ".join(parts)
