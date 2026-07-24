"""配置管理模块"""

import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
BACKEND_ENV = BACKEND_DIR / ".env"

# 加载环境变量
# 首先尝试加载当前目录的.env
load_dotenv(BACKEND_ENV, override=False)
load_dotenv(override=False)

# 然后尝试加载HelloAgents的.env(如果存在)
helloagents_env = Path(__file__).parent.parent.parent.parent / "HelloAgents" / ".env"
if helloagents_env.exists():
    load_dotenv(helloagents_env, override=False)  # 不覆盖已有的环境变量


class Settings(BaseSettings):
    """应用配置"""

    # 应用基本配置
    app_name: str = "灵途 AI 旅行规划师"
    app_version: str = "1.0.0"
    debug: bool = False

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8000

    # 数据库：留空时使用本地 SQLite，生产环境推荐 PostgreSQL
    database_url: str = ""

    # CORS配置 - 使用字符串,在代码中分割
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"
    cors_origin_regex: str = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"

    # 服务端认证配置
    auth_secret_key: str = ""
    auth_access_token_minutes: int = 480
    auth_cookie_name: str = "lingtu_access_token"
    auth_cookie_secure: bool = False
    auth_manager_invite_code: str = ""
    auth_admin_invite_code: str = ""
    # Web Push / VAPID configuration. The private key must remain server-side.
    web_push_vapid_public_key: str = ""
    web_push_vapid_private_key: str = ""
    web_push_vapid_subject: str = ""
    web_push_max_retries: int = 2
    web_push_retry_delay_seconds: float = 0.25
    web_push_ttl_seconds: int = 300
    web_push_timeout_seconds: float = 15.0
    web_push_dns_timeout_seconds: float = 3.0
    web_push_max_subscriptions_per_user: int = 20
    web_push_delivery_budget_seconds: float = 30.0
    web_push_allowed_host_suffixes: str = ""

    # Civil-date timezone for weekend / relative-date semantics.
    business_timezone: str = "Asia/Shanghai"

    # Real SMTP attempts are limited per authenticated user and peer IP.
    email_quota_enabled: bool = True
    email_user_daily_limit: int = 10
    email_ip_hourly_limit: int = 30

    # 高德地图API配置
    amap_api_key: str = ""
    amap_route_timeout: int = 12
    amap_route_max_segments: int = 24

    # Unsplash API配置
    unsplash_access_key: str = ""
    unsplash_secret_key: str = ""

    # FlyAI/Fliggy CLI budget data source.
    flyai_enabled: bool = True
    flyai_api_key: str = ""
    flyai_cli_command: str = "npx --yes @fly-ai/flyai-cli"
    flyai_timeout: int = 25

    # Volcengine Web QA Agent configuration.
    volcengine_agent_enabled: bool = True
    volcengine_agent_api_key: str = ""
    volcengine_agent_bot_id: str = ""
    volcengine_agent_api_url: str = "https://open.feedcoopapi.com/agent_api/agent/chat/completion"
    volcengine_agent_timeout: int = 120
    volcengine_agent_force_web: bool = True
    volcengine_agent_model: str = ""

    # LLM配置 (从环境变量读取,由HelloAgents管理)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4"

    # 日志配置
    log_level: str = "INFO"

    class Config:
        env_file = str(BACKEND_ENV)
        case_sensitive = False
        extra = "ignore"  # 忽略额外的环境变量

    def get_cors_origins_list(self) -> List[str]:
        """获取CORS origins列表"""
        return [origin.strip() for origin in self.cors_origins.split(',') if origin.strip()]


# 创建全局配置实例
settings = Settings()


def get_settings() -> Settings:
    """获取配置实例"""
    return settings


# 验证必要的配置
def validate_config():
    """验证配置是否完整"""
    errors = []
    warnings = []

    if not settings.amap_api_key:
        errors.append("AMAP_API_KEY未配置")

    if len(settings.auth_secret_key) < 32:
        errors.append("AUTH_SECRET_KEY必须配置且至少为32个字符")

    # HelloAgentsLLM会自动从LLM_API_KEY读取,不强制要求OPENAI_API_KEY
    llm_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not llm_api_key:
        warnings.append("LLM_API_KEY或OPENAI_API_KEY未配置,LLM功能可能无法使用")

    if settings.volcengine_agent_enabled and (
        not settings.volcengine_agent_api_key or not settings.volcengine_agent_bot_id
    ):
        warnings.append("VOLCENGINE_AGENT_API_KEY或VOLCENGINE_AGENT_BOT_ID未配置,联网攻略Agent将使用本地降级生成")

    if errors:
        error_msg = "配置错误:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(error_msg)

    if warnings:
        print("\n⚠️  配置警告:")
        for w in warnings:
            print(f"  - {w}")

    return True


# 打印配置信息(用于调试)
def print_config():
    """打印当前配置(隐藏敏感信息)"""
    print(f"应用名称: {settings.app_name}")
    print(f"版本: {settings.app_version}")
    print(f"服务器: {settings.host}:{settings.port}")
    print(f"高德地图API Key: {'已配置' if settings.amap_api_key else '未配置'}")
    print(f"后端认证: {'已配置' if len(settings.auth_secret_key) >= 32 else '未配置'}")

    # 检查LLM配置
    llm_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    llm_base_url = os.getenv("LLM_BASE_URL") or settings.openai_base_url
    llm_model = os.getenv("LLM_MODEL_ID") or settings.openai_model

    print(f"LLM API Key: {'已配置' if llm_api_key else '未配置'}")
    print(f"LLM Base URL: {llm_base_url}")
    print(f"LLM Model: {llm_model}")
    print(
        "联网攻略Agent: "
        f"{'已配置' if settings.volcengine_agent_api_key and settings.volcengine_agent_bot_id else '未配置'}"
    )
    print(f"日志级别: {settings.log_level}")
