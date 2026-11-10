"""The snapshot of the 2026 season's races: what scripts/build_snapshot.py wrote, loaded for analysis.

Nothing here calls an API. See docs/adr/0001.
"""

import gzip
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data"


class DataError(Exception):
    """The request names something the snapshot doesn't have. The message is written for the model."""


@dataclass
class Race:
    """One race's tables. Times are epoch seconds; laps are keyed by driver number."""

    info: dict
    laps: dict[int, list[dict]]
    stints: list[dict] = field(default_factory=list)
    pits: list[dict] = field(default_factory=list)
    race_control: list[dict] = field(default_factory=list)
    overtakes: list[dict] = field(default_factory=list)
    positions: list[dict] = field(default_factory=list)
    weather: list[dict] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.info['name']} (round {self.info['round']})"

    def driver(self, who: str | int) -> dict:
        """Find a driver by three-letter code, car number or name. Raises DataError with the valid codes."""
        wanted = str(who).strip().lower()
        for driver in self.info["drivers"]:
            names = {driver["code"].lower(), str(driver["number"]), driver["name"].lower()}
            names.add(driver["name"].split()[-1].lower())
            if wanted in names:
                return driver
        codes = ", ".join(driver["code"] for driver in self.info["drivers"])
        raise DataError(f"No driver '{who}' in the {self.label}. Drivers in this race: {codes}.")

    def code(self, number: int) -> str:
        for driver in self.info["drivers"]:
            if driver["number"] == number:
                return driver["code"]
        return f"#{number}"


@lru_cache(maxsize=1)
def list_races() -> list[dict]:
    """Every race in the snapshot, in calendar order."""
    path = DATA / "races.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


@lru_cache(maxsize=32)
def load_race(race_round: int) -> Race:
    for info in list_races():
        if info["round"] == race_round:
            break
    else:
        rounds = ", ".join(f"{race['round']} ({race['name']})" for race in list_races())
        raise DataError(f"No round {race_round} in the 2026 snapshot. Rounds available: {rounds}.")
    with gzip.open(DATA / "races" / f"{race_round:02d}.json.gz", "rt", encoding="utf-8") as f:
        tables = json.load(f)
    # The race length is the winner's lap count: the timing feed sometimes logs a stray extra lap.
    classified = [driver["laps_completed"] for driver in info["drivers"] if driver.get("laps_completed")]
    info = {**info, "total_laps": max(classified, default=info["total_laps"])}
    return Race(
        info=info,
        laps={int(number): laps for number, laps in tables["laps"].items()},
        stints=tables["stints"],
        pits=tables["pits"],
        race_control=tables["race_control"],
        overtakes=tables["overtakes"],
        positions=tables["positions"],
        weather=tables["weather"],
    )
