"""Portable schema initialization for SQLite and PostgreSQL."""

from __future__ import annotations

from threading import Lock

from sqlalchemy import inspect, text

from .database_service import DIALECT_NAME, engine
from .db_models import metadata

_inited = False
_init_lock = Lock()


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table_name)}


def _run_compatibility_migrations() -> None:
    tables = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        if "travel_plans" in tables:
            columns = _column_names("travel_plans")
            if "plan_json" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE travel_plans "
                        "ADD COLUMN plan_json TEXT NOT NULL DEFAULT '{}'"
                    )
                )

        if "users" in tables:
            columns = _column_names("users")
            if "email" not in columns:
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN email VARCHAR(254)")
                )
            # Both supported databases understand lower() expression indexes.
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_users_username_lower ON users (lower(username))"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_users_email_lower ON users (lower(email))"
                )
            )


def init_db() -> None:
    global _inited
    if _inited:
        return

    with _init_lock:
        if _inited:
            return
        metadata.create_all(engine, checkfirst=True)
        _run_compatibility_migrations()
        _inited = True
        print(f"[db] {DIALECT_NAME} schema initialised")