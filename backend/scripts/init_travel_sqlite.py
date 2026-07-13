"""Backward-compatible entry point for the portable data seeder."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from backend.scripts.seed_travel_data import main


if __name__ == "__main__":
    print(
        "[deprecated] use backend/scripts/seed_travel_data.py; "
        "the configured DATABASE_URL now selects SQLite or PostgreSQL"
    )
    main()