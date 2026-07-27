"""Tests for web travel guide agent verification failure graceful degradation."""

from __future__ import annotations

from app.agents.web_travel_guide_agent import get_web_travel_guide_agent
from app.models.schemas import DayPlan, TripPlan, TripRequest, WebReference


def _base_request() -> TripRequest:
    return TripRequest(
        origin_city="上海",
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        travel_days=2,
        travelers=2,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=[],
        free_text_input="",
    )


def _base_plan() -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2030-08-02",
        end_date="2030-08-03",
        overall_suggestions="建议游玩",
        days=[
            DayPlan(
                date="2030-08-02",
                day_index=0,
                description="d1",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            ),
            DayPlan(
                date="2030-08-03",
                day_index=1,
                description="d2",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
            ),
        ],
    )


def _structured_guide(*, with_source_link: str | None = None) -> str:
    source_block = (
        f"### 资料来源\n1. [杭州旅游局]({with_source_link})\n"
        if with_source_link
        else "### 资料来源\n1. 本地降级，无联网引用。\n"
    )
    citation = "[来源1]" if with_source_link else ""
    return (
        "## 行前准备与建议\n"
        f"提前核对预约与天气{citation}。\n\n"
        "### 预约要求\n"
        "1. 核心景区提前预约。\n\n"
        "### 穿衣建议\n"
        "根据天气携带衣物。\n\n"
        "### 物品准备\n"
        "1. 带好身份证件和日常用品。\n\n"
        "### 其他注意事项\n"
        "1. 遵守景区规定。\n\n"
        "### 行程总览\n"
        "目的地：杭州\n"
        "旅行日期：2030-08-02 至 2030-08-03\n"
        "旅行总天数：2天\n"
        "出行人数：2人\n\n"
        "### 核心景点\n"
        "1. 西湖\n"
        "2. 灵隐寺\n\n"
        "### 跨市交通方案\n"
        "上海至杭州高铁。\n\n"
        "### 入住酒店\n"
        "经济型酒店。\n\n"
        "### 总预算估算\n"
        "预计800元。\n\n"
        "### 行程定位\n"
        "适合休闲打卡。\n\n"
        f"{source_block}\n"
        "### 审核检查\n"
        "1. 已完成结构检查。\n"
    )


def test_web_audit_timeout_degrades_gracefully():
    agent = get_web_travel_guide_agent()
    req = _base_request()
    plan = _base_plan()

    audit = agent.audit_guide(
        guide="简短降级攻略",
        request=req,
        trip_plan=plan,
        references=[],
        source="local_fallback",
        service_error="timeout after 15s",
    )

    assert audit.status == "warning"
    assert audit.audit_level == "offline_fallback"
    assert any("超时" in s or "降级" in s for s in audit.issues + audit.suggestions)


def test_web_audit_missing_key_degrades_gracefully():
    agent = get_web_travel_guide_agent()
    req = _base_request()
    plan = _base_plan()

    audit = agent.audit_guide(
        guide="无Key降级攻略",
        request=req,
        trip_plan=plan,
        references=[],
        source="local_fallback",
        service_error="",
    )

    assert audit.status == "warning"
    assert audit.audit_level == "offline_fallback"
    assert any(
        "未配置" in s or "未启用" in s or "智谱" in s
        for s in audit.issues + audit.suggestions
    )


def test_web_audit_success_returns_format_only_or_semantic_verified():
    agent = get_web_travel_guide_agent()
    req = _base_request()
    plan = _base_plan()

    ref = WebReference(
        title="杭州旅游局",
        url="https://example.com",
        site_name="官网",
        source_type="zhipu_search_pro",
    )
    guide_text = _structured_guide(with_source_link=ref.url)

    audit = agent.audit_guide(
        guide=guide_text,
        request=req,
        trip_plan=plan,
        references=[ref],
        source="zhipu_search_pro",
    )

    assert audit.status == "passed"
    # External web search is advisory / format-checked, not map semantic verification.
    assert audit.audit_level == "format_only"
    assert audit.audit_level != "semantic_verified"
