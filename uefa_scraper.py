"""
UEFA Champions League Player Stats Scraper
==========================================
Scrapes player statistics from the official UEFA stats API across multiple seasons.
Uses Selenium + Chrome DevTools performance logging to intercept the API responses.

Usage:
    python uefa_scraper.py                          # Scrape all seasons
    python uefa_scraper.py --seasons 2025           # Single season
    python uefa_scraper.py --seasons 2025 2024      # Multiple seasons
    python uefa_scraper.py --output-dir ./output    # Custom output directory
"""

import json
import time
import csv
import os
import argparse
import logging
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SEASONS = {
    "2024-25": 2025,
    "2023-24": 2024,
    "2022-23": 2023,
    "2021-22": 2022,
    "2020-21": 2021,
}

STATS_FIELDS = ",".join([
    "minutes_played_official",
    "matches_appearance",
    "goals",
    "assists",
    "penalty_scored",
    "yellow_cards",
    "red_cards",
])

OUTPUT_FIELDS = [
    "name", "nation", "position", "detailed_position",
    "age", "birth_date",
    "matches_played", "minutes_played",
    "goals", "assists", "penalties_scored",
    "yellow_cards", "red_cards",
    "team_name", "team_code", "team_logo_url",
    "player_id", "player_image_url",
    "season",
]

BATCH_SIZE      = 15
REQUEST_DELAY   = 1.5
PAGE_LOAD_WAIT  = 2.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Browser setup
# ---------------------------------------------------------------------------

def create_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    # webdriver_manager handles download automatically — no hardcoded path
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    return driver


# ---------------------------------------------------------------------------
# Response interception
# ---------------------------------------------------------------------------

def extract_json_from_logs(driver: webdriver.Chrome) -> dict | None:
    logs = driver.get_log("performance")
    for log_entry in logs:
        try:
            message = json.loads(log_entry["message"])["message"]
            if (
                message.get("method") == "Network.responseReceived"
                and "json" in message.get("params", {})
                    .get("response", {})
                    .get("mimeType", "")
            ):
                request_id = message["params"]["requestId"]
                response = driver.execute_cdp_cmd(
                    "Network.getResponseBody", {"requestId": request_id}
                )
                body = response.get("body", "")
                if body:
                    return json.loads(body)
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Data parsing — all field name / team name bugs fixed here
# ---------------------------------------------------------------------------

def safe_int(val, default=0) -> int:
    try:
        return int(float(val)) if val not in (None, "", "None") else default
    except (TypeError, ValueError):
        return default


def parse_player_entry(entry: dict, season_label: str) -> dict:
    player     = entry.get("player", {})
    team       = entry.get("team", {})
    statistics = entry.get("statistics", [])

    # stats lookup: stat_name → value string
    stats = {s["name"]: s.get("value", "0") for s in statistics}

    # BUG FIX: use displayOfficialName ("Paris Saint-Germain"), not displayName ("Paris")
    team_t = team.get("translations", {})
    team_name = (
        team_t.get("displayOfficialName", {}).get("EN")
        or team_t.get("displayName", {}).get("EN")
        or team.get("internationalName", "Unknown")
    )

    player_t = player.get("translations", {})
    nation   = player_t.get("countryName", {}).get("EN", "Unknown")

    return {
        "name":               player.get("internationalName", "Unknown"),
        "nation":             nation,
        "position":           player.get("fieldPosition", "UNKNOWN"),
        "detailed_position":  player.get("detailedFieldPosition", "UNKNOWN"),
        "age":                safe_int(player.get("age")),
        "birth_date":         player.get("birthDate"),
        "matches_played":     safe_int(stats.get("matches_appearance",       0)),
        "minutes_played":     safe_int(stats.get("minutes_played_official",  0)),
        "goals":              safe_int(stats.get("goals",                    0)),
        "assists":            safe_int(stats.get("assists",                  0)),
        "penalties_scored":   safe_int(stats.get("penalty_scored",           0)),
        "yellow_cards":       safe_int(stats.get("yellow_cards",             0)),
        "red_cards":          safe_int(stats.get("red_cards",                0)),
        "team_name":          team_name,
        "team_code":          team.get("teamCode", ""),
        "team_logo_url":      team.get("mediumLogoUrl", ""),
        "player_id":          player.get("id", ""),
        "player_image_url":   player.get("imageUrl", ""),
        "season":             season_label,
    }


# ---------------------------------------------------------------------------
# Season scraping with auto-pagination
# ---------------------------------------------------------------------------

def build_url(season_year: int, offset: int) -> str:
    return (
        f"https://compstats.uefa.com/v1/player-ranking"
        f"?competitionId=1"
        f"&limit={BATCH_SIZE}"
        f"&optionalFields=PLAYER%2CTEAM"
        f"&order=DESC"
        f"&phase=TOURNAMENT"
        f"&seasonYear={season_year}"
        f"&stats={STATS_FIELDS}"
        f"&offset={offset}"
    )


def scrape_season(driver: webdriver.Chrome, season_label: str, season_year: int) -> list[dict]:
    players       = []
    total_players = None
    offset        = 0
    seen_ids      = set()

    log.info(f"Starting season {season_label} (year={season_year})")

    while True:
        url = build_url(season_year, offset)
        log.info(f"  offset {offset}" + (f" / {total_players}" if total_players else ""))

        try:
            driver.get(url)
            time.sleep(PAGE_LOAD_WAIT)

            raw = extract_json_from_logs(driver)
            if not raw:
                log.warning(f"  No JSON at offset {offset} — stopping season")
                break

            # Read total from first response so pagination is not hardcoded
            if total_players is None:
                total_players = raw.get("total") or raw.get("count")
                if total_players:
                    log.info(f"  Total for {season_label}: {total_players}")
                else:
                    log.warning("  No 'total' field — will stop on empty batch")

            batch = raw.get("results") or raw.get("data") or []

            if not batch:
                log.info(f"  Empty batch at offset {offset} — season complete")
                break

            for entry in batch:
                pid = entry.get("playerId") or entry.get("player", {}).get("id")
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                players.append(parse_player_entry(entry, season_label))

            offset += BATCH_SIZE
            if total_players and offset >= int(total_players):
                log.info(f"  Reached total ({total_players}) — season complete")
                break

        except Exception as e:
            log.error(f"  Error at offset {offset}: {e}")
            offset += BATCH_SIZE
            if total_players and offset >= int(total_players):
                break

        time.sleep(REQUEST_DELAY)

    log.info(f"  Collected {len(players)} unique players for {season_label}")
    return players


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def save_csv(players: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=OUTPUT_FIELDS, quoting=csv.QUOTE_MINIMAL, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(players)
    log.info(f"Saved CSV  → {path}  ({len(players)} rows)")


def save_json(players: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(players, f, ensure_ascii=False, indent=2)
    log.info(f"Saved JSON → {path}  ({len(players)} records)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape UEFA Champions League player stats")
    parser.add_argument(
        "--seasons", nargs="+", type=int,
        default=list(SEASONS.values()),
        help="Season years to scrape (e.g. 2025 2024). Defaults to all configured seasons.",
    )
    parser.add_argument(
        "--output-dir", type=str, default="./output",
        help="Directory for output files (default: ./output)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    year_to_label = {v: k for k, v in SEASONS.items()}
    requested = [
        (year_to_label[y], y) for y in args.seasons if y in year_to_label
    ]

    if not requested:
        log.error("No valid seasons. Check SEASONS config.")
        return

    log.info(f"Scraping: {[label for label, _ in requested]}")

    driver      = create_driver()
    all_players = []

    try:
        for season_label, season_year in requested:
            season_players = scrape_season(driver, season_label, season_year)
            all_players.extend(season_players)

            safe_label = season_label.replace("/", "-")
            save_csv( season_players, output_dir / f"ucl_stats_{safe_label}.csv")
            save_json(season_players, output_dir / f"ucl_stats_{safe_label}.json")
    finally:
        driver.quit()

    if all_players:
        save_csv( all_players, output_dir / "ucl_stats_all_seasons.csv")
        save_json(all_players, output_dir / "ucl_stats_all_seasons.json")

        # Compact frontend JSON (no internal IDs or image URLs)
        frontend_fields = [
            "name", "nation", "position", "age",
            "matches_played", "minutes_played",
            "goals", "assists", "penalties_scored",
            "yellow_cards", "red_cards",
            "team_name", "season",
        ]
        frontend_data = [{k: p[k] for k in frontend_fields} for p in all_players]
        save_json(frontend_data, output_dir / "uefa_stats.json")
        log.info("All done.")
    else:
        log.warning("No player data collected.")


if __name__ == "__main__":
    main()
