"""Shared LLM client initialization."""

import os
import threading

from hello_agents import HelloAgentsLLM

from ..config import get_settings


_llm_instance: HelloAgentsLLM | None = None
_llm_lock = threading.Lock()


def _create_llm() -> HelloAgentsLLM:
    settings = get_settings()
    llm_api_key = os.getenv("LLM_API_KEY") or settings.openai_api_key
    llm_base_url = os.getenv("LLM_BASE_URL") or settings.openai_base_url
    llm_model = os.getenv("LLM_MODEL_ID") or settings.openai_model

    if llm_api_key:
        os.environ["OPENAI_API_KEY"] = llm_api_key
    if llm_base_url:
        os.environ["OPENAI_BASE_URL"] = llm_base_url
    if llm_model:
        os.environ["OPENAI_MODEL"] = llm_model

    llm_timeout = os.getenv("LLM_TIMEOUT", "120")
    if llm_timeout and "OPENAI_TIMEOUT" not in os.environ:
        os.environ["OPENAI_TIMEOUT"] = llm_timeout

    instance = HelloAgentsLLM()
    print("LLM 服务初始化成功")
    print(f"   提供商: {instance.provider}")
    print(f"   模型: {instance.model}")
    return instance


def get_llm() -> HelloAgentsLLM:
    """Return the process-wide, thread-safely initialized LLM client."""
    global _llm_instance
    if _llm_instance is None:
        with _llm_lock:
            if _llm_instance is None:
                _llm_instance = _create_llm()
    return _llm_instance


def reset_llm() -> None:
    """Reset the shared client for tests or configuration reloads."""
    global _llm_instance
    with _llm_lock:
        _llm_instance = None
