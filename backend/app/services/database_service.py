"""SQLite database helpers — zero-config, auto-creates backend/data/travel.db."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

BACKEND_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BACKEND_DIR / "data" / "travel.db"

# Accept legacy pyformat placeholders %(name)s and run them as SQLite :name.
_PYFORMAT_RE = re.compile(r"%\((\w+)\)s")


def _normalize_sql(sql: str) -> str:
    return _PYFORMAT_RE.sub(r":\1", sql)


@contextmanager
def get_db_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(sql: str, params: dict | tuple | None = None) -> list[dict]:
    sql = _normalize_sql(sql)
    with get_db_connection() as conn:
        cur = conn.execute(sql, params or {})
        return [dict(row) for row in cur.fetchall()]


def fetch_one(sql: str, params: dict | tuple | None = None) -> dict | None:
    sql = _normalize_sql(sql)
    with get_db_connection() as conn:
        cur = conn.execute(sql, params or {})
        row = cur.fetchone()
        return dict(row) if row else None


def execute(sql: str, params: dict | tuple | None = None) -> None:
    sql = _normalize_sql(sql)
    with get_db_connection() as conn:
        conn.execute(sql, params or {})
