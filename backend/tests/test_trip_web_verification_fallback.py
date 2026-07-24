"""Tests for web travel guide agent verification failure graceful degradation."""

from __future__ import annotations

from app.agents.web_travel_guide_agent import (
    WebReference,
    get_web_travel_guide_agent,
)
from app.models.schemas import DayPlan, TripPlan, TripRequest


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
        service_error="VOLCENGINE_AGENT_API_KEY timeout after 120s",
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
    assert any("未配置" in s or "凭证" in s for s in audit.issues + audit.suggestions)


def test_web_audit_success_returns_format_only_or_semantic_verified():
    agent = get_web_travel_guide_agent()
    req = _base_request()
    plan = _base_plan()

    guide_text = (
        "行前准备：提前预约。\n"
        "预约要求：核心景区提前预约。\n"
        "穿衣建议：根据天气携带衣物。\n"
        "物品准备：带好身份证件和日常用品。\n"
        "其他注意事项：遵守景区规定。\n"
        "行程总览：杭州2天游。\n"
        "核心景点：西湖、灵隐寺。\n"
        "跨市交通：上海至杭州高铁。\n"
        "入住酒店：经济型酒店。\n"
        "总预算：预计800元。\n"
        "行程定位：适合休闲打卡。\n"
        "2030年08月02日至2030年08月03日前往杭州。"
    )
    ref = WebReference(title="杭州旅游局", url="https://example.com", site_name="官网", summary="预约规则")

    audit = agent.audit_guide(
        guide=guide_text,
        request=req,
        trip_plan=plan,
        references=[ref],
        source="volcengine_web_agent",
    )

    assert audit.status == "passed"
    assert audit.audit_level == "format_only"
