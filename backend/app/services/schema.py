"""SQLite schema initialisation — called once on first access."""

from __future__ import annotations

from .database_service import get_db_connection

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS travel_plans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_no       TEXT UNIQUE NOT NULL,
    user_id       TEXT NOT NULL DEFAULT 'u_current',
    user_role     TEXT NOT NULL DEFAULT 'user',
    origin_city   TEXT,
    destination   TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    end_date      TEXT NOT NULL,
    travel_days   INTEGER NOT NULL,
    travelers     INTEGER NOT NULL DEFAULT 1,
    budget        REAL,
    actual_cost   REAL,
    transportation TEXT,
    accommodation TEXT,
    preferences   TEXT NOT NULL DEFAULT '[]',
    free_text     TEXT,
    summary       TEXT,
    status        TEXT NOT NULL DEFAULT 'completed',
    source        TEXT NOT NULL DEFAULT 'generated',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_plans_user ON travel_plans(user_id);
CREATE INDEX IF NOT EXISTS idx_plans_dest ON travel_plans(destination);
CREATE INDEX IF NOT EXISTS idx_plans_date ON travel_plans(start_date);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id        TEXT PRIMARY KEY,
    plan_count     INTEGER NOT NULL DEFAULT 0,
    top_tags       TEXT NOT NULL DEFAULT '[]',
    fav_cities     TEXT NOT NULL DEFAULT '[]',
    avg_budget     REAL,
    avg_days       REAL,
    traveler_type  TEXT,
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT,
    user_role     TEXT,
    message       TEXT,
    agent         TEXT,
    tool          TEXT,
    allowed       INTEGER NOT NULL DEFAULT 1,
    sensitive_hit INTEGER NOT NULL DEFAULT 0,
    detail        TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS query_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT,
    user_role     TEXT,
    question      TEXT NOT NULL,
    intent        TEXT,
    sql_text      TEXT,
    result_summary TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_inited = False


def init_db() -> None:
    global _inited
    if _inited:
        return
    with get_db_connection() as conn:
        conn.executescript(SCHEMA_SQL)
    _inited = True
    print("[db] SQLite schema initialised")
