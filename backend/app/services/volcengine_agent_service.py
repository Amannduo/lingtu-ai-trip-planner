"""Volcengine Web QA Agent API client."""

from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..config import get_settings
from ..models.schemas import WebReference


class VolcengineAgentService:
    """Client for Volcengine 联网问答Agent 智能体会话API."""

    def __init__(self):
        self.settings = get_settings()

    @property
    def is_configured(self) -> bool:
        return bool(
            self.settings.volcengine_agent_enabled
            and self.settings.volcengine_agent_api_key
            and self.settings.volcengine_agent_bot_id
        )

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        knowledge: str = "",
        location_info: Optional[Dict[str, Any]] = None,
        user_id: str = "trip-planner"
    ) -> Tuple[str, List[WebReference], Dict[str, Any]]:
        """Call the non-streaming chat completion API."""
        if not self.is_configured:
            raise RuntimeError("Volcengine agent is not configured")

        payload: Dict[str, Any] = {
            "bot_id": self.settings.volcengine_agent_bot_id,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "user_id": user_id,
            "extension_options": {
                "browsing_mode": 2 if self.settings.volcengine_agent_force_web else 1,
                "disable_follow_up": True,
                "disable_text_to_image": True,
                "disable_video_text_mix": True,
                "disable_image_text_mix": True,
                "filter_emoji": True
            }
        }

        if knowledge:
            payload["knowledge"] = knowledge
        if location_info:
            payload["location_info"] = location_info
        if self.settings.volcengine_agent_model:
            payload["model"] = self.settings.volcengine_agent_model

        headers = {
            "Authorization": f"Bearer {self.settings.volcengine_agent_api_key}",
            "Content-Type": "application/json"
        }

        with httpx.Client(timeout=self.settings.volcengine_agent_timeout) as client:
            response = client.post(
                self.settings.volcengine_agent_api_url,
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()

        return self._extract_content(data), self._extract_references(data), data

    def _extract_content(self, data: Dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""

        first_choice = choices[0] or {}
        message = first_choice.get("message") or {}
        delta = first_choice.get("delta") or {}
        content = message.get("content") or delta.get("content") or ""
        return str(content).strip()

    def _extract_references(self, data: Dict[str, Any]) -> List[WebReference]:
        raw_references = []
        for key in ("references", "search_results", "thinking_references"):
            values = data.get(key)
            if isinstance(values, list):
                raw_references.extend(values)

        references: List[WebReference] = []
        seen_urls = set()
        for item in raw_references:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not url and not title:
                continue
            key = url or title
            if key in seen_urls:
                continue
            seen_urls.add(key)
            references.append(
                WebReference(
                    title=title,
                    url=url,
                    site_name=str(item.get("site_name") or ""),
                    source_type=str(item.get("source_type") or ""),
                    publish_time=item.get("publish_time") if isinstance(item.get("publish_time"), int) else None
                )
            )

        return references[:12]


_volcengine_agent_service: Optional[VolcengineAgentService] = None


def get_volcengine_agent_service() -> VolcengineAgentService:
    """Get singleton Volcengine agent service."""
    global _volcengine_agent_service
    if _volcengine_agent_service is None:
        _volcengine_agent_service = VolcengineAgentService()
    return _volcengine_agent_service
