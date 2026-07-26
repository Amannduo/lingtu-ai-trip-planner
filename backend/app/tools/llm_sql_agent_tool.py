"""LLM-powered NL2SQL agent for the travel plan dataset.

Uses an LLM (SimpleAgent) to convert natural-language questions into
read-only parameterized SQL. Includes validation, retry, and automatic
fallback to the rule-based sql_agent_tool.
"""

from __future__ import annotations

import json
import re

from ..services.database_service import DIALECT_NAME
from .permission_tool import normalize_role, scope_user_filter
from .sql_agent_tool import SQLPlan, classify_sql_intent, run_sql_plan

# ---------------------------------------------------------------------------
# Compact schema description fed to the LLM
# ---------------------------------------------------------------------------

DB_SCHEMA_PROMPT = f"""
你是一个 {DIALECT_NAME} SQL 查询专家。你只能生成只读 SELECT 语句。
以下是 travel 数据库的表结构：

-- 旅行计划表（核心数据，每次生成行程自动写入）
CREATE TABLE travel_plans (
    id INTEGER PRIMARY KEY,
    plan_no TEXT UNIQUE,          -- 计划编号
    user_id TEXT,                 -- 用户 ID
    user_role TEXT,               -- 角色 guest/user/manager/admin
    origin_city TEXT,             -- 出发城市
    destination TEXT,             -- 目的地城市
    start_date TEXT,              -- 出发日期 YYYY-MM-DD
    end_date TEXT,                -- 结束日期
    travel_days INTEGER,          -- 旅行天数
    travelers INTEGER,            -- 出行人数
    budget REAL,                  -- 预算（元）
    actual_cost REAL,             -- 实际花费
    transportation TEXT,          -- 市内交通
    accommodation TEXT,           -- 住宿偏好
    preferences TEXT,             -- 偏好 JSON 数组字符串
    free_text TEXT,               -- 额外要求
    summary TEXT,                 -- 行程摘要
    status TEXT,                  -- 状态
    source TEXT,                  -- 来源
    created_at TEXT               -- 创建时间
);

-- 用户画像表（自动聚合）
CREATE TABLE user_profiles (
    user_id TEXT PRIMARY KEY,
    plan_count INTEGER,           -- 计划总数
    top_tags TEXT,                -- 兴趣标签 JSON
    fav_cities TEXT,              -- 常去城市 JSON
    avg_budget REAL,              -- 平均预算
    avg_days REAL,                -- 平均天数
    traveler_type TEXT,           -- 旅行者类型
    updated_at TEXT
);

-- 审计日志表
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    user_role TEXT,
    message TEXT,
    agent TEXT,
    tool TEXT,
    allowed INTEGER,
    sensitive_hit INTEGER,
    detail TEXT,
    created_at TEXT
);

-- 查询日志表
CREATE TABLE query_logs (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    user_role TEXT,
    question TEXT,
    intent TEXT,
    sql_text TEXT,
    result_summary TEXT,
    created_at TEXT
);

重要规则：
1. 只能生成 SELECT 查询，禁止 INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE
2. 参数占位符使用 :name 语法（如 :user_id）
3. 日期函数用 strftime()，如 substr(start_date, 1, 7) 按月分组
4. 统计/排行类查询加上 LIMIT 20
5. 字段别名使用中文（如 AS 目的地, AS 平均预算）
6. preferences 是 JSON 字符串；除非确认方言兼容，否则不要调用数据库专有 JSON 函数
"""

LLM_SQL_SYSTEM_PROMPT = f"""{DB_SCHEMA_PROMPT}

你必须严格按以下 JSON 格式返回，不要输出任何其他内容：
{{"sql": "SELECT ...", "title": "图表中文标题", "intent": "分类意图"}}

intent 可选值：city_rank, avg_budget, budget_trend, profile, recommendation, prediction, traveler_type_distribution, all_plan_detail, audit_log
"""

# SQL validation patterns
_DANGEROUS_SQL = re.compile(
    r"\b(drop|delete|update|insert|alter|truncate|create|grant|revoke|copy|execute)\b",
    re.IGNORECASE,
)
_SQL_SELECT_PATTERN = re.compile(r"^\s*select\b", re.IGNORECASE)

# Remove unused _PLACEHOLDER_RE

# Keywords before which we may insert a WHERE clause safely
_SAFE_INSERT_POINTS = re.compile(
    r"\b(GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING|OFFSET)\b", re.IGNORECASE
)

_USER_ID_PARAM = ":user_id"

# Marker for all user_id references in SQL
_USER_ID_IN_SQL = re.compile(r":user_id|%\(user_id\)s|user_id\s*=", re.IGNORECASE)


def _validate_sql(sql: str) -> tuple[bool, str]:
    """Return (is_valid, error_message)."""
    cleaned = sql.strip()
    if not cleaned:
        return False, "SQL 为空"
    if not _SQL_SELECT_PATTERN.match(cleaned):
        return False, "SQL 必须以 SELECT 开头"
    if _DANGEROUS_SQL.search(cleaned):
        return False, "SQL 包含危险操作（DROP/DELETE/UPDATE/INSERT 等）"
    return True, ""


def _normalize_placeholders(sql: str) -> str:
    """Convert %(param_name)s → :param_name for SQLAlchemy compatibility."""
    return _PYFORMAT_RE.sub(r":\1", sql)


_PYFORMAT_RE = re.compile(r"%\((\w+)\)s")


def _sql_already_has_user_id(sql: str) -> bool:
    """Check if the SQL already filters on user_id."""
    return bool(_USER_ID_IN_SQL.search(sql))


def _inject_user_filter(sql: str) -> str:
    """Insert 'WHERE user_id = :user_id' into an existing SQL statement."""
    clause = f"user_id = {_USER_ID_PARAM}"

    if re.search(r"\bWHERE\b", sql, re.IGNORECASE):
        # Already has a WHERE → append with AND
        return re.sub(
            r"(\bWHERE\b)",
            rf"\1 {clause} AND ",
            sql,
            count=1,
            flags=re.IGNORECASE,
        )

    if _SAFE_INSERT_POINTS.search(sql):
        return _SAFE_INSERT_POINTS.sub(rf"WHERE {clause} \1", sql, count=1)

    return sql.rstrip(";").rstrip() + f" WHERE {clause}"


def _parse_llm_response(response: str) -> dict | None:
    """Extract JSON from LLM output, handling markdown code fences."""
    text = response.strip()
    for marker in ("```json", "```"):
        if marker in text:
            start = text.find(marker) + len(marker)
            end = text.rfind("```")
            if end > start:
                text = text[start:end].strip()
            else:
                text = text[start:].strip()
            break

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        text = text[brace_start:brace_end + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _build_llm_sql_plan(message: str, user_id: str, role: str) -> SQLPlan | None:
    """Use LLM to generate a SQL plan from natural language."""
    from ..services.llm_service import get_llm
    from hello_agents import SimpleAgent

    role = normalize_role(role)
    # Dynamic model-authored SQL is reserved for administrators. User and
    # manager analytics use the deterministic allow-list planner.
    if role != "admin":
        return None
    is_scoped = False

    llm = get_llm()
    agent = SimpleAgent(
        name="NL2SQL",
        llm=llm,
        system_prompt=LLM_SQL_SYSTEM_PROMPT,
    )

    scope_note = (
        f"注意：当前角色为{role}，只能查询自己的数据。"
        f"SQL 中必须包含 WHERE user_id = :user_id 条件。"
        if is_scoped
        else f"当前角色为{role}，可以查询全局数据。"
    )
    user_prompt = (
        f"用户问题：{message}\n"
        f"当前用户 ID：{user_id}\n"
        f"用户角色：{role}\n"
        f"{scope_note}"
    )

    # ── 1st attempt ────────────────────────────────────────────────
    sql, title, intent = _try_generate_sql(agent, user_prompt, message)
    if sql:
        return _finalise_plan(sql, title, intent, user_id, is_scoped)

    # ── 2nd attempt (retry with error hint) ────────────────────────
    retry_prompt = (
        f"上次生成的 SQL 校验未通过。请生成一个合法的只读 SELECT 语句。\n"
        f"参数占位符使用 :user_id 格式。\n"
        f"原问题：{message}"
    )
    sql, title, intent = _try_generate_sql(agent, retry_prompt, message)
    if sql:
        return _finalise_plan(sql, title, intent, user_id, is_scoped)

    return None


def _try_generate_sql(agent, prompt: str, fallback_message: str) -> tuple[str | None, str, str]:
    """Single attempt: call LLM, parse, validate, normalise. Returns (sql, title, intent)."""
    try:
        response = agent.run(prompt)
    except Exception as exc:
        print(f"[llm_sql] LLM call failed: {type(exc).__name__}")
        return None, "", ""

    parsed = _parse_llm_response(response)
    if not parsed or not parsed.get("sql"):
        return None, "", ""

    sql = parsed["sql"].strip()
    sql = _normalize_placeholders(sql)

    valid, err = _validate_sql(sql)
    if not valid:
        print(f"[llm_sql] SQL validation failed: {err}")
        return None, "", ""

    title = parsed.get("title", "旅行计划查询")
    intent = parsed.get("intent", classify_sql_intent(fallback_message))
    return sql, title, intent


def _finalise_plan(
    sql: str, title: str, intent: str, user_id: str, is_scoped: bool
) -> SQLPlan:
    """Apply user-scope filter and return a SQLPlan ready for execution."""
    if is_scoped and not _sql_already_has_user_id(sql):
        sql = _inject_user_filter(sql)

    params = {"user_id": user_id} if is_scoped else {}
    return SQLPlan(intent=intent, sql=sql, params=params, agent="SQLAgent", title=title)


# ── Public API ─────────────────────────────────────────────────────────

def build_sql_plan_with_llm(message: str, user_id: str, role: str) -> SQLPlan | None:
    """Dynamic SQL is disabled; callers must use deterministic allow-list plans."""
    return None


def run_llm_sql_plan(plan: SQLPlan, role: str) -> list[dict]:
    """Refuse execution of model-authored SQL at the authorization boundary."""
    raise PermissionError("模型生成 SQL 已禁用，请使用服务端白名单分析计划。")
