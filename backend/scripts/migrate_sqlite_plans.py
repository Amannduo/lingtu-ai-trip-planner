"""Migrate legacy SQLite travel plans into the configured PostgreSQL database.

The command is a dry run unless ``--apply`` is supplied. Existing plan numbers
are skipped, which makes repeated executions safe.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, select

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from backend.app.services.database_service import DB_PATH, engine
from backend.app.services.db_models import travel_plans
from backend.app.services.schema import init_db
from backend.app.services.travel_plan_data_service import get_travel_plan_data_service


REQUIRED_COLUMNS = {
    "plan_no",
    "user_id",
    "destination",
    "start_date",
    "end_date",
    "travel_days",
}


def _read_source(source_path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")

    connection = sqlite3.connect(source_path)
    connection.row_factory = sqlite3.Row
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'travel_plans'"
        ).fetchone()
        if table is None:
            raise RuntimeError("SQLite database does not contain travel_plans")
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(travel_plans)")
        }
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise RuntimeError(
                "SQLite travel_plans is missing required columns: "
                + ", ".join(sorted(missing))
            )
        rows = [dict(row) for row in connection.execute("SELECT * FROM travel_plans")]
        return rows, columns
    finally:
        connection.close()


def _normalise_row(
    row: dict[str, Any], copy_columns: list[str]
) -> dict[str, Any]:
    result = {column: row.get(column) for column in copy_columns}
    result["user_id"] = result.get("user_id") or ""
    result["user_role"] = result.get("user_role") or "user"
    result["travelers"] = result.get("travelers") or 1
    result["preferences"] = result.get("preferences") or "[]"
    result["plan_json"] = result.get("plan_json") or "{}"
    result["status"] = result.get("status") or "completed"
    original_source = result.get("source") or "unknown"
    if not str(original_source).startswith("sqlite_migrated/"):
        result["source"] = f"sqlite_migrated/{original_source}"[:64]
    return result


def migrate(source_path: Path, *, apply: bool) -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError(
            "Configured destination is not PostgreSQL; refusing to migrate into itself"
        )

    init_db()
    source_rows, source_columns = _read_source(source_path)
    destination_columns = {
        column["name"] for column in inspect(engine).get_columns("travel_plans")
    }
    copy_columns = [
        column.name
        for column in travel_plans.columns
        if column.name != "id"
        and column.name in source_columns
        and column.name in destination_columns
    ]

    with engine.connect() as connection:
        existing_plan_numbers = set(
            connection.execute(select(travel_plans.c.plan_no)).scalars().all()
        )

    unique_rows: dict[str, dict[str, Any]] = {}
    source_duplicates = 0
    for row in source_rows:
        plan_no = str(row.get("plan_no") or "").strip()
        if not plan_no:
            raise RuntimeError("SQLite contains a travel plan without plan_no")
        if plan_no in unique_rows:
            source_duplicates += 1
            continue
        unique_rows[plan_no] = row

    pending = [
        _normalise_row(row, copy_columns)
        for plan_no, row in unique_rows.items()
        if plan_no not in existing_plan_numbers
    ]
    target_duplicates = len(unique_rows) - len(pending)
    complete_json = sum(
        1
        for row in source_rows
        if str(row.get("plan_json") or "").strip() not in {"", "{}", "null"}
    )
    sources = Counter(str(row.get("source") or "unknown") for row in source_rows)

    print(
        {
            "mode": "apply" if apply else "dry-run",
            "source": str(source_path),
            "source_rows": len(source_rows),
            "source_duplicates": source_duplicates,
            "already_in_postgresql": target_duplicates,
            "ready_to_insert": len(pending),
            "complete_plan_json": complete_json,
            "source_types": dict(sorted(sources.items())),
        }
    )

    if not apply or not pending:
        return 0

    with engine.begin() as connection:
        connection.execute(travel_plans.insert(), pending)

    profile_service = get_travel_plan_data_service()
    affected_users = {
        str(row.get("user_id") or "").strip() for row in source_rows
    } - {""}
    for user_id in affected_users:
        profile_service._refresh_profile(user_id)

    migrated_numbers = {str(row["plan_no"]) for row in pending}
    with engine.connect() as connection:
        verified = set(
            connection.execute(
                select(travel_plans.c.plan_no).where(
                    travel_plans.c.plan_no.in_(migrated_numbers)
                )
            ).scalars()
        )
    if verified != migrated_numbers:
        raise RuntimeError(
            f"Migration verification failed: expected {len(migrated_numbers)}, "
            f"found {len(verified)}"
        )
    print(
        {
            "status": "completed",
            "inserted": len(migrated_numbers),
            "profiles_refreshed": len(affected_users),
        }
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DB_PATH,
        help=f"legacy SQLite file (default: {DB_PATH})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the migration; without this flag only show a dry run",
    )
    args = parser.parse_args()
    return migrate(args.source.resolve(), apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
