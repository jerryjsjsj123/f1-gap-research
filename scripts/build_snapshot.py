"""Build the offline snapshot of the 2026 season's races from OpenF1 and Jolpica.

    uv run scripts/build_snapshot.py            # fetch races that aren't in the snapshot yet
    uv run scripts/build_snapshot.py --force    # refetch everything

The deployed agent never calls these APIs: it reads the files this script writes (see
docs/adr/0001). After a new race, run this again and push.

Output:
    data/races.json                 one entry per race: names, date, drivers, grid, result
    data/races/<round>.json.gz      that race's laps, stints, pit stops, race control, ...
"""

import gzip
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

SEASON = 2026
OPENF1 = "https://api.openf1.org/v1"
JOLPICA = "https://api.jolpi.ca/ergast/f1"
DATA = Path(__file__).resolve().parent.parent / "data"

# OpenF1's free tier allows 30 requests a minute.
OPENF1_PAUSE = 2.2
JOLPICA_PAUSE = 0.5


def get(url: str, params: dict | None = None, pause: float = OPENF1_PAUSE):
    """GET a JSON document, waiting out rate limits."""
    for attempt in range(6):
        response = httpx.get(url, params=params, timeout=60, headers={"User-Agent": "f1-gap-research snapshot"})
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(15 * (attempt + 1))
            continue
        time.sleep(pause)
        if response.status_code == 404:  # OpenF1 answers 404 when a query matches nothing
            return []
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"Gave up on {url} {params}")


def epoch(iso: str | None) -> float | None:
    return round(datetime.fromisoformat(iso).timestamp(), 3) if iso else None


def openf1(endpoint: str, session_key: int) -> list[dict]:
    rows = get(f"{OPENF1}/{endpoint}", {"session_key": session_key})
    return rows if isinstance(rows, list) else []


def fetch_race(session: dict, meeting: dict, jolpica_race: dict) -> tuple[dict, dict]:
    """Return (index entry, race tables) for one race."""
    key = session["session_key"]
    race_round = int(jolpica_race["round"])

    laps: dict[str, list[dict]] = {}
    for row in openf1("laps", key):
        laps.setdefault(str(row["driver_number"]), []).append(
            {
                "lap": row["lap_number"],
                "start": epoch(row["date_start"]),
                "duration": row["lap_duration"],
                "s1": row["duration_sector_1"],
                "s2": row["duration_sector_2"],
                "s3": row["duration_sector_3"],
                "pit_out": bool(row["is_pit_out_lap"]),
                "speed_trap": row["st_speed"],
            }
        )
    for driver_laps in laps.values():
        driver_laps.sort(key=lambda lap: lap["lap"])

    stints = [
        {
            "driver": row["driver_number"],
            "stint": row["stint_number"],
            "lap_start": row["lap_start"],
            "lap_end": row["lap_end"],
            "compound": row["compound"],
            "tyre_age_at_start": row["tyre_age_at_start"],
        }
        for row in openf1("stints", key)
    ]
    pits = [
        {
            "driver": row["driver_number"],
            "lap": row["lap_number"],
            "time": epoch(row["date"]),
            "lane_duration": row.get("lane_duration") or row.get("pit_duration"),
            "stop_duration": row.get("stop_duration"),
        }
        for row in openf1("pit", key)
    ]
    race_control = [
        {
            "time": epoch(row["date"]),
            "lap": row["lap_number"],
            "category": row["category"],
            "flag": row["flag"],
            "scope": row["scope"],
            "sector": row["sector"],
            "driver": row["driver_number"],
            "message": row["message"],
        }
        for row in openf1("race_control", key)
    ]
    overtakes = [
        {
            "time": epoch(row["date"]),
            "overtaking": row["overtaking_driver_number"],
            "overtaken": row["overtaken_driver_number"],
            "position": row["position"],
        }
        for row in openf1("overtakes", key)
    ]
    positions = [
        {"time": epoch(row["date"]), "driver": row["driver_number"], "position": row["position"]}
        for row in openf1("position", key)
    ]
    weather = [
        {
            "time": epoch(row["date"]),
            "rainfall": row["rainfall"],
            "track_temperature": row["track_temperature"],
            "air_temperature": row["air_temperature"],
        }
        for row in openf1("weather", key)
    ]

    openf1_results = {row["driver_number"]: row for row in openf1("session_result", key)}
    jolpica_results = {int(row["number"]): row for row in jolpica_race["Results"]}
    drivers = []
    for row in openf1("drivers", key):
        number = row["driver_number"]
        official = jolpica_results.get(number, {})
        timing = openf1_results.get(number, {})
        drivers.append(
            {
                "number": number,
                "code": row["name_acronym"],
                "name": f"{row['first_name']} {row['last_name']}",
                "team": row["team_name"],
                "team_colour": row.get("team_colour"),
                # Grid and classification are Jolpica's: OpenF1 has no grid for a race session.
                "grid": int(official["grid"]) if official.get("grid") else None,
                "position": int(official["position"]) if official.get("position") else timing.get("position"),
                "status": official.get("status"),
                "laps_completed": int(official["laps"]) if official.get("laps") else timing.get("number_of_laps"),
                "race_time": timing.get("duration"),
            }
        )
    drivers.sort(key=lambda driver: (driver["position"] is None, driver["position"] or 0, driver["number"]))

    entry = {
        "round": race_round,
        "name": jolpica_race["raceName"],
        "official_name": meeting.get("meeting_name"),
        "country": session["country_name"],
        "location": session["location"],
        "circuit": jolpica_race["Circuit"]["circuitName"],
        "date": jolpica_race["date"],
        "total_laps": max((driver["laps_completed"] or 0 for driver in drivers), default=0),
        "openf1_session_key": key,
        "jolpica_url": f"{JOLPICA}/{SEASON}/{race_round}/results.json",
        "drivers": drivers,
    }
    tables = {
        "round": race_round,
        "start_time": epoch(session["date_start"]),
        "laps": laps,
        "stints": stints,
        "pits": pits,
        "race_control": race_control,
        "overtakes": overtakes,
        "positions": positions,
        "weather": weather,
    }
    return entry, tables


def main() -> None:
    force = "--force" in sys.argv
    (DATA / "races").mkdir(parents=True, exist_ok=True)
    index_path = DATA / "races.json"
    index = {entry["round"]: entry for entry in json.loads(index_path.read_text())} if index_path.exists() else {}

    sessions = get(f"{OPENF1}/sessions", {"year": SEASON, "session_name": "Race"})
    meetings = {m["meeting_key"]: m for m in get(f"{OPENF1}/meetings", {"year": SEASON})}
    calendar = get(f"{JOLPICA}/{SEASON}/races.json", {"limit": 100}, JOLPICA_PAUSE)["MRData"]["RaceTable"]["Races"]
    rounds_by_date = {race["date"]: race["round"] for race in calendar}

    for session in sessions:
        date = session["date_start"][:10]
        race_round = rounds_by_date.get(date)
        if race_round is None:
            print(f"skip   {date} {session['country_name']}: not on Jolpica's calendar (cancelled)")
            continue
        if int(race_round) in index and not force:
            print(f"have   round {race_round} {date} {session['country_name']}")
            continue
        results = get(f"{JOLPICA}/{SEASON}/{race_round}/results.json", {"limit": 100}, JOLPICA_PAUSE)
        races = results["MRData"]["RaceTable"]["Races"]
        if not races:
            print(f"skip   round {race_round} {date} {session['country_name']}: no results yet")
            continue
        entry, tables = fetch_race(session, meetings.get(session["meeting_key"], {}), races[0])
        if not tables["laps"]:
            print(f"skip   round {race_round} {date} {session['country_name']}: no lap data")
            continue
        with gzip.open(DATA / "races" / f"{entry['round']:02d}.json.gz", "wt", encoding="utf-8") as f:
            json.dump(tables, f, separators=(",", ":"))
        index[entry["round"]] = entry
        # Write the index after every race so an interrupted run can resume.
        index_path.write_text(json.dumps([index[r] for r in sorted(index)], indent=1, ensure_ascii=False))
        print(f"wrote  round {entry['round']} {date} {entry['name']}: {entry['total_laps']} laps, {len(entry['drivers'])} drivers")


if __name__ == "__main__":
    main()
