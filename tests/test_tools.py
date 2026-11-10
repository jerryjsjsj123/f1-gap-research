import pytest

from analysis import EvidenceLog, lap_pace
from f1data import DataError
from tests.builders import make_race


def test_every_tool_call_registers_evidence_with_its_source():
    race = make_race({"AAA": [91.0, 90.5, 90.5, 90.5], "BBB": [90.0] * 4})
    log = EvidenceLog()

    first = lap_pace(race, "AAA", "BBB", 2, 4, log)
    second = lap_pace(race, "AAA", "BBB", 2, 3, log)

    assert (first["evidence_id"], second["evidence_id"]) == ("E1", "E2")
    evidence = log.get("E1")
    assert evidence["source"] == "OpenF1 laps"
    assert evidence["race"] == "Test Grand Prix (round 99)"
    assert evidence["drivers"] == ["AAA", "BBB"]
    assert (evidence["lap_start"], evidence["lap_end"]) == (2, 4)
    assert len(evidence["rows"]) == 3
    assert evidence["columns"][0] == "lap"


def test_lap_pace_compares_clean_laps():
    race = make_race({"AAA": [91.0, 90.5, 90.7, 90.3, 90.5], "BBB": [90.0] * 5})

    result = lap_pace(race, "AAA", "BBB", 2, 5, EvidenceLog())

    assert result["clean_laps"] == 4
    assert result["AAA"]["mean_lap"] == pytest.approx(90.5)
    assert result["BBB"]["mean_lap"] == pytest.approx(90.0)
    assert result["mean_difference"] == pytest.approx(0.5)  # A slower by half a second a lap
    assert result["gap_before"] == pytest.approx(1.0)
    assert result["gap_after"] == pytest.approx(3.0)
    assert result["gap_change"] == pytest.approx(2.0)


def test_lap_pace_leaves_pit_laps_out_of_the_pace_comparison():
    times = [90.0, 90.0, 105.0, 97.0, 90.0, 90.0]
    race = make_race({"AAA": times, "BBB": [90.0] * 6},
                     pits=[{"driver": 1, "lap": 3, "time": 0, "lane_duration": 22.0, "stop_duration": 2.5}])

    result = lap_pace(race, "AAA", "BBB", 2, 6, EvidenceLog())

    assert result["clean_laps"] == 3  # laps 2, 5 and 6
    assert result["mean_difference"] == pytest.approx(0.0)
    assert result["gap_change"] == pytest.approx(22.0)
    assert result["gap_change_by_lap_type"] == {"clean laps": 0.0, "pit in-laps and out-laps": 22.0}


def test_lap_pace_shows_whose_lap_times_drifted():
    a = [90.0, 90.0, 90.2, 90.4, 90.6, 90.8, 91.0]
    race = make_race({"AAA": a, "BBB": [90.0] * 7})

    result = lap_pace(race, "AAA", "BBB", 2, 7, EvidenceLog())

    assert result["AAA"]["drift_per_lap"] == pytest.approx(0.2)
    assert result["BBB"]["drift_per_lap"] == pytest.approx(0.0)


def test_lap_range_outside_the_race_is_refused():
    race = make_race({"AAA": [90.0] * 3, "BBB": [90.0] * 3})

    with pytest.raises(DataError, match="laps 1 to 3"):
        lap_pace(race, "AAA", "BBB", 2, 9, EvidenceLog())


# --- tyres ---

from analysis import pit_stops, tyres  # noqa: E402


def stint(number, n, lap_start, lap_end, compound, age=0):
    return {"driver": number, "stint": n, "lap_start": lap_start, "lap_end": lap_end,
            "compound": compound, "tyre_age_at_start": age}


def test_tyres_reports_compound_and_tyre_age_across_the_range():
    race = make_race({"AAA": [90.0] * 20, "BBB": [90.0] * 20})
    race.stints = [stint(1, 1, 1, 8, "MEDIUM"), stint(1, 2, 9, 20, "HARD"),
                   stint(2, 1, 1, 14, "MEDIUM"), stint(2, 2, 15, 20, "SOFT", age=3)]

    result = tyres(race, "AAA", "BBB", 10, 16, EvidenceLog())

    assert result["AAA"] == [{"compound": "HARD", "laps": "10-16", "tyre_age_at_first_lap": 1, "tyre_age_at_last_lap": 7}]
    assert result["BBB"] == [
        {"compound": "MEDIUM", "laps": "10-14", "tyre_age_at_first_lap": 9, "tyre_age_at_last_lap": 13},
        {"compound": "SOFT", "laps": "15-16", "tyre_age_at_first_lap": 3, "tyre_age_at_last_lap": 4},
    ]
    assert result["tyre_age_difference_at_first_lap"] == -8  # A's tyres are 8 laps younger
    assert result["evidence_id"] == "E1"


def test_tyres_says_when_stint_data_is_missing():
    race = make_race({"AAA": [90.0] * 5, "BBB": [90.0] * 5})
    race.stints = [stint(1, 1, 1, 5, "MEDIUM")]

    result = tyres(race, "AAA", "BBB", 2, 4, EvidenceLog())

    assert result["BBB"] == []
    assert "No tyre data for BBB" in result["missing"]


# --- pit stops ---


def test_pit_stops_show_what_each_stop_cost_against_the_drivers_own_pace():
    a = [90.0] * 5 + [105.0, 97.0] + [90.0] * 5
    b = [90.0] * 8 + [103.0, 96.0] + [90.0] * 2
    race = make_race({"AAA": a, "BBB": b}, pits=[
        {"driver": 1, "lap": 6, "time": 0, "lane_duration": 23.0, "stop_duration": 3.5},
        {"driver": 2, "lap": 9, "time": 0, "lane_duration": 21.0, "stop_duration": 2.2},
    ])

    result = pit_stops(race, "AAA", "BBB", 5, 11, EvidenceLog())

    assert result["AAA"]["stops"] == [{
        "in_lap": 6, "pit_lane_time": 23.0, "stationary_time": 3.5, "in_lap_time": 105.0, "out_lap_time": 97.0,
        "time_lost_vs_own_pace": 22.0, "under": None,
    }]
    assert result["BBB"]["stops"][0]["time_lost_vs_own_pace"] == pytest.approx(19.0)
    assert result["AAA"]["total_time_lost"] == pytest.approx(22.0)
    assert result["difference_in_time_lost"] == pytest.approx(3.0)  # A lost 3s more in the pits
    assert result["gap_before"] == pytest.approx(0.0)
    assert result["gap_after"] == pytest.approx(3.0)


def test_pit_stops_reports_when_nobody_stopped():
    race = make_race({"AAA": [90.0] * 6, "BBB": [90.0] * 6})

    result = pit_stops(race, "AAA", "BBB", 2, 5, EvidenceLog())

    assert result["AAA"]["stops"] == [] and result["BBB"]["stops"] == []
    assert "Neither driver pitted" in result["note"]


# --- race events ---

from analysis import gap_overview, race_events, race_overview, track_position  # noqa: E402
from tests.builders import at  # noqa: E402


def message(race, lap, text, category="Other", flag=None, driver=None):
    return {"time": at(race, "BBB", lap), "lap": lap, "category": category, "flag": flag, "scope": None,
            "sector": None, "driver": driver, "message": text}


def test_race_events_lists_neutralisations_and_messages_about_the_pair():
    race = make_race({"AAA": [90.0] * 10, "BBB": [90.0] * 10, "CCC": [91.0] * 10})
    race.race_control = [
        message(race, 3, "SAFETY CAR DEPLOYED", category="SafetyCar"),
        message(race, 5, "SAFETY CAR IN THIS LAP", category="SafetyCar"),
        message(race, 4, "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 1 (AAA) - SPEEDING IN THE PIT LANE"),
        message(race, 4, "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 3 (CCC) - FALSE START"),
        message(race, 6, "BLUE FLAG FOR CAR 3 (CCC)", category="Flag", flag="BLUE", driver=3),
        message(race, 9, "CAR 2 (BBB) TIME 1:30.000 DELETED - TRACK LIMITS AT TURN 4 LAP 8"),
    ]

    log = EvidenceLog()
    result = race_events(race, "AAA", "BBB", 2, 8, log)

    assert result["neutralisations"] == [{"kind": "Safety Car", "laps": "3-5"}]
    assert result["about_AAA"] == ["lap 4: FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 1 (AAA) - SPEEDING IN THE PIT LANE"]
    assert result["about_BBB"] == []  # the deleted lap time was announced on lap 9, outside the range
    texts = [row[2] for row in log.get(result["evidence_id"])["rows"]]
    assert any("SAFETY CAR DEPLOYED" in text for text in texts)
    assert not any("CCC" in text for text in texts)


def test_race_events_says_when_nothing_happened():
    race = make_race({"AAA": [90.0] * 6, "BBB": [90.0] * 6})

    result = race_events(race, "AAA", "BBB", 2, 5, EvidenceLog())

    assert result["neutralisations"] == []
    assert "No Safety Car" in result["note"]


# --- track position ---


def test_track_position_shows_race_position_and_the_car_ahead():
    # CCC leads by a second a lap; AAA sits half a second behind BBB the whole way.
    race = make_race({"AAA": [90.5] + [90.0] * 5, "BBB": [90.0] * 6, "CCC": [89.0] * 6})

    result = track_position(race, "AAA", "BBB", 2, 6, EvidenceLog())

    assert result["AAA"]["position_at_end_of_first_lap_in_range"] == 3
    assert result["AAA"]["position_at_end_of_last_lap_in_range"] == 3
    assert result["BBB"]["position_at_end_of_last_lap_in_range"] == 2
    assert result["AAA"]["laps_within_1.5s_of_car_ahead"] == 5
    assert result["AAA"]["most_often_behind"] == "BBB"
    assert result["BBB"]["laps_within_1.5s_of_car_ahead"] == 0  # CCC is already 2s up the road


def test_track_position_lists_overtakes_involving_the_pair():
    race = make_race({"AAA": [90.0] * 6, "BBB": [90.0] * 6, "CCC": [90.0] * 6})
    race.overtakes = [
        {"time": at(race, "AAA", 3), "overtaking": 1, "overtaken": 2, "position": 1},
        {"time": at(race, "AAA", 4), "overtaking": 3, "overtaken": 2, "position": 2},
        {"time": at(race, "AAA", 5), "overtaking": 3, "overtaken": 1, "position": 1},
    ]

    result = track_position(race, "AAA", "BBB", 2, 4, EvidenceLog())

    assert result["overtakes"] == [
        "lap 3: AAA overtook BBB for position 1",
        "lap 4: CCC overtook BBB for position 2",
    ]


# --- overviews ---


def test_race_overview_lists_the_field_with_grid_and_finish():
    race = make_race({"AAA": [90.0] * 3, "BBB": [90.0] * 3})

    result = race_overview(race, EvidenceLog())

    assert result["race"] == "Test Grand Prix (round 99)"
    assert result["drivers"][0] == {"code": "AAA", "name": "Driver Aaa", "team": "Team 1", "grid": 1,
                                    "finish": 1, "status": "Finished", "laps_completed": 3}
    assert result["evidence_id"] == "E1"


def test_gap_overview_gives_the_final_gap_and_candidate_phases():
    race = make_race({"AAA": [91.0] + [90.5] * 9, "BBB": [90.0] * 10})

    log = EvidenceLog()
    result = gap_overview(race, "AAA", "BBB", log)

    assert result["final_gap"] == pytest.approx(5.5)
    assert [(phase["lap_start"], phase["lap_end"]) for phase in result["candidate_phases"]] == [(1, 1), (2, 10)]
    assert "points" not in result  # the lap-by-lap series goes to the Evidence, not to the model
    assert len(log.get(result["evidence_id"])["rows"]) == 10


def test_gap_overview_also_gives_the_official_margin_when_both_finished_on_the_lead_lap():
    race = make_race({"AAA": [91.0] + [90.0] * 4, "BBB": [90.0] * 5})
    race.info["drivers"][0]["race_time"] = 456.2  # includes a 5s penalty added after the flag
    race.info["drivers"][1]["race_time"] = 450.0

    result = gap_overview(race, "AAA", "BBB", EvidenceLog())

    assert result["final_gap"] == pytest.approx(1.0)
    assert result["official_margin"] == pytest.approx(6.2)


def test_gap_overview_has_no_official_margin_for_a_lapped_driver():
    race = make_race({"AAA": [95.0] * 4, "BBB": [90.0] * 5})
    race.info["drivers"][1]["race_time"] = 450.0

    assert gap_overview(race, "AAA", "BBB", EvidenceLog())["official_margin"] is None


# --- results say in words who lost time, so a model never has to read a sign ---


def test_lap_pace_says_in_words_who_lost_time_and_where():
    times = [93.0, 90.5, 105.0, 97.0, 90.5, 90.5]
    race = make_race({"AAA": times, "BBB": [90.0] * 6},
                     pits=[{"driver": 1, "lap": 3, "time": 0, "lane_duration": 22.0, "stop_duration": 2.5}])

    result = lap_pace(race, "AAA", "BBB", 1, 6, EvidenceLog())

    assert result["gap_change_by_lap_type"] == {"clean laps": 1.5, "first lap": 3.0, "pit in-laps and out-laps": 22.0}
    assert result["in_words"] == [
        "Over laps 1-6 AAA lost 26.5s to BBB.",
        "On the 3 clean laps AAA was 0.5s per lap slower than BBB, losing 1.5s.",
        "On the first lap AAA lost 3.0s to BBB.",
        "On pit in-laps and out-laps AAA lost 22.0s to BBB.",
    ]


def test_pit_stops_say_in_words_who_lost_more_time_pitting():
    a = [90.0] * 5 + [105.0, 97.0] + [90.0] * 5
    b = [90.0] * 5 + [103.0, 96.0] + [90.0] * 5
    race = make_race({"AAA": a, "BBB": b}, pits=[
        {"driver": 1, "lap": 6, "time": 0, "lane_duration": 23.0, "stop_duration": 3.5},
        {"driver": 2, "lap": 6, "time": 0, "lane_duration": 21.0, "stop_duration": 2.2},
    ])

    result = pit_stops(race, "AAA", "BBB", 5, 9, EvidenceLog())

    assert result["in_words"] == "Pitting on these laps cost AAA 22.0s and BBB 19.0s against their own pace: AAA lost 3.0s more than BBB."


def test_track_position_gives_the_grid_slot_separately_from_the_running_position():
    race = make_race({"AAA": [90.0] * 4, "BBB": [91.0] * 4})
    race.info["drivers"][0]["grid"] = 21

    result = track_position(race, "AAA", "BBB", 1, 4, EvidenceLog())

    assert result["AAA"]["grid_slot"] == 21
    assert result["AAA"]["position_at_end_of_first_lap_in_range"] == 1
    assert result["AAA"]["position_at_end_of_last_lap_in_range"] == 1


def test_pit_stops_under_a_safety_car_are_compared_by_pit_lane_time_not_by_laps_that_were_slow_anyway():
    a = [90.0] * 4 + [130.0, 150.0, 131.0] + [90.0] * 5
    b = [90.0] * 4 + [125.0, 140.0, 130.0] + [90.0] * 5
    race = make_race({"AAA": a, "BBB": b}, pits=[
        {"driver": 1, "lap": 5, "time": 0, "lane_duration": 24.0, "stop_duration": 3.0},
        {"driver": 2, "lap": 5, "time": 0, "lane_duration": 22.5, "stop_duration": 2.4},
    ])
    race.race_control = [
        {"time": at(race, "BBB", 5), "lap": 5, "category": "SafetyCar", "flag": None, "message": "SAFETY CAR DEPLOYED"},
        {"time": at(race, "BBB", 7), "lap": 7, "category": "SafetyCar", "flag": None, "message": "SAFETY CAR IN THIS LAP"},
    ]

    result = pit_stops(race, "AAA", "BBB", 4, 8, EvidenceLog())

    assert result["AAA"]["stops"][0]["under"] == "Safety Car"
    assert result["AAA"]["stops"][0]["time_lost_vs_own_pace"] is None
    assert result["in_words"] == (
        "Both drivers pitted under a Safety Car. AAA spent 24.0s in the pit lane and BBB 22.5s: "
        "AAA was 1.5s slower through the pit lane. Lap times around these stops were slow for everyone, "
        "so they say nothing about what the stops cost."
    )
