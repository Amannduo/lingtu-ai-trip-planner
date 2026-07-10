"""LLM服务模块"""

import os

from hello_agents import HelloAgentsLLM
from ..config import get_settings

# 全局LLM实例
_llm_instance = None


def get_llm() -> HelloAgentsLLM:
    """
    获取LLM实例(单例模式)
    
    Returns:
        HelloAgentsLLM实例
    """
    global _llm_instance
    
    if _llm_instance is None:
        settings = get_settings()

        # hello-agents 实际读取的是 OPENAI_* 环境变量。
        # 当前项目示例里又提供了 LLM_* 配置，因此这里做一次显式映射，
        # 并优先使用项目 .env 中的 LLM_*，避免被外部残留的 OPENAI_* 污染。
        llm_api_key = os.getenv("LLM_API_KEY") or settings.openai_api_key
        llm_base_url = os.getenv("LLM_BASE_URL") or settings.openai_base_url
        llm_model = os.getenv("LLM_MODEL_ID") or settings.openai_model

        if llm_api_key:
            os.environ["OPENAI_API_KEY"] = llm_api_key
        if llm_base_url:
            os.environ["OPENAI_BASE_URL"] = llm_base_url
        if llm_model:
            os.environ["OPENAI_MODEL"] = llm_model

        _llm_instance = HelloAgentsLLM()

        print(f"✅ LLM服务初始化成功")
        print(f"   提供商: {_llm_instance.provider}")
        print(f"   模型: {_llm_instance.model}")
    
    return _llm_instance


def reset_llm():
    """重置LLM实例(用于测试或重新配置)"""
    global _llm_instance
    _llm_instance = None
