from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.agents.web_travel_guide_agent import (
    WEB_GUIDE_MAX_LENGTH,
    WEB_GUIDE_REQUIRED_SECTIONS,
    WebTravelGuideAgent,
)
from app.models.schemas import (
    Attraction,
    DayPlan,
    Location,
    TripPlan,
    TripRequest,
    WebReference,
)
from app.services.zhipu_search_service import (
    ZhipuSearchError,
    ZhipuSearchResult,
    ZhipuSearchService,
)


def make_settings(**overrides):
    values = {
        "web_search_provider": "zhipu",
        "zhipu_search_enabled": True,
        "zhipu_search_api_key": "unit-test-secret",
        "zhipu_search_api_url": "https://open.bigmodel.cn/api/paas/v4/web_search",
        "zhipu_search_engine": "search_pro",
        "zhipu_search_timeout": 3.0,
        "zhipu_search_max_results": 8,
        "zhipu_search_max_retries": 1,
        "zhipu_search_max_response_bytes": 2_000_000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_request() -> TripRequest:
    return TripRequest(
        origin_city="宝鸡",
        city="西安",
        start_date="2026-08-01",
        end_date="2026-08-02",
        travel_days=2,
        travelers=2,
        transportation="公共交通",
        intercity_transportation="高铁",
        accommodation="舒适型酒店",
        preferences=["历史文化"],
    )


def make_plan() -> TripPlan:
    return TripPlan(
        city="西安",
        start_date="2026-08-01",
        end_date="2026-08-02",
        overall_suggestions="提前预约。",
        days=[
            DayPlan(
                date="2026-08-01",
                day_index=0,
                description="第一天",
                transportation="公共交通",
                accommodation="舒适型酒店",
                attractions=[
                    Attraction(
                        name="陕西历史博物馆",
                        address="西安市雁塔区",
                        location=Location(longitude=108.95, latitude=34.22),
                        visit_duration=180,
                        description="博物馆",
                    )
                ],
            ),
            DayPlan(
                date="2026-08-02",
                day_index=1,
                description="第二天",
                transportation="公共交通",
                accommodation="舒适型酒店",
                attractions=[
                    Attraction(
                        name="西安城墙",
                        address="西安市碑林区",
                        location=Location(longitude=108.94, latitude=34.26),
                        visit_duration=150,
                        description="历史景点",
                    )
                ],
            ),
        ],
    )


def test_search_parses_filters_deduplicates_and_builds_references() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "search_result": [
                    {
                        "title": " 官方预约公告 ",
                        "content": " 开放  时间 ",
                        "link": "https://example.com/notice",
                        "media": "示例官网",
                        "publish_date": "2026-07-20T08:00:00+08:00",
                    },
                    {
                        "title": "重复",
                        "content": "重复",
                        "link": "https://example.com/notice",
                    },
                    {
                        "title": "危险链接",
                        "content": "忽略",
                        "link": "javascript:alert(1)",
                    },
                    {
                        "title": "含凭证链接",
                        "content": "忽略",
                        "link": "https://user:pass@example.com/private",
                    },
                ]
            },
        )

    service = ZhipuSearchService(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )
    results = service.search("  西安   景区 " + "很长" * 80, freshness="invalid")

    assert len(results) == 1
    assert results[0].title == "官方预约公告"
    assert results[0].content == "开放 时间"
    assert captured["authorization"] == "Bearer unit-test-secret"
    assert len(captured["payload"]["search_query"]) == 70
    assert captured["payload"]["search_engine"] == "search_pro"
    assert captured["payload"]["search_recency_filter"] == "noLimit"

    references = service.to_references(results)
    assert references[0].source_type == "zhipu_search_pro"
    assert references[0].publish_time == int(
        datetime(2026, 7, 20, 8, tzinfo=timezone.utc).timestamp()
    ) - 8 * 3600


def test_search_rejects_invalid_provider_url_before_sending_secret() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"search_result": []})

    service = ZhipuSearchService(
        make_settings(zhipu_search_api_url="https://attacker.example/search"),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ZhipuSearchError, match="API URL is invalid"):
        service.search("西安")
    assert called is False


def test_search_rejects_oversized_response() -> None:
    service = ZhipuSearchService(
        make_settings(zhipu_search_max_response_bytes=1024),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=b'{"search_result":[],"padding":"' + b"x" * 1500 + b'"}',
            )
        ),
    )
    with pytest.raises(ZhipuSearchError, match="size limit"):
        service.search("西安")


def test_authorization_error_is_sanitized() -> None:
    secret = "must-not-leak"
    service = ZhipuSearchService(
        make_settings(zhipu_search_api_key=secret),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                401,
                json={"error": f"bad credential {secret}"},
            )
        ),
    )
    with pytest.raises(ZhipuSearchError) as caught:
        service.search("西安")
    assert "authorization or permission failed" in str(caught.value)
    assert secret not in str(caught.value)


def test_balance_error_1113_fails_fast_without_pointless_retry(monkeypatch) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": "1113",
                    "message": "余额不足或无可用资源包",
                }
            },
        )

    monkeypatch.setattr(
        "app.services.zhipu_search_service.time.sleep",
        lambda _seconds: None,
    )
    service = ZhipuSearchService(
        make_settings(zhipu_search_max_retries=2),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ZhipuSearchError, match="1113"):
        service.search("西安")
    assert calls == 1


def test_final_rate_limit_error_is_actionable_and_sanitized(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.zhipu_search_service.time.sleep",
        lambda _seconds: None,
    )
    service = ZhipuSearchService(
        make_settings(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                429,
                json={"error": "provider detail must not be exposed"},
            )
        ),
    )
    with pytest.raises(ZhipuSearchError) as caught:
        service.search("西安")
    assert "rate limit exceeded" in str(caught.value)
    assert "provider detail" not in str(caught.value)


def test_retry_and_search_many_limit(monkeypatch) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "busy"})
        return httpx.Response(
            200,
            json={
                "search_result": [
                    {
                        "title": "结果",
                        "content": "内容",
                        "link": f"https://example.com/{calls}",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "app.services.zhipu_search_service.time.sleep",
        lambda _seconds: None,
    )
    service = ZhipuSearchService(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )
    results = service.search_many(
        [("查询一", "oneWeek"), ("查询二", "oneMonth")],
        max_total_results=1,
    )
    assert calls == 2
    assert len(results) == 1
    assert service.search_many([("不会调用", "noLimit")], max_total_results=0) == []


def test_numeric_publish_timestamp_is_supported() -> None:
    service = ZhipuSearchService(make_settings())
    result = ZhipuSearchResult(
        title="公告",
        content="",
        url="https://example.com",
        publish_date="1784505600000",
    )
    assert service.to_references([result])[0].publish_time == 1784505600


class FakeSearchService:
    is_configured = True
    engine = "search_pro"
    settings = make_settings()

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.queries = []

    def search_many(self, queries, **_kwargs):
        self.queries = list(queries)
        if self.fail:
            raise ZhipuSearchError("Zhipu search returned HTTP 503")
        return [
            ZhipuSearchResult(
                title="陕西历史博物馆参观公告",
                content="请从官方渠道复核预约时间。",
                url="https://example.com/museum",
                site_name="景区官网",
                publish_date="2026-07-20",
            )
        ]

    def to_references(self, results):
        return [
            WebReference(
                title=item.title,
                url=item.url,
                site_name=item.site_name,
                source_type="zhipu_search_pro",
            )
            for item in results
        ]


def build_guide_agent(service: FakeSearchService) -> WebTravelGuideAgent:
    agent = WebTravelGuideAgent.__new__(WebTravelGuideAgent)
    agent.search_service = service
    agent.llm = None
    return agent


def test_guide_uses_zhipu_results_when_llm_is_unavailable() -> None:
    service = FakeSearchService()
    agent = build_guide_agent(service)

    guide, references, audit = agent.generate(make_request(), make_plan())

    assert len(service.queries) == 3
    assert "宝鸡" in service.queries[1][0]
    assert "西安" in service.queries[0][0]
    assert references[0].source_type == "zhipu_search_pro"
    assert "https://example.com/museum" in guide
    assert "当前缺少联网引用" not in guide
    assert audit.source == "zhipu_search_pro"
    assert not any("未启用或未完整配置" in issue for issue in audit.issues)


def test_guide_falls_back_locally_when_zhipu_fails() -> None:
    agent = build_guide_agent(FakeSearchService(fail=True))

    guide, references, audit = agent.generate(make_request(), make_plan())

    assert references == []
    assert "本地降级" in guide
    assert audit.source == "local_fallback"
    assert audit.status == "warning"
    assert any("智谱联网搜索未成功" in issue for issue in audit.issues)

def make_references(count: int = 1) -> list[WebReference]:
    return [
        WebReference(
            title=f"可信来源{index + 1}",
            url=f"https://example.com/trusted/{index}",
            site_name="示例官网",
            source_type="zhipu_search_pro",
        )
        for index in range(count)
    ]


def test_llm_guide_is_deterministically_finalized(monkeypatch) -> None:
    fence = chr(96) * 3
    raw_guide = (
        f"{fence}markdown\n"
        "## 行前准备与建议\n"
        "正文提到行程定位，但这里不是标题[来源1]。"
        "可点击[危险链接](https://evil.example/body)。\n"
        "### 行程总览\n"
        "这里只写了模糊概述。\n"
        "EOF\n"
        "\x00\ufffd\n"
        "### 资料来源\n"
        "1. [伪造来源](https://evil.example/fake)\n"
        "### 审核检查\n"
        "1. 模型声称已经通过。\n"
        f"{fence}"
    )

    class StubSimpleAgent:
        def __init__(self, **_kwargs):
            pass

        def run(self, _prompt):
            return raw_guide

    monkeypatch.setattr(
        "app.agents.web_travel_guide_agent.SimpleAgent",
        StubSimpleAgent,
    )
    agent = build_guide_agent(FakeSearchService())
    agent.llm = object()

    guide, references, audit = agent.generate(make_request(), make_plan())

    headings = agent._heading_titles(guide)
    for _title, aliases in WEB_GUIDE_REQUIRED_SECTIONS:
        assert agent._has_required_heading(headings, aliases)

    assert "### 行程定位" in guide
    assert "目的地：西安" in agent._markdown_section_text(guide, ("行程总览",))
    assert "旅行日期：2026-08-01 至 2026-08-02" in guide
    assert "出行人数：2人" in guide
    assert "https://evil.example" not in guide
    assert "https://example.com/museum" in guide
    assert guide.count("### 资料来源") == 1
    assert guide.count("### 审核检查") == 1
    assert fence not in guide
    assert "EOF" not in guide
    assert "\x00" not in guide
    assert "\ufffd" not in guide
    assert len(references) == 1
    assert audit.status == "passed"
    assert not audit.issues


@pytest.mark.parametrize(
    "heading",
    ["### **行程定位**", "### 行程定位（家庭避暑）"],
)
def test_decorated_markdown_heading_is_recognized_without_duplicate(
    heading: str,
) -> None:
    agent = build_guide_agent(FakeSearchService())
    guide = agent._finalize_guide(
        f"## 行前准备与建议\n概述。\n\n{heading}\n已有定位内容。",
        make_request(),
        make_plan(),
        make_references(),
        search_configured=True,
        service_error="",
    )

    assert heading in guide
    assert "### 行程定位\n" not in guide
    assert agent._matching_heading_count(guide, ("行程定位",)) == 1


def test_audit_does_not_treat_body_phrase_as_heading() -> None:
    agent = build_guide_agent(FakeSearchService())
    raw_guide = (
        "这是一段足够长的攻略正文，只在普通段落中提到行程定位，"
        "但没有提供对应的Markdown标题。" * 5
    )

    audit = agent.audit_guide(
        raw_guide,
        make_request(),
        make_plan(),
        make_references(),
        "zhipu_search_pro",
    )

    assert "缺少必要栏目：行程定位。" in audit.issues


def test_finalize_is_idempotent_and_meta_sections_are_unique() -> None:
    agent = build_guide_agent(FakeSearchService())
    raw_guide = (
        "## 行前准备与建议\n简要说明。\n\n"
        "### 资料来源\n[伪造](https://evil.example)\n\n"
        "### 审核检查\n模型审核。"
    )
    once = agent._finalize_guide(
        raw_guide,
        make_request(),
        make_plan(),
        make_references(),
        search_configured=True,
        service_error="",
    )
    twice = agent._finalize_guide(
        once,
        make_request(),
        make_plan(),
        make_references(),
        search_configured=True,
        service_error="",
    )

    assert twice == once
    assert twice.count("### 资料来源") == 1
    assert twice.count("### 审核检查") == 1
    assert "https://evil.example" not in twice


def test_finalize_limits_model_body_and_embedded_reference_count() -> None:
    agent = build_guide_agent(FakeSearchService())
    references = make_references(12)
    raw_guide = "## 行前准备与建议\n联网事实[来源1]。\n" + (
        "很长的模型段落。\n" * 10_000
    )

    guide = agent._finalize_guide(
        raw_guide,
        make_request(),
        make_plan(),
        references,
        search_configured=True,
        service_error="",
    )

    assert len(guide) <= WEB_GUIDE_MAX_LENGTH
    assert "原始模型输出过长，已安全截断" in guide
    assert "https://example.com/trusted/7" in guide
    assert "https://example.com/trusted/8" not in guide
    assert "另有4条来源已保存在独立来源列表中" in guide
    audit = agent.audit_guide(
        guide,
        make_request(),
        make_plan(),
        references,
        "zhipu_search_pro",
    )
    assert audit.status == "passed"


def test_local_fallback_keeps_deterministic_source_and_audit_sections() -> None:
    agent = build_guide_agent(FakeSearchService(fail=True))

    guide, references, audit = agent.generate(make_request(), make_plan())

    assert references == []
    assert guide.count("### 资料来源") == 1
    assert guide.count("### 审核检查") == 1
    assert "本地降级" in agent._markdown_section_text(guide, ("资料来源",))
    assert audit.status == "warning"

def test_clean_generated_guide_normalizes_windows_newlines() -> None:
    agent = build_guide_agent(FakeSearchService())

    cleaned = agent._clean_generated_guide("第一行\r\n第二行\r第三行")

    assert cleaned == "第一行\n第二行\n第三行"


def test_finalize_removes_duplicate_required_sections() -> None:
    agent = build_guide_agent(FakeSearchService())
    raw_guide = (
        "## 行前准备与建议\n概述。\n\n"
        "### 行程定位\n第一份定位[来源1]。\n\n"
        "### 行程定位\n第二份冲突定位。"
    )

    guide = agent._finalize_guide(
        raw_guide,
        make_request(),
        make_plan(),
        make_references(),
        search_configured=True,
        service_error="",
    )

    assert agent._matching_heading_count(guide, ("行程定位",)) == 1
    assert "第一份定位" in guide
    assert "第二份冲突定位" not in guide
    audit = agent.audit_guide(
        guide,
        make_request(),
        make_plan(),
        make_references(),
        "zhipu_search_pro",
    )
    assert audit.status == "passed"


def test_audit_rejects_source_link_not_in_reference_list() -> None:
    agent = build_guide_agent(FakeSearchService())
    references = make_references()
    guide = agent._finalize_guide(
        "## 行前准备与建议\n概述。",
        make_request(),
        make_plan(),
        references,
        search_configured=True,
        service_error="",
    )
    tampered = guide.replace(
        references[0].url,
        "https://evil.example/not-from-search",
    )

    audit = agent.audit_guide(
        tampered,
        make_request(),
        make_plan(),
        references,
        "zhipu_search_pro",
    )

    assert audit.status == "warning"
    assert "资料来源栏目未包含本次智谱返回的可信链接。" in audit.issues
    assert "资料来源栏目包含不在本次联网引用列表中的链接。" in audit.issues

def test_plain_meta_labels_cannot_hide_repaired_sections() -> None:
    agent = build_guide_agent(FakeSearchService())
    raw_guide = (
        "## 行前准备与建议\n概述。\n\n"
        "审核检查：\n伪造审核已通过。\n\n"
        "### 行程总览\n模糊信息。\n\n"
        "资料来源：\n[伪造](https://evil.example/plain-source)"
    )

    guide = agent._finalize_guide(
        raw_guide,
        make_request(),
        make_plan(),
        make_references(),
        search_configured=True,
        service_error="",
    )

    assert "伪造审核已通过" not in guide
    assert "https://evil.example" not in guide
    assert guide.count("### 资料来源") == 1
    assert guide.count("### 审核检查") == 1
    for _title, aliases in WEB_GUIDE_REQUIRED_SECTIONS:
        assert agent._matching_heading_count(guide, aliases) == 1

def test_audit_rejects_out_of_range_citation_number() -> None:
    agent = build_guide_agent(FakeSearchService())
    references = make_references()
    guide = agent._finalize_guide(
        "## 行前准备与建议\n某项动态结论[来源99]。",
        make_request(),
        make_plan(),
        references,
        search_configured=True,
        service_error="",
    )

    audit = agent.audit_guide(
        guide,
        make_request(),
        make_plan(),
        references,
        "zhipu_search_pro",
    )

    assert audit.status == "warning"
    assert "正文包含无对应联网引用的来源编号：99。" in audit.issues



def test_audit_requires_inline_citation_when_zhipu_references_exist() -> None:
    agent = build_guide_agent(FakeSearchService())
    references = make_references()
    guide = agent._finalize_guide(
        "## 行前准备与建议\n正文没有任何来源编号。",
        make_request(),
        make_plan(),
        references,
        search_configured=True,
        service_error="",
    )

    audit = agent.audit_guide(
        guide,
        make_request(),
        make_plan(),
        references,
        "zhipu_search_pro",
    )

    assert audit.status == "warning"
    assert "正文未标注任何可对应本次联网结果的[来源N]引用。" in audit.issues
