"""Safe client for Zhipu Web Search API (search_pro)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional
from urllib.parse import urlsplit

import httpx

from ..config import get_settings
from ..models.schemas import WebReference


class ZhipuSearchError(RuntimeError):
    """A sanitized provider error safe to expose in diagnostics."""


@dataclass(frozen=True)
class ZhipuSearchResult:
    title: str
    content: str
    url: str
    site_name: str = ""
    publish_date: str = ""


class ZhipuSearchService:
    ALLOWED_ENGINES = {
        "search_std",
        "search_pro",
        "search_pro_sogou",
        "search_pro_quark",
    }
    ALLOWED_FRESHNESS = {"oneDay", "oneWeek", "oneMonth", "oneYear", "noLimit"}

    def __init__(self, settings=None, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings or get_settings()
        self._transport = transport

    @property
    def is_configured(self) -> bool:
        return bool(
            str(self.settings.web_search_provider).strip().lower() == "zhipu"
            and self.settings.zhipu_search_enabled
            and str(self.settings.zhipu_search_api_key).strip()
        )

    @property
    def engine(self) -> str:
        value = str(self.settings.zhipu_search_engine or "search_pro")
        return value if value in self.ALLOWED_ENGINES else "search_pro"

    def search(
        self,
        query: str,
        *,
        freshness: str = "noLimit",
        user_id: str = "lingtu-travel",
    ) -> list[ZhipuSearchResult]:
        if not self.is_configured:
            raise ZhipuSearchError("Zhipu search is not configured")

        normalized_query = " ".join(str(query or "").split())[:70]
        if not normalized_query:
            return []
        normalized_freshness = (
            freshness if freshness in self.ALLOWED_FRESHNESS else "noLimit"
        )
        payload = {
            "search_engine": self.engine,
            "search_query": normalized_query,
            "search_intent": False,
            "count": max(1, min(50, int(self.settings.zhipu_search_max_results))),
            "search_recency_filter": normalized_freshness,
            "content_size": "high",
            "request_id": f"trip-{uuid.uuid4().hex}",
            "user_id": self._safe_user_id(user_id),
        }
        data = self._request(payload)
        raw_results = data.get("search_result") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            return []

        results: list[ZhipuSearchResult] = []
        seen_urls: set[str] = set()
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = self._safe_url(item.get("link"))
            title = self._bounded_text(item.get("title"), 500)
            if not url or not title or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(
                ZhipuSearchResult(
                    title=title,
                    content=self._bounded_text(item.get("content"), 4000),
                    url=url,
                    site_name=self._bounded_text(item.get("media"), 200),
                    publish_date=self._bounded_text(item.get("publish_date"), 40),
                )
            )
        return results

    def search_many(
        self,
        queries: Iterable[tuple[str, str]],
        *,
        user_id: str = "lingtu-travel",
        max_total_results: int = 16,
    ) -> list[ZhipuSearchResult]:
        limit = max(0, min(50, int(max_total_results)))
        if limit == 0:
            return []
        combined: list[ZhipuSearchResult] = []
        seen_urls: set[str] = set()
        for query, freshness in queries:
            for result in self.search(query, freshness=freshness, user_id=user_id):
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)
                combined.append(result)
                if len(combined) >= limit:
                    return combined
        return combined

    def to_references(
        self,
        results: Iterable[ZhipuSearchResult],
    ) -> list[WebReference]:
        references: list[WebReference] = []
        for result in results:
            references.append(
                WebReference(
                    title=result.title,
                    url=result.url,
                    site_name=result.site_name,
                    source_type=f"zhipu_{self.engine}",
                    publish_time=self._publish_timestamp(result.publish_date),
                )
            )
        return references[:16]

    def _request(self, payload: dict) -> dict:
        api_url = self._validated_api_url()
        headers = {
            "Authorization": f"Bearer {self.settings.zhipu_search_api_key}",
            "Content-Type": "application/json",
        }
        retries = max(0, min(2, int(self.settings.zhipu_search_max_retries)))
        for attempt in range(retries + 1):
            try:
                with httpx.Client(
                    timeout=max(1.0, float(self.settings.zhipu_search_timeout)),
                    transport=self._transport,
                ) as client:
                    with client.stream(
                        "POST",
                        api_url,
                        headers=headers,
                        json=payload,
                    ) as response:
                        if response.status_code in {401, 403}:
                            raise ZhipuSearchError(
                                "Zhipu search authorization or permission failed"
                            )
                        if response.status_code == 429:
                            error_code = self._provider_error_code(response)
                            if error_code == "1113":
                                raise ZhipuSearchError(
                                    "Zhipu search account balance or resource package "
                                    "is unavailable (code 1113)"
                                )
                            if attempt < retries:
                                time.sleep(0.2 * (attempt + 1))
                                continue
                            raise ZhipuSearchError(
                                "Zhipu search rate limit exceeded (HTTP 429)"
                            )
                        if (
                            response.status_code in {500, 502, 503, 504}
                            and attempt < retries
                        ):
                            time.sleep(0.2 * (attempt + 1))
                            continue
                        if response.status_code >= 400:
                            raise ZhipuSearchError(
                                f"Zhipu search returned HTTP {response.status_code}"
                            )
                        content = self._read_bounded_response(response)
                parsed = json.loads(content.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise ZhipuSearchError("Zhipu search returned an invalid response")
                return parsed
            except ZhipuSearchError:
                raise
            except httpx.TransportError as exc:
                if attempt < retries:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise ZhipuSearchError(
                    f"Zhipu search request failed: {type(exc).__name__}"
                ) from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ZhipuSearchError("Zhipu search returned invalid JSON") from exc
        raise ZhipuSearchError("Zhipu search request failed")

    def _provider_error_code(self, response: httpx.Response) -> str:
        try:
            content = self._read_bounded_response(response)
            parsed = json.loads(content.decode("utf-8"))
        except (ZhipuSearchError, UnicodeDecodeError, json.JSONDecodeError):
            return ""
        if not isinstance(parsed, dict):
            return ""
        error = parsed.get("error")
        if isinstance(error, dict):
            return str(error.get("code") or "")[:32]
        return str(parsed.get("code") or parsed.get("error_code") or "")[:32]

    def _validated_api_url(self) -> str:
        url = str(self.settings.zhipu_search_api_url or "").strip()
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "open.bigmodel.cn"
            or parsed.username
            or parsed.password
            or parsed.path.rstrip("/") != "/api/paas/v4/web_search"
        ):
            raise ZhipuSearchError("Zhipu search API URL is invalid")
        return url

    def _read_bounded_response(self, response: httpx.Response) -> bytes:
        limit = max(1024, int(self.settings.zhipu_search_max_response_bytes))
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > limit:
                    raise ZhipuSearchError(
                        "Zhipu search response exceeded the size limit"
                    )
            except ValueError:
                pass
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > limit:
                raise ZhipuSearchError("Zhipu search response exceeded the size limit")
            chunks.append(chunk)
        return b"".join(chunks)

    def _safe_url(self, value) -> str:
        url = str(value or "").strip()
        if not url or len(url) > 2048:
            return ""
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username or parsed.password:
            return ""
        return url

    def _safe_user_id(self, value: str) -> str:
        normalized = "".join(
            character
            for character in str(value or "lingtu-travel")
            if character.isalnum() or character in {"-", "_"}
        )[:128]
        return normalized if len(normalized) >= 6 else "lingtu-travel"

    def _bounded_text(self, value, limit: int) -> str:
        return " ".join(str(value or "").split())[:limit]

    def _publish_timestamp(self, value: str) -> Optional[int]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            if text.isdigit():
                timestamp = int(text)
                return timestamp // 1000 if timestamp > 10_000_000_000 else timestamp
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp())
        except ValueError:
            return None


_zhipu_search_service: Optional[ZhipuSearchService] = None


def get_zhipu_search_service() -> ZhipuSearchService:
    global _zhipu_search_service
    if _zhipu_search_service is None:
        _zhipu_search_service = ZhipuSearchService()
    return _zhipu_search_service
