"""Hand-built races for tests: a few laps with known times, so expected values can be worked out by hand."""

from f1data import Race

START = 1_000_000.0


def make_race(lap_times: dict[str, list[float]], **tables) -> Race:
    """A race where every driver starts lap 1 at the same instant and runs the lap times given.

    lap_times: driver code -> lap durations in seconds. Car numbers are assigned 1, 2, 3, ... in order.
    """
    drivers = []
    laps = {}
    for number, (code, times) in enumerate(lap_times.items(), start=1):
        drivers.append(
            {"number": number, "code": code, "name": f"Driver {code.title()}", "team": f"Team {number}",
             "grid": number, "position": number, "status": "Finished", "laps_completed": len(times)}
        )
        start = START
        laps[number] = []
        for lap, duration in enumerate(times, start=1):
            laps[number].append({"lap": lap, "start": start, "duration": duration,
                                 "s1": None, "s2": None, "s3": None, "pit_out": False, "speed_trap": None})
            start += duration
    info = {"round": 99, "name": "Test Grand Prix", "date": "2026-01-01", "drivers": drivers,
            "total_laps": max(len(times) for times in lap_times.values())}
    return Race(info=info, laps=laps, **tables)


def at(race: Race, code: str, lap: int, fraction: float = 0.5) -> float:
    """The time when a driver is a given fraction of the way through a lap."""
    row = race.laps[race.driver(code)["number"]][lap - 1]
    return row["start"] + fraction * row["duration"]
