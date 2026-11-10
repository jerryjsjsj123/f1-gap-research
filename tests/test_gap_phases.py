import pytest

from analysis import gap_phases
from tests.builders import at, make_race


def spans(phases):
    return [(phase["lap_start"], phase["lap_end"], phase["kind"]) for phase in phases]


def test_the_first_lap_is_its_own_phase():
    race = make_race({"AAA": [93.0] + [90.0] * 9, "BBB": [90.0] * 10})

    phases = gap_phases(race, "AAA", "BBB")["phases"]

    assert spans(phases) == [(1, 1, "start"), (2, 10, "green")]
    assert phases[0]["change"] == pytest.approx(3.0)
    assert phases[1]["change"] == pytest.approx(0.0)


def test_a_change_of_pace_splits_green_running_into_two_phases():
    race = make_race({"AAA": [90.5] * 10 + [90.0] * 10, "BBB": [90.0] * 20})

    phases = gap_phases(race, "AAA", "BBB")["phases"]

    assert spans(phases) == [(1, 1, "start"), (2, 10, "green"), (11, 20, "green")]
    assert phases[1]["change"] == pytest.approx(4.5)
    assert phases[1]["per_lap"] == pytest.approx(0.5)
    assert phases[1]["gap_before"] == pytest.approx(0.5)
    assert phases[1]["gap_after"] == pytest.approx(5.0)
    assert phases[2]["change"] == pytest.approx(0.0)


def test_lap_to_lap_noise_does_not_split_a_phase():
    noisy = [90.3, 89.8, 90.1, 89.9, 90.2, 89.7, 90.2, 90.0, 89.9, 90.1, 90.0, 89.8]
    race = make_race({"AAA": noisy, "BBB": [90.0] * 12})

    assert spans(gap_phases(race, "AAA", "BBB")["phases"]) == [(1, 1, "start"), (2, 12, "green")]


def test_phase_changes_add_up_to_the_final_gap():
    race = make_race({"AAA": [91.0, 90.4, 90.6, 90.5, 112.0, 95.0, 90.0, 90.0, 89.5, 89.4, 89.6, 89.5],
                      "BBB": [90.0] * 12},
                     pits=[{"driver": 1, "lap": 5, "time": 0, "lane_duration": 22.0, "stop_duration": 2.5}])

    result = gap_phases(race, "AAA", "BBB")

    assert sum(phase["change"] for phase in result["phases"]) == pytest.approx(result["final_gap"])
    assert result["final_gap"] == pytest.approx(27.5)
    assert [phase["id"] for phase in result["phases"]] == [f"P{n}" for n in range(1, len(result["phases"]) + 1)]


def pit(number, lap):
    return {"driver": number, "lap": lap, "time": 0, "lane_duration": 22.0, "stop_duration": 2.5}


def test_a_pit_stop_makes_a_phase_of_its_in_lap_and_out_lap():
    times = [90.0] * 5 + [105.0, 97.0] + [90.0] * 5
    race = make_race({"AAA": times, "BBB": [90.0] * 12}, pits=[pit(1, 6)])

    phases = gap_phases(race, "AAA", "BBB")["phases"]

    assert spans(phases) == [(1, 1, "start"), (2, 5, "green"), (6, 7, "pit_cycle"), (8, 12, "green")]
    assert phases[2]["change"] == pytest.approx(22.0)
    assert phases[2]["events"] == ["AAA pit stop at the end of lap 6"]


def test_stops_a_few_laps_apart_are_one_pit_cycle():
    a = [90.0] * 5 + [105.0, 97.0] + [90.0] * 9
    b = [90.0] * 8 + [105.0, 96.0] + [90.0] * 6
    race = make_race({"AAA": a, "BBB": b}, pits=[pit(1, 6), pit(2, 9)])

    phases = gap_phases(race, "AAA", "BBB")["phases"]

    assert spans(phases) == [(1, 1, "start"), (2, 5, "green"), (6, 10, "pit_cycle"), (11, 16, "green")]
    assert phases[2]["change"] == pytest.approx(1.0)  # A's stop cost 22s, B's 21s
    assert phases[2]["events"] == ["AAA pit stop at the end of lap 6", "BBB pit stop at the end of lap 9"]


def test_stops_far_apart_are_separate_phases():
    a = [90.0] * 5 + [105.0, 97.0] + [90.0] * 13
    b = [90.0] * 15 + [105.0, 97.0] + [90.0] * 3
    race = make_race({"AAA": a, "BBB": b}, pits=[pit(1, 6), pit(2, 16)])

    kinds = spans(gap_phases(race, "AAA", "BBB")["phases"])

    assert (6, 7, "pit_cycle") in kinds
    assert (16, 17, "pit_cycle") in kinds
    assert (8, 15, "green") in kinds


def test_safety_car_laps_are_a_neutralised_phase():
    a = [90.0] * 4 + [120.0, 140.0, 131.0] + [90.0] * 5
    b = [90.0] * 4 + [125.0, 140.0, 130.0] + [90.0] * 5
    race = make_race({"AAA": a, "BBB": b})
    race.race_control = [
        {"time": at(race, "BBB", 5), "lap": 5, "category": "SafetyCar", "flag": None, "message": "SAFETY CAR DEPLOYED"},
        {"time": at(race, "BBB", 7), "lap": 7, "category": "SafetyCar", "flag": None, "message": "SAFETY CAR IN THIS LAP"},
    ]

    phases = gap_phases(race, "AAA", "BBB")["phases"]

    assert spans(phases) == [(1, 1, "start"), (2, 4, "green"), (5, 7, "neutralised"), (8, 12, "green")]
    assert phases[2]["events"] == ["Safety Car"]
    assert phases[2]["change"] == pytest.approx(-4.0)


def test_a_pit_stop_under_the_safety_car_joins_the_neutralised_phase():
    a = [90.0] * 4 + [120.0, 150.0, 131.0, 95.0] + [90.0] * 4
    b = [90.0] * 4 + [125.0, 140.0, 130.0, 90.0] + [90.0] * 4
    race = make_race({"AAA": a, "BBB": b}, pits=[pit(1, 7)])
    race.race_control = [
        {"time": at(race, "BBB", 5), "lap": 5, "category": "SafetyCar", "flag": None, "message": "SAFETY CAR DEPLOYED"},
        {"time": at(race, "BBB", 7), "lap": 7, "category": "SafetyCar", "flag": None, "message": "SAFETY CAR IN THIS LAP"},
    ]

    phases = gap_phases(race, "AAA", "BBB")["phases"]

    assert (5, 8, "neutralised") in spans(phases)
    assert "pit_cycle" not in [phase["kind"] for phase in phases]


def test_a_one_lap_swing_stands_alone():
    a = [90.0] * 6 + [98.0] + [90.0] * 6
    race = make_race({"AAA": a, "BBB": [90.0] * 13})

    phases = gap_phases(race, "AAA", "BBB")["phases"]

    assert spans(phases) == [(1, 1, "start"), (2, 6, "green"), (7, 7, "jump"), (8, 13, "green")]
    assert phases[2]["change"] == pytest.approx(8.0)


def test_a_safety_car_that_becomes_a_stoppage_ends_when_the_race_resumes():
    from analysis import neutralisations

    race = make_race({"AAA": [90.0] * 3 + [900.0] + [90.0] * 6, "BBB": [90.0] * 3 + [900.0] + [90.0] * 6})

    def control(lap, category, text, fraction=0.5):
        return {"time": at(race, "BBB", lap, fraction), "lap": lap, "category": category, "flag": None, "message": text}

    race.race_control = [
        control(3, "SafetyCar", "SAFETY CAR DEPLOYED"),
        control(3, "SessionStatus", "SESSION ABORTED", 0.8),
        control(4, "SessionStatus", "SESSION STARTED", 0.9),
    ]

    assert [(p["kind"], p["lap_start"], p["lap_end"]) for p in neutralisations(race)] == [
        ("Safety Car", 3, 3),
        ("Red Flag", 3, 4),
    ]
    assert spans(gap_phases(race, "AAA", "BBB")["phases"]) == [
        (1, 1, "start"), (2, 2, "green"), (3, 4, "neutralised"), (5, 10, "green")]



def test_the_slow_laps_that_re_form_the_grid_after_a_stoppage_stay_in_the_neutralised_phase():
    # Lap 3 is the stoppage. Laps 4 and 5 are the crawl out of the pit lane and the standing restart:
    # the Gap swings by half a minute there without anyone racing.
    a = [90.0, 90.0, 900.0, 230.0, 135.0] + [90.0] * 5
    b = [90.0, 90.0, 930.0, 200.0, 140.0] + [90.0] * 5
    race = make_race({"AAA": a, "BBB": b})
    race.race_control = [
        {"time": at(race, "BBB", 3, 0.1), "lap": 3, "category": "SessionStatus", "flag": None, "message": "SESSION ABORTED"},
        {"time": at(race, "BBB", 3, 0.9), "lap": 3, "category": "SessionStatus", "flag": None, "message": "SESSION STARTED"},
    ]

    phases = gap_phases(race, "AAA", "BBB")["phases"]

    assert spans(phases) == [(1, 1, "start"), (2, 2, "green"), (3, 5, "neutralised"), (6, 10, "green")]
    assert phases[2]["change"] == pytest.approx(-5.0)
    assert phases[2]["events"] == ["Red Flag"]
