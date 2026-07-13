"""Database helpers backed by SQLAlchemy for SQLite and PostgreSQL."""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from ..config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BACKEND_DIR / "data" / "travel.db"
_PYFORMAT_RE = re.compile(r"%\((\w+)\)s")


def _normalize_database_url(raw_url: str) -> str:
    value = raw_url.strip()
    if not value:
        return f"sqlite:///{DB_PATH.as_posix()}"
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


DATABASE_URL = _normalize_database_url(get_settings().database_url)
IS_SQLITE = DATABASE_URL.startswith("sqlite")
DIALECT_NAME = "sqlite" if IS_SQLITE else "postgresql"

_engine_options: dict[str, Any] = {"future": True}
if IS_SQLITE:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _engine_options["connect_args"] = {
        "timeout": 30,
        "check_same_thread": False,
    }
else:
    _engine_options.update(
        {
            "pool_pre_ping": True,
            "pool_size": 5,
            "max_overflow": 10,
            "pool_recycle": 1800,
        }
    )

engine: Engine = create_engine(DATABASE_URL, **_engine_options)

if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def _normalize_sql(sql: str) -> str:
    return _PYFORMAT_RE.sub(r":\1", sql)


@contextmanager
def get_db_connection() -> Iterator[Connection]:
    with engine.begin() as connection:
        yield connection


def fetch_all(
    sql: str,
    params: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        result = connection.execute(text(_normalize_sql(sql)), dict(params or {}))
        return [dict(row) for row in result.mappings().all()]


def fetch_one(
    sql: str,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    with engine.connect() as connection:
        row = connection.execute(
            text(_normalize_sql(sql)),
            dict(params or {}),
        ).mappings().first()
        return dict(row) if row is not None else None


def fetch_scalar(
    sql: str,
    params: Mapping[str, Any] | None = None,
) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            text(_normalize_sql(sql)),
            dict(params or {}),
        ).scalar_one_or_none()


def execute(
    sql: str,
    params: Mapping[str, Any] | None = None,
) -> int:
    with engine.begin() as connection:
        result = connection.execute(text(_normalize_sql(sql)), dict(params or {}))
        return int(result.rowcount or 0)


def execute_many(
    sql: str,
    rows: Sequence[Mapping[str, Any]],
) -> int:
    if not rows:
        return 0
    with engine.begin() as connection:
        result = connection.execute(
            text(_normalize_sql(sql)),
            [dict(row) for row in rows],
        )
        return int(result.rowcount or 0)


def _database_metadata() -> dict[str, str]:
    return {
        "dialect": DIALECT_NAME,
        "database_url": "sqlite" if IS_SQLITE else "postgresql",
    }

def database_status() -> dict[str, str]:
    """Return a connection-safe health summary without exposing the DSN."""
    metadata = _database_metadata()

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return {"status": "unavailable", **metadata}
    return {"status": "healthy", **metadata}
