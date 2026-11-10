"""The arithmetic of a race: the Gap between a Driver Pair, where it changed, and what was going on.

Every number the agents quote is computed here, from the snapshot. See CONTEXT.md for the terms.
"""

import re

from f1data import DataError, Race


def _line_times(race: Race, number: int) -> dict[int, float]:
    """lap -> when the driver crossed the line at the end of it."""
    laps = race.laps.get(number, [])
    times = {}
    for row, following in zip(laps, laps[1:] + [None]):
        if following and following["lap"] == row["lap"] + 1 and following["start"]:
            times[row["lap"]] = following["start"]
        elif row["start"] and row["duration"]:
            times[row["lap"]] = row["start"] + row["duration"]
    return times


def _pair(race: Race, a: str, b: str) -> tuple[dict, dict]:
    driver_a, driver_b = race.driver(a), race.driver(b)
    if driver_a["number"] == driver_b["number"]:
        raise DataError("A Driver Pair needs two different drivers.")
    return driver_a, driver_b


def gap_series(race: Race, a: str, b: str) -> dict:
    """The Gap at the end of every lap both drivers completed. Positive: A is behind B."""
    driver_a, driver_b = _pair(race, a, b)
    times_a, times_b = _line_times(race, driver_a["number"]), _line_times(race, driver_b["number"])
    points = []
    previous = 0.0
    for lap in sorted(set(times_a) & set(times_b)):
        gap = round(times_a[lap] - times_b[lap], 3)
        points.append({"lap": lap, "gap": gap, "change": round(gap - previous, 3)})
        previous = gap
    last_common = points[-1]["lap"] if points else 0

    # Say so when the Gap covers less than the race: a retirement, or a driver who was lapped.
    notes = []
    for driver, times in ((driver_a, times_a), (driver_b, times_b)):
        completed = max(times, default=0)
        if completed < race.info["total_laps"]:
            notes.append(
                f"{driver['code']} completed {completed} laps of {race.info['total_laps']} "
                f"(classified: {driver.get('status') or 'unknown'})."
            )
    if notes:
        notes.append(f"The Gap is only defined up to lap {last_common}, the last lap both drivers completed.")
    return {
        "a": driver_a["code"],
        "b": driver_b["code"],
        "last_common_lap": last_common,
        "points": points,
        "note": " ".join(notes),
    }


# --- What was happening on a lap: pit stops and neutralisations ---


def _lap_intervals(race: Race, number: int) -> dict[int, tuple[float, float]]:
    """lap -> (when the driver started it, when they finished it)."""
    ends = _line_times(race, number)
    return {row["lap"]: (row["start"], ends[row["lap"]]) for row in race.laps.get(number, []) if row["lap"] in ends and row["start"]}


def neutralisations(race: Race) -> list[dict]:
    """Safety Car, Virtual Safety Car and red flag periods, from race control's messages."""
    periods = []
    open_period = None
    for message in sorted(race.race_control, key=lambda row: row["time"] or 0):
        text = (message.get("message") or "").upper()
        category = message.get("category")
        stopped = message.get("flag") == "RED" or (category == "SessionStatus" and "ABORTED" in text)
        kind = None
        if stopped:
            # A stoppage is sometimes announced twice: by the red flag and by the session status.
            kind = None if open_period and open_period["kind"] == "Red Flag" else "Red Flag"
        elif category == "SafetyCar" and "DEPLOYED" in text:
            kind = "Virtual Safety Car" if ("VSC" in text or "VIRTUAL" in text) else "Safety Car"
        resumed = message.get("flag") == "GREEN" or (category == "SessionStatus" and "STARTED" in text)
        ends = (category == "SafetyCar" and ("ENDING" in text or "IN THIS LAP" in text)) or (
            open_period and open_period["kind"] == "Red Flag" and resumed
        )
        if kind:
            if open_period:  # e.g. a Safety Car that turns into a red flag
                open_period.update(end_time=message["time"], lap_end=message["lap"])
                periods.append(open_period)
            open_period = {"kind": kind, "start_time": message["time"], "lap_start": message["lap"]}
        elif ends and open_period:
            open_period.update(end_time=message["time"], lap_end=message["lap"])
            periods.append(open_period)
            open_period = None
    if open_period:  # never withdrawn: the race ended under it
        open_period.update(end_time=float("inf"), lap_end=race.info["total_laps"])
        periods.append(open_period)
    return periods


# After a stoppage the field crawls out of the pit lane and re-forms the grid. Those laps are this much
# slower than a racing lap, and the Gap swings on them without anyone racing.
RESTART_LAP_FACTOR = 1.3


def _neutralised_laps(race: Race, number: int) -> dict[int, str]:
    """lap -> the kind of neutralisation that was in force during some of it, for one driver."""
    intervals = _lap_intervals(race, number)
    laps = {}
    for lap, (start, end) in intervals.items():
        for period in neutralisations(race):
            if start <= period["end_time"] and end >= period["start_time"]:
                laps[lap] = period["kind"]
    durations = {lap: end - start for lap, (start, end) in intervals.items()}
    typical = _median(list(durations.values())) if durations else 0.0
    for lap in sorted(lap for lap, kind in laps.items() if kind == "Red Flag"):
        following = lap + 1
        while following in durations and following not in laps and durations[following] > RESTART_LAP_FACTOR * typical:
            laps[following] = "Red Flag"
            following += 1
    return laps


def _pit_laps(race: Race, number: int) -> list[int]:
    """The laps a driver ended in the pit lane (their in-laps)."""
    return sorted(stop["lap"] for stop in race.pits if stop["driver"] == number and stop["lap"])


# --- Candidate Gap Phases ---

# A lap that swings the Gap this much more than its neighbours is a one-off, not a trend.
JUMP_SECONDS = 4.0
# Two pit stops this many laps apart or fewer are one pit cycle.
PIT_CYCLE_LAPS = 8
# Green-flag running splits where the rate of change differs by at least this much, and clearly.
MIN_RATE_DIFFERENCE = 0.15
MIN_PHASE_LAPS = 3


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _split_by_rate(changes: list[float]) -> list[int]:
    """Where the Gap's rate of change shifts within one stretch of laps. Returns the lengths of the pieces."""
    n = len(changes)
    best = None
    for cut in range(MIN_PHASE_LAPS, n - MIN_PHASE_LAPS + 1):
        left, right = changes[:cut], changes[cut:]
        mean_left, mean_right = sum(left) / len(left), sum(right) / len(right)
        difference = abs(mean_left - mean_right)
        if difference < MIN_RATE_DIFFERENCE:
            continue
        spread = sum((x - mean_left) ** 2 for x in left) + sum((x - mean_right) ** 2 for x in right)
        noise = max((spread / max(n - 2, 1)) ** 0.5, 0.05)
        score = difference / (noise * (1 / len(left) + 1 / len(right)) ** 0.5)
        if score >= 3.5 and (best is None or score > best[0]):
            best = (score, cut)
    if best is None:
        return [n]
    cut = best[1]
    return _split_by_rate(changes[:cut]) + _split_by_rate(changes[cut:])


def gap_phases(race: Race, a: str, b: str) -> dict:
    """Split the race into candidate Gap Phases: stretches of laps over which the Gap behaved one way.

    The first lap, pit cycles and neutralised laps are always their own phases. Green-flag running
    in between splits where the Gap's rate of change shifts, and one-off jumps stand alone.
    """
    series = gap_series(race, a, b)
    driver_a, driver_b = race.driver(a), race.driver(b)
    code_a, code_b = series["a"], series["b"]
    points = {point["lap"]: point for point in series["points"]}
    laps = sorted(points)
    if not laps:
        raise DataError(f"{code_a} and {code_b} did not complete a lap together in the {race.label}.")

    neutral = {**_neutralised_laps(race, driver_b["number"]), **_neutralised_laps(race, driver_a["number"])}
    pits_a, pits_b = _pit_laps(race, driver_a["number"]), _pit_laps(race, driver_b["number"])
    # A stop costs time on the in-lap and the out-lap.
    pit_window = {lap for stop in pits_a + pits_b for lap in (stop, stop + 1)}
    stops = sorted(set(pits_a + pits_b))
    for earlier, later in zip(stops, stops[1:]):
        if later - earlier <= PIT_CYCLE_LAPS:
            pit_window.update(range(earlier, later + 2))

    kinds = {}
    for lap in laps:
        if lap == 1:
            kinds[lap] = "start"
        elif lap in neutral:
            kinds[lap] = "neutralised"
        elif lap in pit_window:
            kinds[lap] = "pit_cycle"
        else:
            kinds[lap] = "green"
    # A pit stop taken under a Safety Car belongs to the neutralised phase around it.
    for lap in laps:
        if kinds[lap] == "pit_cycle" and "neutralised" in (kinds.get(lap - 1), kinds.get(lap + 1)):
            kinds[lap] = "neutralised"
    green = [points[lap]["change"] for lap in laps if kinds[lap] == "green"]
    typical = _median(green) if green else 0.0
    for lap in laps:
        if kinds[lap] == "green" and abs(points[lap]["change"] - typical) > JUMP_SECONDS:
            kinds[lap] = "jump"

    runs: list[list[int]] = []
    for lap in laps:
        if runs and kinds[runs[-1][-1]] == kinds[lap] and runs[-1][-1] == lap - 1:
            runs[-1].append(lap)
        else:
            runs.append([lap])
    pieces: list[list[int]] = []
    for run in runs:
        if kinds[run[0]] != "green":
            pieces.append(run)
            continue
        start = 0
        for length in _split_by_rate([points[lap]["change"] for lap in run]):
            pieces.append(run[start : start + length])
            start += length

    phases = []
    for index, piece in enumerate(pieces, start=1):
        first, last = piece[0], piece[-1]
        change = round(sum(points[lap]["change"] for lap in piece), 3)
        events = []
        for code, stops_of in ((code_a, pits_a), (code_b, pits_b)):
            events += [f"{code} pit stop at the end of lap {stop}" for stop in stops_of if first <= stop <= last]
        events += sorted({f"{neutral[lap]}" for lap in piece if lap in neutral})
        who = f"{code_a} lost {abs(change):.1f}s to {code_b}" if change > 0 else f"{code_a} gained {abs(change):.1f}s on {code_b}"
        phases.append(
            {
                "id": f"P{index}",
                "lap_start": first,
                "lap_end": last,
                "kind": kinds[first],
                "gap_before": round(points[first]["gap"] - points[first]["change"], 3),
                "gap_after": points[last]["gap"],
                "change": change,
                "per_lap": round(change / len(piece), 3),
                "events": events,
                "summary": f"Laps {first}-{last}: {who} ({change / len(piece):+.2f}s per lap).",
            }
        )
    return {**series, "final_gap": series["points"][-1]["gap"], "phases": phases}


# --- Evidence: what every tool call leaves behind ---


class EvidenceLog:
    """The Evidence gathered in one conversation. Reports cite these by id: [E3]."""

    def __init__(self):
        self.items: dict[str, dict] = {}

    def add(self, *, source: str, race: Race, drivers: list[str], lap_start: int | None, lap_end: int | None,
            title: str, columns: list[str], rows: list[list]) -> str:
        evidence_id = f"E{len(self.items) + 1}"
        self.items[evidence_id] = {
            "id": evidence_id,
            "source": source,
            "race": race.label,
            "drivers": drivers,
            "lap_start": lap_start,
            "lap_end": lap_end,
            "title": title,
            "columns": columns,
            "rows": rows,
        }
        return evidence_id

    def get(self, evidence_id: str) -> dict | None:
        return self.items.get(evidence_id)


def _lap_range(race: Race, a: str, b: str, lap_start: int, lap_end: int) -> dict:
    """The Gap series for a pair, after checking the lap range lies inside the laps both completed."""
    series = gap_series(race, a, b)
    last = series["last_common_lap"]
    if not (1 <= lap_start <= lap_end <= last):
        raise DataError(
            f"Laps {lap_start}-{lap_end} are not valid for {series['a']} and {series['b']} in the {race.label}: "
            f"both drivers completed laps 1 to {last}. {series['note']}".strip()
        )
    return series


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _drift(laps: list[int], times: list[float]) -> float | None:
    """Seconds per lap by which lap times rose (positive) or fell: the slope of a straight-line fit."""
    if len(laps) < 3:
        return None
    mean_lap, mean_time = sum(laps) / len(laps), sum(times) / len(times)
    spread = sum((lap - mean_lap) ** 2 for lap in laps)
    return round(sum((lap - mean_lap) * (time - mean_time) for lap, time in zip(laps, times)) / spread, 3)


def _lost(code_a: str, code_b: str, seconds: float) -> str:
    """A Gap change in words, so nobody has to read a sign."""
    if seconds >= 0:
        return f"{code_a} lost {seconds:.1f}s to {code_b}"
    return f"{code_a} gained {-seconds:.1f}s on {code_b}"


def _short(seconds: float) -> str:
    return f"{seconds:.2f}".rstrip("0").rstrip(".")


def _lap_notes(race: Race, number: int) -> dict[int, str]:
    """lap -> why it isn't a clean racing lap for this driver."""
    notes = {1: "first lap"}
    notes.update(_neutralised_laps(race, number))
    for stop in _pit_laps(race, number):
        notes[stop] = "pit in-lap"
        notes[stop + 1] = "pit out-lap"
    return notes


def lap_pace(race: Race, a: str, b: str, lap_start: int, lap_end: int, log: EvidenceLog) -> dict:
    """Compare two drivers' lap times over a range of laps.

    Pace is compared on clean laps only: laps where neither driver pitted, the race wasn't
    neutralised, and it wasn't lap 1.
    """
    series = _lap_range(race, a, b, lap_start, lap_end)
    code_a, code_b = series["a"], series["b"]
    number_a, number_b = race.driver(a)["number"], race.driver(b)["number"]
    laps_a = {row["lap"]: row for row in race.laps[number_a]}
    laps_b = {row["lap"]: row for row in race.laps[number_b]}
    notes_a, notes_b = _lap_notes(race, number_a), _lap_notes(race, number_b)
    neutral_a, neutral_b = _neutralised_laps(race, number_a), _neutralised_laps(race, number_b)
    points = {point["lap"]: point for point in series["points"]}

    rows, clean = [], []
    by_type: dict[str, float] = {}
    for lap in range(lap_start, lap_end + 1):
        row_a, row_b = laps_a.get(lap, {}), laps_b.get(lap, {})
        time_a, time_b = row_a.get("duration"), row_b.get("duration")
        note = "; ".join(f"{code} {notes[lap]}" for code, notes in ((code_a, notes_a), (code_b, notes_b)) if lap in notes)
        difference = round(time_a - time_b, 3) if time_a and time_b else None
        if not note and difference is not None:
            clean.append(lap)
        if lap == 1:
            lap_type = "first lap"
        elif lap in neutral_a or lap in neutral_b:
            lap_type = "neutralised laps"
        elif note:
            lap_type = "pit in-laps and out-laps"
        elif difference is None:
            lap_type = "laps with a missing lap time"
        else:
            lap_type = "clean laps"
        if lap in points:
            by_type[lap_type] = by_type.get(lap_type, 0.0) + points[lap]["change"]
        rows.append([lap, time_a, time_b, difference, points[lap]["gap"] if lap in points else None, note])

    def side(code: str, laps: dict[int, dict]) -> dict:
        times = [laps[lap]["duration"] for lap in clean]
        summary = {"mean_lap": _mean(times), "best_lap": min(times, default=None), "drift_per_lap": _drift(clean, times)}
        for sector in ("s1", "s2", "s3"):
            values = [laps[lap][sector] for lap in clean if laps[lap].get(sector)]
            summary[f"mean_{sector}"] = _mean(values)
        return summary

    summary_a, summary_b = side(code_a, laps_a), side(code_b, laps_b)
    differences = [laps_a[lap]["duration"] - laps_b[lap]["duration"] for lap in clean]
    by_type = {lap_type: round(change, 3) for lap_type, change in by_type.items()}
    gap_before = round(points[lap_start]["gap"] - points[lap_start]["change"], 3)
    gap_after = points[lap_end]["gap"]
    sector_differences = {
        sector: round(summary_a[f"mean_{sector}"] - summary_b[f"mean_{sector}"], 3)
        for sector in ("s1", "s2", "s3")
        if summary_a[f"mean_{sector}"] is not None and summary_b[f"mean_{sector}"] is not None
    }
    mean_difference = _mean(differences)
    in_words = [f"Over laps {lap_start}-{lap_end} {_lost(code_a, code_b, gap_after - gap_before)}."]
    if clean:
        pace = "slower" if mean_difference >= 0 else "faster"
        outcome = "losing" if by_type.get("clean laps", 0.0) >= 0 else "gaining"
        in_words.append(
            f"On the {len(clean)} clean laps {code_a} was {_short(abs(mean_difference))}s per lap {pace} than {code_b}, "
            f"{outcome} {abs(by_type.get('clean laps', 0.0)):.1f}s."
        )
    for lap_type in ("first lap", "pit in-laps and out-laps", "neutralised laps", "laps with a missing lap time"):
        if lap_type in by_type:
            where = "the first lap" if lap_type == "first lap" else lap_type
            in_words.append(f"On {where} {_lost(code_a, code_b, by_type[lap_type])}.")
    evidence_id = log.add(
        source="OpenF1 laps", race=race, drivers=[code_a, code_b], lap_start=lap_start, lap_end=lap_end,
        title=f"Lap times, {code_a} vs {code_b}, laps {lap_start}-{lap_end}",
        columns=["lap", f"{code_a} lap time (s)", f"{code_b} lap time (s)", f"{code_a} minus {code_b} (s)",
                 f"Gap at end of lap (s, + = {code_a} behind)", "not a clean lap because"],
        rows=rows,
    )
    return {
        "evidence_id": evidence_id,
        "laps": f"{lap_start}-{lap_end}",
        "in_words": in_words,
        "gap_before": gap_before,
        "gap_after": gap_after,
        "gap_change": round(gap_after - gap_before, 3),
        "gap_change_by_lap_type": by_type,
        "clean_laps": len(clean),
        "clean_lap_numbers": clean,
        "mean_difference": mean_difference,
        "laps_a_faster": sum(1 for d in differences if d < 0),
        "laps_b_faster": sum(1 for d in differences if d > 0),
        "mean_sector_difference": sector_differences,
        code_a: summary_a,
        code_b: summary_b,
        "reading": (
            f"Positive differences mean {code_a} was slower than {code_b}. drift_per_lap is how much a driver's "
            "own lap times rose (+) or fell (-) per lap across the clean laps."
        ),
        "not_clean": [f"lap {row[0]}: {row[5]}" for row in rows if row[5]],
    }


def tyres(race: Race, a: str, b: str, lap_start: int, lap_end: int, log: EvidenceLog) -> dict:
    """Which tyres each driver was on over a range of laps, and how old they were."""
    series = _lap_range(race, a, b, lap_start, lap_end)
    result: dict = {"laps": f"{lap_start}-{lap_end}"}
    rows, missing, first_ages = [], [], []
    for code in (series["a"], series["b"]):
        number = race.driver(code)["number"]
        stints = sorted((s for s in race.stints if s["driver"] == number), key=lambda s: s["lap_start"])
        used = []
        for stint in stints:
            first, last = max(stint["lap_start"], lap_start), min(stint["lap_end"], lap_end)
            if first > last:
                continue
            age = stint["tyre_age_at_start"] or 0
            used.append(
                {
                    "compound": stint["compound"],
                    "laps": f"{first}-{last}",
                    "tyre_age_at_first_lap": age + first - stint["lap_start"],
                    "tyre_age_at_last_lap": age + last - stint["lap_start"],
                }
            )
            rows.append([code, stint["stint"], stint["compound"], stint["lap_start"], stint["lap_end"], age])
        result[code] = used
        if used:
            first_ages.append(used[0]["tyre_age_at_first_lap"])
        else:
            missing.append(f"No tyre data for {code} on laps {lap_start}-{lap_end}.")
    if len(first_ages) == 2:
        result["tyre_age_difference_at_first_lap"] = first_ages[0] - first_ages[1]
        result["reading"] = (
            f"Tyre age is in laps. A negative difference means {series['a']}'s tyres were younger than "
            f"{series['b']}'s at lap {lap_start}. The data has no tyre temperatures or wear measurements."
        )
    if missing:
        result["missing"] = " ".join(missing)
    result["evidence_id"] = log.add(
        source="OpenF1 stints", race=race, drivers=[series["a"], series["b"]], lap_start=lap_start, lap_end=lap_end,
        title=f"Tyre stints, {series['a']} and {series['b']}, covering laps {lap_start}-{lap_end}",
        columns=["driver", "stint", "compound", "first lap of stint", "last lap of stint", "tyre age at start of stint (laps)"],
        rows=rows,
    )
    return result


def _reference_lap(race: Race, number: int, around: int) -> float | None:
    """A driver's typical clean lap near a given lap: the median of up to five clean laps either side."""
    notes = _lap_notes(race, number)
    clean = {row["lap"]: row["duration"] for row in race.laps[number] if row["duration"] and row["lap"] not in notes}
    before = [clean[lap] for lap in sorted(clean) if lap < around][-5:]
    after = [clean[lap] for lap in sorted(clean) if lap > around + 1][:5]
    return _median(before + after) if before + after else None


def pit_stops(race: Race, a: str, b: str, lap_start: int, lap_end: int, log: EvidenceLog) -> dict:
    """Each driver's pit stops in a range of laps, and what each one cost."""
    series = _lap_range(race, a, b, lap_start, lap_end)
    points = {point["lap"]: point for point in series["points"]}
    result: dict = {"laps": f"{lap_start}-{lap_end}"}
    rows = []
    for code in (series["a"], series["b"]):
        number = race.driver(code)["number"]
        laps = {row["lap"]: row for row in race.laps[number]}
        neutral = _neutralised_laps(race, number)
        stops = []
        for stop in sorted((s for s in race.pits if s["driver"] == number), key=lambda s: s["lap"] or 0):
            in_lap = stop["lap"]
            if in_lap is None or not (lap_start <= in_lap <= lap_end):
                continue
            in_time = laps.get(in_lap, {}).get("duration")
            out_time = laps.get(in_lap + 1, {}).get("duration")
            reference = _reference_lap(race, number, in_lap)
            lost = round(in_time + out_time - 2 * reference, 3) if in_time and out_time and reference else None
            if neutral.get(in_lap):
                lost = None  # the laps around it were slow for everyone: they don't measure the stop
            stops.append(
                {
                    "in_lap": in_lap,
                    "pit_lane_time": stop.get("lane_duration"),
                    "stationary_time": stop.get("stop_duration"),
                    "in_lap_time": in_time,
                    "out_lap_time": out_time,
                    "time_lost_vs_own_pace": lost,
                    "under": neutral.get(in_lap),
                }
            )
            rows.append([code, in_lap, stop.get("lane_duration"), stop.get("stop_duration"), in_time, out_time, lost, neutral.get(in_lap) or ""])
        losses = [stop["time_lost_vs_own_pace"] for stop in stops if stop["time_lost_vs_own_pace"] is not None]
        result[code] = {"stops": stops, "total_time_lost": round(sum(losses), 3) if losses else 0.0}
    side_a, side_b = result[series["a"]], result[series["b"]]
    result["difference_in_time_lost"] = round(side_a["total_time_lost"] - side_b["total_time_lost"], 3)
    result["gap_before"] = round(points[lap_start]["gap"] - points[lap_start]["change"], 3)
    result["gap_after"] = points[lap_end]["gap"]
    result["gap_change"] = round(result["gap_after"] - result["gap_before"], 3)
    lost_a, lost_b = side_a["total_time_lost"], side_b["total_time_lost"]
    all_stops = side_a["stops"] + side_b["stops"]
    under = sorted({stop["under"] for stop in all_stops if stop["under"]})
    lane_a = round(sum(stop["pit_lane_time"] or 0 for stop in side_a["stops"]), 3)
    lane_b = round(sum(stop["pit_lane_time"] or 0 for stop in side_b["stops"]), 3)
    if "Red Flag" in under:
        result["in_words"] = (
            "The race was stopped by a red flag on these laps. The pit lane times recorded here are cars waiting "
            "in the pit lane during the stoppage, where teams may change tyres freely: they are not pit stops."
        )
    elif under and side_a["stops"] and side_b["stops"]:
        slower = "slower" if lane_a >= lane_b else "faster"
        who = "Both drivers pitted" if all(stop["under"] for stop in all_stops) else "At least one of these stops was"
        result["in_words"] = (
            f"{who} under a {' / '.join(under)}. {series['a']} spent {lane_a:.1f}s in the pit lane and "
            f"{series['b']} {lane_b:.1f}s: {series['a']} was {abs(lane_a - lane_b):.1f}s {slower} through the pit lane. "
            "Lap times around these stops were slow for everyone, so they say nothing about what the stops cost."
        )
    elif under:
        only, lane = (series["a"], lane_a) if side_a["stops"] else (series["b"], lane_b)
        result["in_words"] = (
            f"Only {only} pitted on these laps, under a {' / '.join(under)}, spending {lane:.1f}s in the pit lane. "
            "A stop under a neutralisation costs much less than a normal one, because the rest of the field is slow."
        )
    elif side_a["stops"] and side_b["stops"]:
        more = "more" if lost_a >= lost_b else "less"
        result["in_words"] = (
            f"Pitting on these laps cost {series['a']} {lost_a:.1f}s and {series['b']} {lost_b:.1f}s against their own "
            f"pace: {series['a']} lost {abs(lost_a - lost_b):.1f}s {more} than {series['b']}."
        )
    elif side_a["stops"] or side_b["stops"]:
        only, cost = (series["a"], lost_a) if side_a["stops"] else (series["b"], lost_b)
        result["in_words"] = f"Only {only} pitted on these laps, which cost {cost:.1f}s against their own pace."
    if under:
        result["difference_in_time_lost"] = None
    result["reading"] = (
        "time_lost_vs_own_pace is the in-lap plus the out-lap, minus two of the driver's typical clean laps nearby: "
        "what the stop cost in total, including a slow out-lap on cold tyres. It is left empty for a stop under a "
        f"Safety Car or Virtual Safety Car, which costs less because everyone else is slow too. A 'stop' recorded under a Red Flag is the "
        f"car waiting in the pit lane during the stoppage, not a normal stop. A positive difference_in_time_lost means "
        f"{series['a']} lost more time pitting than {series['b']} in these laps."
    )
    if not side_a["stops"] and not side_b["stops"]:
        result["note"] = f"Neither driver pitted on laps {lap_start}-{lap_end}."
    result["evidence_id"] = log.add(
        source="OpenF1 pit + laps", race=race, drivers=[series["a"], series["b"]], lap_start=lap_start, lap_end=lap_end,
        title=f"Pit stops, {series['a']} and {series['b']}, laps {lap_start}-{lap_end}",
        columns=["driver", "in-lap", "pit lane time (s)", "stationary time (s)", "in-lap time (s)", "out-lap time (s)",
                 "time lost vs own pace (s)", "under"],
        rows=rows,
    )
    return result


def _mentions(message: dict, driver: dict) -> bool:
    return message.get("driver") == driver["number"] or f"({driver['code']})" in (message.get("message") or "")


def race_events(race: Race, a: str, b: str, lap_start: int, lap_end: int, log: EvidenceLog) -> dict:
    """What race control did over a range of laps: neutralisations, flags, and anything about the pair."""
    series = _lap_range(race, a, b, lap_start, lap_end)
    driver_a, driver_b = race.driver(a), race.driver(b)
    in_range = [m for m in sorted(race.race_control, key=lambda m: m["time"] or 0) if m["lap"] and lap_start <= m["lap"] <= lap_end]

    periods = [
        {"kind": period["kind"], "laps": f"{period['lap_start']}-{period['lap_end']}"}
        for period in neutralisations(race)
        if period["lap_start"] <= lap_end and period["lap_end"] >= lap_start
    ]
    about = {driver["code"]: [] for driver in (driver_a, driver_b)}
    blue_flags = {driver["code"]: 0 for driver in (driver_a, driver_b)}
    yellow_laps, track_wide, rows = set(), [], []
    for message in in_range:
        text = message.get("message") or ""
        flag = message.get("flag")
        named = [driver for driver in (driver_a, driver_b) if _mentions(message, driver)]
        if flag == "BLUE":
            for driver in named:
                blue_flags[driver["code"]] += 1
            continue
        keep = False
        for driver in named:
            about[driver["code"]].append(f"lap {message['lap']}: {text}")
            keep = True
        if message.get("category") == "SafetyCar" or flag in ("RED", "CHEQUERED"):
            keep = True
        elif flag in ("YELLOW", "DOUBLE YELLOW"):
            yellow_laps.add(message["lap"])
            keep = True
        elif not named and any(word in text for word in ("LOW GRIP", "RAIN", "OVERTAKE ENABLED", "OVERTAKE DISABLED", "DRS")):
            track_wide.append(f"lap {message['lap']}: {text}")
            keep = True
        if keep:
            rows.append([message["lap"], message.get("category"), text])

    intervals = _lap_intervals(race, driver_b["number"])
    window = (intervals[lap_start][0], intervals[lap_end][1]) if lap_start in intervals and lap_end in intervals else None
    samples = [w for w in race.weather if window and window[0] <= (w["time"] or 0) <= window[1]]
    weather = None
    if samples:
        temperatures = [w["track_temperature"] for w in samples if w["track_temperature"] is not None]
        weather = {
            "rain_recorded": any(w["rainfall"] for w in samples),
            "track_temperature_range": [min(temperatures), max(temperatures)] if temperatures else None,
        }

    result = {
        "laps": f"{lap_start}-{lap_end}",
        "neutralisations": periods,
        f"about_{driver_a['code']}": about[driver_a["code"]],
        f"about_{driver_b['code']}": about[driver_b["code"]],
        "blue_flags_shown": blue_flags,
        "laps_with_yellow_flags": sorted(yellow_laps),
        "track_wide_messages": track_wide,
        "weather": weather,
        "reading": (
            "Lap numbers are the leader's lap when the message was sent. Blue flags are shown to a driver who is "
            "about to be lapped. Race control messages don't record driver mistakes, car damage or team orders."
        ),
    }
    if not periods and not rows:
        result["note"] = f"No Safety Car, Virtual Safety Car, red flag, yellow flag or message about either driver on laps {lap_start}-{lap_end}."
    elif not periods:
        result["note"] = f"No Safety Car, Virtual Safety Car or red flag on laps {lap_start}-{lap_end}."
    result["evidence_id"] = log.add(
        source="OpenF1 race_control + weather", race=race, drivers=[series["a"], series["b"]], lap_start=lap_start,
        lap_end=lap_end, title=f"Race control messages, laps {lap_start}-{lap_end}",
        columns=["lap", "category", "message"], rows=rows,
    )
    return result


TRAFFIC_SECONDS = 1.5


def track_position(race: Race, a: str, b: str, lap_start: int, lap_end: int, log: EvidenceLog) -> dict:
    """Where each driver was running over a range of laps: race position, the car ahead on track, overtakes."""
    series = _lap_range(race, a, b, lap_start, lap_end)
    line_times = {number: _line_times(race, number) for number in race.laps}
    crossings = sorted((time, number, lap) for number, times in line_times.items() for lap, time in times.items())

    result: dict = {"laps": f"{lap_start}-{lap_end}"}
    rows = []
    for code in (series["a"], series["b"]):
        number = race.driver(code)["number"]
        positions, behind, close_laps = {}, [], 0
        for lap in range(lap_start, lap_end + 1):
            time = line_times[number].get(lap)
            if time is None:
                continue
            positions[lap] = 1 + sum(1 for other, times in line_times.items() if other != number and times.get(lap, float("inf")) < time)
            ahead = None
            for crossing_time, other, other_lap in crossings:
                if crossing_time >= time:
                    break
                if other != number:
                    ahead = (crossing_time, other, other_lap)
            ahead_code, ahead_gap, relation = "", None, ""
            if ahead:
                ahead_code, ahead_gap = race.code(ahead[1]), round(time - ahead[0], 3)
                relation = "same lap" if ahead[2] == lap else ("a lap or more down" if ahead[2] < lap else "a lap or more ahead")
                if ahead_gap <= TRAFFIC_SECONDS:
                    close_laps += 1
                    behind.append(ahead_code)
            rows.append([code, lap, positions[lap], ahead_code, ahead_gap, relation])
        laps_seen = sorted(positions)
        result[code] = {
            "grid_slot": race.driver(code).get("grid"),
            "position_at_end_of_first_lap_in_range": positions[laps_seen[0]] if laps_seen else None,
            "position_at_end_of_last_lap_in_range": positions[laps_seen[-1]] if laps_seen else None,
            f"laps_within_{TRAFFIC_SECONDS}s_of_car_ahead": close_laps,
            "most_often_behind": max(set(behind), key=behind.count) if behind else None,
            "cars_followed_closely": {car: behind.count(car) for car in sorted(set(behind))},
        }

    pair = {race.driver(code)["number"] for code in (series["a"], series["b"])}
    intervals = {number: _lap_intervals(race, number) for number in pair}
    overtakes = []
    for move in sorted(race.overtakes, key=lambda m: m["time"] or 0):
        involved = pair & {move["overtaking"], move["overtaken"]}
        if not involved:
            continue
        ours = next(iter(involved))
        lap = next((lap for lap, (start, end) in intervals[ours].items() if start <= move["time"] <= end), None)
        if lap is None or not (lap_start <= lap <= lap_end):
            continue
        overtakes.append(f"lap {lap}: {race.code(move['overtaking'])} overtook {race.code(move['overtaken'])} for position {move['position']}")
    result["overtakes"] = overtakes
    result["reading"] = (
        f"grid_slot is where the driver started the race (0 = pit lane). Position is the race position at the end "
        f"of a lap, which is not the grid slot. The car ahead is the last car to cross the line before "
        f"the driver, whatever lap it is on; within {TRAFFIC_SECONDS}s of it, a driver is in its turbulent air and may be "
        "held up. That is consistent with being stuck in traffic, but lap times alone cannot prove the driver could "
        "have gone faster. Overtakes recorded in the pit lane or during pit cycles also appear here."
    )
    result["evidence_id"] = log.add(
        source="OpenF1 laps + overtakes", race=race, drivers=[series["a"], series["b"]], lap_start=lap_start,
        lap_end=lap_end, title=f"Track position, {series['a']} and {series['b']}, laps {lap_start}-{lap_end}",
        columns=["driver", "lap", "race position", "car ahead on track", "gap to car ahead (s)", "that car is on"],
        rows=rows,
    )
    return result


def race_overview(race: Race, log: EvidenceLog) -> dict:
    """The field: who started where, who finished where, and when the race was neutralised."""
    drivers = [
        {
            "code": driver["code"],
            "name": driver["name"],
            "team": driver["team"],
            "grid": driver.get("grid"),
            "finish": driver.get("position"),
            "status": driver.get("status"),
            "laps_completed": driver.get("laps_completed"),
        }
        for driver in race.info["drivers"]
    ]
    evidence_id = log.add(
        source="Jolpica results + OpenF1 drivers", race=race, drivers=[], lap_start=None, lap_end=None,
        title=f"Classification, {race.label}",
        columns=["driver", "name", "team", "grid", "finish", "status", "laps completed"],
        rows=[list(driver.values()) for driver in drivers],
    )
    return {
        "evidence_id": evidence_id,
        "race": race.label,
        "date": race.info.get("date"),
        "circuit": race.info.get("circuit"),
        "total_laps": race.info["total_laps"],
        "neutralisations": [{"kind": p["kind"], "laps": f"{p['lap_start']}-{p['lap_end']}"} for p in neutralisations(race)],
        "drivers": drivers,
        "reading": "grid 0 means a pit lane start. finish is the classified position.",
    }


def gap_overview(race: Race, a: str, b: str, log: EvidenceLog) -> dict:
    """The whole race for a Driver Pair: the final Gap and the candidate Gap Phases it was built in."""
    result = gap_phases(race, a, b)
    code_a, code_b = result["a"], result["b"]
    evidence_id = log.add(
        source="OpenF1 laps", race=race, drivers=[code_a, code_b], lap_start=1, lap_end=result["last_common_lap"],
        title=f"Gap between {code_a} and {code_b}, lap by lap",
        columns=["lap", f"Gap at end of lap (s, + = {code_a} behind {code_b})", "change on this lap (s)"],
        rows=[[point["lap"], point["gap"], point["change"]] for point in result["points"]],
    )

    def summary(code: str) -> dict:
        driver = race.driver(code)
        return {"team": driver["team"], "grid": driver.get("grid"), "finish": driver.get("position"),
                "status": driver.get("status"), "pit_in_laps": _pit_laps(race, driver["number"])}

    pair = [race.driver(code) for code in (code_a, code_b)]
    on_lead_lap = all(driver.get("race_time") and driver.get("laps_completed") == race.info["total_laps"] for driver in pair)
    official = round(pair[0]["race_time"] - pair[1]["race_time"], 3) if on_lead_lap else None

    return {
        "evidence_id": evidence_id,
        "race": race.label,
        "a": code_a,
        "b": code_b,
        code_a: summary(code_a),
        code_b: summary(code_b),
        "last_common_lap": result["last_common_lap"],
        "final_gap": result["final_gap"],
        "official_margin": official,
        "note": result["note"],
        "neutralisations": [{"kind": p["kind"], "laps": f"{p['lap_start']}-{p['lap_end']}"} for p in neutralisations(race)],
        # Without the P-ids: the model would cite them as if they were Evidence.
        "candidate_phases": [{key: value for key, value in phase.items() if key != "id"} for phase in result["phases"]],
        "reading": (
            f"The Gap is {code_a}'s time at the line minus {code_b}'s at the end of each lap: positive means {code_a} "
            f"is behind. final_gap is the Gap at the end of lap {result['last_common_lap']}. The phases' changes add up "
            "to it. official_margin is the classified race-time difference when both finished on the lead lap: it can "
            "differ from final_gap by a few tenths (the timing feed's lap timestamps are approximate) or by a time "
            "penalty added after the flag, which the Gap does not include. A pit stop by one driver swings the Gap by "
            "about 20s until the other driver stops too: judge pit strategy over a range of laps that contains both "
            "drivers' stops. Under a Safety Car the field queues up behind it, so a Gap built before it disappears."
        ),
    }


# --- Citations: a report may only cite Evidence that exists ---

CITATION = re.compile(r"\s*\[(E\d+(?:\s*,\s*E\d+)*)\]")


def check_citations(report: str, log: EvidenceLog) -> dict:
    """Which Evidence ids a report cites, and which of them were never registered."""
    cited, unknown = [], []
    for group in CITATION.findall(report):
        for evidence_id in re.findall(r"E\d+", group):
            target = cited if log.get(evidence_id) else unknown
            if evidence_id not in target:
                target.append(evidence_id)
    return {"cited": cited, "unknown": unknown}


def drop_unknown_citations(report: str, log: EvidenceLog) -> str:
    """Remove citations of Evidence that doesn't exist, keeping the real ones."""

    def keep_known(match: re.Match) -> str:
        known = [evidence_id for evidence_id in re.findall(r"E\d+", match.group(1)) if log.get(evidence_id)]
        return f" [{', '.join(known)}]" if known else ""

    return CITATION.sub(keep_known, report)
