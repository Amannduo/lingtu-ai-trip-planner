"""Initialize PostgreSQL travel-plan dataset for Lingtu.

Usage:
  python backend/scripts/init_travel_postgres.py --rows 1000

Environment:
  DATABASE_URL=postgresql+psycopg://postgres:password@localhost:5432/lingtu_travel
  POSTGRES_ADMIN_URL=postgresql+psycopg://postgres:password@localhost:5432/postgres
"""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    import psycopg
except ImportError as exc:  # pragma: no cover - shown to the user at runtime.
    raise SystemExit(
        "Missing PostgreSQL driver. Install backend requirements first: "
        "python -m pip install -r backend/requirements.txt"
    ) from exc


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
SCHEMA_PATH = BACKEND_DIR / "db" / "travel_plan_schema.sql"
DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/lingtu_travel"


DESTINATIONS = [
    ("成都", ["美食", "休闲", "历史文化"], 0.95),
    ("长沙", ["美食", "夜生活", "购物"], 0.82),
    ("重庆", ["美食", "城市漫步", "夜景"], 0.88),
    ("杭州", ["自然风光", "休闲", "艺术"], 0.86),
    ("南京", ["历史文化", "美食", "博物馆"], 0.78),
    ("西安", ["历史文化", "美食", "深度体验"], 0.76),
    ("厦门", ["海滨休闲", "摄影", "轻松低强度"], 0.72),
    ("苏州", ["园林", "历史文化", "轻松低强度"], 0.68),
    ("上海", ["购物", "艺术", "城市漫步"], 0.74),
    ("北京", ["历史文化", "博物馆", "亲子"], 0.8),
    ("广州", ["美食", "购物", "城市漫步"], 0.7),
    ("深圳", ["亲子", "购物", "海滨休闲"], 0.63),
    ("桂林", ["自然风光", "摄影", "轻户外"], 0.61),
    ("青岛", ["海滨休闲", "美食", "摄影"], 0.58),
    ("大理", ["自然风光", "休闲", "摄影"], 0.55),
]

ORIGIN_CITIES = ["上海", "北京", "广州", "深圳", "杭州", "南京", "武汉", "成都", "西安", "长沙", "苏州", "天津"]
TRANSPORTATION = ["公共交通", "自驾", "步行", "混合"]
INTERCITY = ["自动选择", "火车/高铁", "飞机", "自驾"]
ACCOMMODATION = ["经济型酒店", "舒适型酒店", "豪华酒店", "民宿", "亲子酒店"]
ROLES = ["guest", "user", "manager", "admin"]
ROLE_WEIGHTS = [0.08, 0.76, 0.12, 0.04]
STATUS = ["completed", "completed", "completed", "cancelled", "draft"]
LAST_NAMES = ["王", "李", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴", "徐", "孙"]
FIRST_NAMES = ["一然", "子涵", "明轩", "雨桐", "思远", "可欣", "俊杰", "若曦", "晨宇", "佳宁"]


@dataclass(frozen=True)
class PlanSeed:
    plan_no: str
    user_id: str
    user_role: str
    origin_city: str
    destination_city: str
    start_date: date
    end_date: date
    travel_days: int
    travelers: int
    budget: float
    actual_cost: float
    transportation: str
    intercity_transportation: str
    accommodation: str
    preferences: list[str]
    free_text_input: str
    generated_summary: str
    contact_name: str
    contact_phone: str
    contact_email: str
    status: str
    created_at: datetime


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def normalize_psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


def database_name(url: str) -> str:
    path = urlsplit(normalize_psycopg_url(url)).path.strip("/")
    if not path:
        raise ValueError("DATABASE_URL must include a database name.")
    return path.split("/")[0]


def url_for_database(url: str, db_name: str) -> str:
    parts = urlsplit(normalize_psycopg_url(url))
    query = urlencode(parse_qsl(parts.query, keep_blank_values=True))
    return urlunsplit((parts.scheme, parts.netloc, f"/{db_name}", query, parts.fragment))


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def create_database_if_needed(database_url: str, admin_url: str | None) -> None:
    db_name = database_name(database_url)
    admin_conn_url = normalize_psycopg_url(admin_url) if admin_url else url_for_database(database_url, "postgres")
    with psycopg.connect(admin_conn_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            exists = cur.fetchone() is not None
            if exists:
                print(f"[db] database already exists: {db_name}")
                return
            cur.execute(f"CREATE DATABASE {quote_ident(db_name)}")
            print(f"[db] database created: {db_name}")


def apply_schema(database_url: str) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with psycopg.connect(normalize_psycopg_url(database_url), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
    print(f"[db] schema applied: {SCHEMA_PATH}")


def weighted_destination(rng: random.Random) -> tuple[str, list[str]]:
    cities = [item[0] for item in DESTINATIONS]
    weights = [item[2] for item in DESTINATIONS]
    city = rng.choices(cities, weights=weights, k=1)[0]
    tags = next(item[1] for item in DESTINATIONS if item[0] == city)
    return city, tags


def choose_preferences(rng: random.Random, base_tags: list[str]) -> list[str]:
    pool = list(dict.fromkeys(base_tags + [
        "美食",
        "历史文化",
        "自然风光",
        "亲子",
        "购物",
        "休闲",
        "摄影",
        "轻松低强度",
        "深度体验",
        "高性价比",
    ]))
    count = rng.choice([2, 2, 3, 3, 4])
    result = list(base_tags[: rng.choice([1, 2, 3])])
    while len(result) < count:
        tag = rng.choice(pool)
        if tag not in result:
            result.append(tag)
    return result[:4]


def budget_for_plan(rng: random.Random, city: str, days: int, travelers: int, accommodation: str) -> tuple[float, float]:
    city_factor = {
        "上海": 1.35,
        "北京": 1.28,
        "深圳": 1.24,
        "杭州": 1.12,
        "厦门": 1.08,
        "成都": 0.96,
        "长沙": 0.88,
        "重庆": 0.92,
        "西安": 0.9,
        "桂林": 0.86,
    }.get(city, 1.0)
    hotel_factor = {
        "经济型酒店": 0.78,
        "舒适型酒店": 1.0,
        "豪华酒店": 1.58,
        "民宿": 0.9,
        "亲子酒店": 1.22,
    }.get(accommodation, 1.0)
    base_per_person_day = rng.randint(420, 980) * city_factor * hotel_factor
    budget = round(base_per_person_day * days * travelers / 50) * 50
    actual = max(300, budget * rng.uniform(0.82, 1.18))
    return float(round(budget, 2)), float(round(actual, 2))


def make_phone(index: int) -> str:
    return f"13{index % 10}{(70000000 + index * 97) % 100000000:08d}"[:11]


def generate_plans(rows: int, seed: int) -> list[PlanSeed]:
    rng = random.Random(seed)
    today = date.today()
    user_count = max(260, rows // 3)
    plans: list[PlanSeed] = []
    for index in range(rows):
        destination, base_tags = weighted_destination(rng)
        origin = rng.choice([city for city in ORIGIN_CITIES if city != destination] or ORIGIN_CITIES)
        days = rng.choices([1, 2, 3, 4, 5, 6, 7], weights=[4, 18, 34, 22, 13, 6, 3], k=1)[0]
        travelers = rng.choices([1, 2, 3, 4, 5, 6], weights=[14, 45, 18, 16, 5, 2], k=1)[0]
        start = today - timedelta(days=rng.randint(0, 540))
        end = start + timedelta(days=days - 1)
        accommodation = rng.choice(ACCOMMODATION)
        preferences = choose_preferences(rng, base_tags)
        budget, actual_cost = budget_for_plan(rng, destination, days, travelers, accommodation)
        user_index = rng.randint(1, user_count)
        user_id = f"u_{user_index:04d}"
        role = rng.choices(ROLES, weights=ROLE_WEIGHTS, k=1)[0]
        contact_name = rng.choice(LAST_NAMES) + rng.choice(FIRST_NAMES)
        free_text = f"偏好{','.join(preferences[:3])}，希望{destination}{days}天行程节奏舒适。"
        summary = (
            f"{destination}{days}天旅行计划，{travelers}人出行，"
            f"预算约{int(budget)}元，重点关注{','.join(preferences[:2])}。"
        )
        created_at = datetime.combine(start - timedelta(days=rng.randint(3, 45)), datetime.min.time()) + timedelta(
            hours=rng.randint(8, 23),
            minutes=rng.randint(0, 59),
        )
        plans.append(
            PlanSeed(
                plan_no=f"LTP{today.strftime('%Y%m%d')}{index + 1:06d}",
                user_id=user_id,
                user_role=role,
                origin_city=origin,
                destination_city=destination,
                start_date=start,
                end_date=end,
                travel_days=days,
                travelers=travelers,
                budget=budget,
                actual_cost=actual_cost,
                transportation=rng.choice(TRANSPORTATION),
                intercity_transportation=rng.choice(INTERCITY),
                accommodation=accommodation,
                preferences=preferences,
                free_text_input=free_text,
                generated_summary=summary,
                contact_name=contact_name,
                contact_phone=make_phone(index + 1),
                contact_email=f"user{user_index:04d}@example.com",
                status=rng.choice(STATUS),
                created_at=created_at,
            )
        )
    return plans


def tag_scores(plan: PlanSeed) -> dict[str, float]:
    scores: dict[str, float] = {tag: 0.72 for tag in plan.preferences}
    if plan.budget / max(plan.travelers * plan.travel_days, 1) < 650:
        scores["高性价比"] = 0.82
    if plan.travel_days <= 3:
        scores["短途旅行"] = 0.76
    if plan.travel_days >= 5:
        scores["深度体验"] = max(scores.get("深度体验", 0), 0.78)
    if plan.transportation in ("步行", "公共交通"):
        scores["城市漫步"] = max(scores.get("城市漫步", 0), 0.68)
    if plan.accommodation in ("豪华酒店", "亲子酒店"):
        scores["品质住宿"] = 0.74
    if "轻松低强度" in plan.preferences or "休闲" in plan.preferences:
        scores["轻松低强度"] = max(scores.get("轻松低强度", 0), 0.84)
    return scores


def insert_seed_data(database_url: str, rows: int, seed: int, reset: bool) -> None:
    plans = generate_plans(rows, seed)
    with psycopg.connect(normalize_psycopg_url(database_url)) as conn:
        with conn.cursor() as cur:
            if reset:
                cur.execute(
                    "TRUNCATE user_query_logs, agent_audit_logs, recommendation_logs, "
                    "user_interest_profiles, travel_plan_tags, travel_plans RESTART IDENTITY CASCADE"
                )
                print("[seed] existing dataset truncated")

            cur.execute("SELECT COUNT(*) FROM travel_plans")
            current_count = int(cur.fetchone()[0])
            if current_count >= rows and not reset:
                print(f"[seed] travel_plans already has {current_count} rows; skip insert")
                refresh_profiles(cur)
                conn.commit()
                return

            needed = rows - current_count
            plans = plans[:needed]
            for plan in plans:
                cur.execute(
                    """
                    INSERT INTO travel_plans (
                      plan_no, user_id, user_role, origin_city, destination_city,
                      start_date, end_date, travel_days, travelers, budget, actual_cost,
                      transportation, intercity_transportation, accommodation,
                      preferences, free_text_input, generated_summary,
                      contact_name, contact_phone, contact_email, source, status, created_at
                    )
                    VALUES (
                      %(plan_no)s, %(user_id)s, %(user_role)s, %(origin_city)s, %(destination_city)s,
                      %(start_date)s, %(end_date)s, %(travel_days)s, %(travelers)s, %(budget)s, %(actual_cost)s,
                      %(transportation)s, %(intercity_transportation)s, %(accommodation)s,
                      %(preferences)s::jsonb, %(free_text_input)s, %(generated_summary)s,
                      %(contact_name)s, %(contact_phone)s, %(contact_email)s, 'mock', %(status)s, %(created_at)s
                    )
                    ON CONFLICT (plan_no) DO NOTHING
                    RETURNING id
                    """,
                    {
                        **plan.__dict__,
                        "preferences": json.dumps(plan.preferences, ensure_ascii=False),
                    },
                )
                returned = cur.fetchone()
                if not returned:
                    continue
                plan_id = returned[0]
                for tag_name, score in tag_scores(plan).items():
                    cur.execute(
                        """
                        INSERT INTO travel_plan_tags (plan_id, user_id, tag_name, tag_score, source, created_at)
                        VALUES (%s, %s, %s, %s, 'seed_rule', %s)
                        """,
                        (plan_id, plan.user_id, tag_name, score, plan.created_at),
                    )

            refresh_profiles(cur)
        conn.commit()

    print(f"[seed] inserted target rows: {rows}")


def refresh_profiles(cur: Any) -> None:
    cur.execute("SELECT user_id, destination_city, budget, actual_cost, travel_days FROM travel_plans")
    plans_by_user: dict[str, list[tuple[str, float, float, int]]] = defaultdict(list)
    for user_id, city, budget, actual_cost, days in cur.fetchall():
        plans_by_user[user_id].append((city, float(budget or 0), float(actual_cost or 0), int(days or 0)))

    cur.execute("SELECT user_id, tag_name, AVG(tag_score) FROM travel_plan_tags GROUP BY user_id, tag_name")
    tag_by_user: dict[str, Counter[str]] = defaultdict(Counter)
    for user_id, tag_name, avg_score in cur.fetchall():
        tag_by_user[user_id][tag_name] = float(avg_score)

    for user_id, plans in plans_by_user.items():
        city_counter = Counter(item[0] for item in plans)
        tags = tag_by_user.get(user_id, Counter())
        top_tags = [{"tag": tag, "score": round(score, 3)} for tag, score in tags.most_common(5)]
        favorite_cities = [{"city": city, "count": count} for city, count in city_counter.most_common(5)]
        avg_budget = sum(item[1] for item in plans) / len(plans)
        avg_actual_cost = sum(item[2] for item in plans) / len(plans)
        avg_days = sum(item[3] for item in plans) / len(plans)
        traveler_type = infer_traveler_type(top_tags, avg_budget / max(avg_days, 1))
        cur.execute(
            """
            INSERT INTO user_interest_profiles (
              user_id, plan_count, top_tags, favorite_cities, avg_budget,
              avg_actual_cost, avg_travel_days, traveler_type, updated_at
            )
            VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET
              plan_count = EXCLUDED.plan_count,
              top_tags = EXCLUDED.top_tags,
              favorite_cities = EXCLUDED.favorite_cities,
              avg_budget = EXCLUDED.avg_budget,
              avg_actual_cost = EXCLUDED.avg_actual_cost,
              avg_travel_days = EXCLUDED.avg_travel_days,
              traveler_type = EXCLUDED.traveler_type,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                user_id,
                len(plans),
                json.dumps(top_tags, ensure_ascii=False),
                json.dumps(favorite_cities, ensure_ascii=False),
                round(avg_budget, 2),
                round(avg_actual_cost, 2),
                round(avg_days, 2),
                traveler_type,
            ),
        )
    print(f"[profile] refreshed users: {len(plans_by_user)}")


def infer_traveler_type(top_tags: list[dict[str, Any]], per_day_budget: float) -> str:
    tag_names = {item["tag"] for item in top_tags}
    if {"美食", "休闲"} & tag_names and "轻松低强度" in tag_names:
        return "美食休闲型"
    if {"历史文化", "博物馆"} & tag_names:
        return "文化探索型"
    if {"自然风光", "摄影"} & tag_names:
        return "风光摄影型"
    if "亲子" in tag_names:
        return "亲子陪伴型"
    if per_day_budget > 1200:
        return "品质度假型"
    if "高性价比" in tag_names:
        return "高性价比型"
    return "城市漫游型"


def print_summary(database_url: str) -> None:
    with psycopg.connect(normalize_psycopg_url(database_url)) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM travel_plans")
            plan_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM travel_plan_tags")
            tag_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM user_interest_profiles")
            profile_count = cur.fetchone()[0]
            cur.execute(
                """
                SELECT destination_city, COUNT(*) AS count, ROUND(AVG(budget), 2) AS avg_budget
                FROM travel_plans
                GROUP BY destination_city
                ORDER BY count DESC
                LIMIT 8
                """
            )
            rows = cur.fetchall()
    print(f"[summary] travel_plans={plan_count}, travel_plan_tags={tag_count}, profiles={profile_count}")
    print("[summary] top destinations:")
    for city, count, avg_budget in rows:
        print(f"  - {city}: {count} plans, avg_budget={avg_budget}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize Lingtu PostgreSQL travel-plan dataset.")
    parser.add_argument("--rows", type=int, default=1000, help="Target travel plan rows. Default: 1000")
    parser.add_argument("--seed", type=int, default=20260710, help="Random seed for reproducible mock data.")
    parser.add_argument("--reset", action="store_true", help="Truncate existing dataset before inserting.")
    args = parser.parse_args()

    load_env_file(BACKEND_DIR / ".env")
    load_env_file(BACKEND_DIR / ".env.example")

    database_url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    admin_url = os.getenv("POSTGRES_ADMIN_URL")

    print(f"[config] target database: {database_name(database_url)}")
    create_database_if_needed(database_url, admin_url)
    apply_schema(database_url)
    insert_seed_data(database_url, rows=max(args.rows, 1000), seed=args.seed, reset=args.reset)
    print_summary(database_url)


if __name__ == "__main__":
    main()
