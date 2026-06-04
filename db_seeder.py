"""
UEFA Stats Database Seeder
==========================
Reads scraped CSV/JSON and upserts all records into PostgreSQL.
Safe to run multiple times — ON CONFLICT DO UPDATE.

Usage:
    python db_seeder.py
    python db_seeder.py --file ./output/ucl_stats_2024-25.csv
    python db_seeder.py --dry-run
"""

import os
import csv
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime

import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DEFAULT_INPUT     = Path("./output/ucl_stats_all_seasons.csv")
DEFAULT_BATCH     = 100


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------

def get_connection():
    # Load .env file if present (requires python-dotenv)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url.replace("postgres://", "postgresql://", 1))

    required = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing  = [v for v in required if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(
            f"Missing env vars: {missing}\n"
            "Set DATABASE_URL or individual DB_HOST / DB_NAME / DB_USER / DB_PASSWORD."
        )

    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", 5432)),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE IF NOT EXISTS player_stats (
    id                SERIAL PRIMARY KEY,
    name              VARCHAR(255)   NOT NULL,
    nation            VARCHAR(100),
    position          VARCHAR(50),
    detailed_position VARCHAR(50),
    age               INTEGER,
    birth_date        DATE,
    matches_played    INTEGER        DEFAULT 0,
    minutes_played    INTEGER        DEFAULT 0,
    goals             INTEGER        DEFAULT 0,
    assists           INTEGER        DEFAULT 0,
    penalties_scored  INTEGER        DEFAULT 0,
    yellow_cards      INTEGER        DEFAULT 0,
    red_cards         INTEGER        DEFAULT 0,
    team_name         VARCHAR(255),
    team_code         VARCHAR(10),
    team_logo_url     TEXT,
    player_id         VARCHAR(50),
    player_image_url  TEXT,
    season            VARCHAR(10)    NOT NULL,
    created_at        TIMESTAMP      DEFAULT NOW(),
    updated_at        TIMESTAMP      DEFAULT NOW(),

    CONSTRAINT uq_player_team_season UNIQUE (name, team_name, season)
);

CREATE INDEX IF NOT EXISTS idx_ps_season   ON player_stats (season);
CREATE INDEX IF NOT EXISTS idx_ps_team     ON player_stats (LOWER(team_name));
CREATE INDEX IF NOT EXISTS idx_ps_nation   ON player_stats (LOWER(nation));
CREATE INDEX IF NOT EXISTS idx_ps_position ON player_stats (LOWER(position));
CREATE INDEX IF NOT EXISTS idx_ps_name     ON player_stats (LOWER(name));
"""

UPSERT_SQL = """
INSERT INTO player_stats (
    name, nation, position, detailed_position, age, birth_date,
    matches_played, minutes_played, goals, assists, penalties_scored,
    yellow_cards, red_cards,
    team_name, team_code, team_logo_url,
    player_id, player_image_url, season, updated_at
)
VALUES %s
ON CONFLICT (name, team_name, season) DO UPDATE SET
    nation            = EXCLUDED.nation,
    position          = EXCLUDED.position,
    detailed_position = EXCLUDED.detailed_position,
    age               = EXCLUDED.age,
    birth_date        = EXCLUDED.birth_date,
    matches_played    = EXCLUDED.matches_played,
    minutes_played    = EXCLUDED.minutes_played,
    goals             = EXCLUDED.goals,
    assists           = EXCLUDED.assists,
    penalties_scored  = EXCLUDED.penalties_scored,
    yellow_cards      = EXCLUDED.yellow_cards,
    red_cards         = EXCLUDED.red_cards,
    team_code         = EXCLUDED.team_code,
    team_logo_url     = EXCLUDED.team_logo_url,
    player_id         = EXCLUDED.player_id,
    player_image_url  = EXCLUDED.player_image_url,
    updated_at        = NOW();
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_int(val, default=0):
    try:
        return int(float(val)) if val not in (None, "", "None") else default
    except (TypeError, ValueError):
        return default


def safe_date(val):
    if val and isinstance(val, str) and len(val) == 10 and val[4] == "-":
        return val
    return None


def row_to_tuple(row: dict) -> tuple:
    return (
        row.get("name", "Unknown"),
        row.get("nation"),
        row.get("position"),
        row.get("detailed_position"),
        safe_int(row.get("age")),
        safe_date(row.get("birth_date")),
        safe_int(row.get("matches_played")),
        safe_int(row.get("minutes_played")),
        safe_int(row.get("goals")),
        safe_int(row.get("assists")),
        safe_int(row.get("penalties_scored")),
        safe_int(row.get("yellow_cards")),
        safe_int(row.get("red_cards")),
        row.get("team_name"),
        row.get("team_code"),
        row.get("team_logo_url"),
        row.get("player_id"),
        row.get("player_image_url"),
        row.get("season", "unknown"),
        datetime.now(),
    )


def load_file(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with open(path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    elif suffix == ".json":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if "all_results" in data:
            # Raw scraper format
            flat = []
            for batch in data["all_results"]:
                flat.extend(batch if isinstance(batch, list) else [batch])
            return flat
        raise ValueError(f"Unexpected JSON structure in {path}")
    raise ValueError(f"Unsupported format: {suffix}")


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def seed(file_path: Path, batch_size: int = DEFAULT_BATCH, dry_run: bool = False) -> None:
    if not file_path.exists():
        raise FileNotFoundError(f"Input not found: {file_path}")

    rows = load_file(file_path)
    log.info(f"Loaded {len(rows)} rows from {file_path}")

    # Season breakdown
    seasons: dict[str, int] = {}
    for r in rows:
        s = r.get("season", "unknown")
        seasons[s] = seasons.get(s, 0) + 1
    for s, count in sorted(seasons.items()):
        log.info(f"  {s}: {count} players")

    if dry_run:
        log.info("Dry run — no DB writes.")
        return

    conn = get_connection()
    cur  = conn.cursor()

    try:
        log.info("Ensuring schema…")
        cur.execute(CREATE_TABLE_SQL)
        conn.commit()

        tuples   = [row_to_tuple(r) for r in rows]
        total    = len(tuples)
        inserted = 0

        for i in range(0, total, batch_size):
            batch = tuples[i: i + batch_size]
            execute_values(cur, UPSERT_SQL, batch)
            conn.commit()
            inserted += len(batch)
            log.info(f"  {inserted}/{total} rows upserted")

        log.info(f"Done. {total} rows in player_stats.")

    except Exception as e:
        conn.rollback()
        log.error(f"DB error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Seed UEFA stats into PostgreSQL")
    parser.add_argument("--file",       type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--batch-size", type=int,  default=DEFAULT_BATCH)
    parser.add_argument("--dry-run",    action="store_true")
    args = parser.parse_args()
    seed(args.file, batch_size=args.batch_size, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
