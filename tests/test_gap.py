import pytest

from analysis import gap_series
from tests.builders import make_race


def test_gap_is_how_far_a_is_behind_b_at_the_end_of_each_lap():
    race = make_race({"AAA": [91.0, 90.5, 90.0], "BBB": [90.0, 90.0, 90.0]})

    series = gap_series(race, "AAA", "BBB")

    assert [point["lap"] for point in series["points"]] == [1, 2, 3]
    assert [point["gap"] for point in series["points"]] == pytest.approx([1.0, 1.5, 1.5])
    assert [point["change"] for point in series["points"]] == pytest.approx([1.0, 0.5, 0.0])


def test_gap_stops_at_the_last_lap_both_drivers_completed():
    race = make_race({"AAA": [90.0, 90.0], "BBB": [90.0, 90.0, 90.0, 90.0]})
    race.info["drivers"][0]["status"] = "Retired"

    series = gap_series(race, "AAA", "BBB")

    assert series["last_common_lap"] == 2
    assert [point["lap"] for point in series["points"]] == [1, 2]
    assert "AAA completed 2 laps" in series["note"]
    assert "Retired" in series["note"]


def test_gap_note_is_empty_when_both_ran_the_whole_race():
    race = make_race({"AAA": [91.0, 90.0], "BBB": [90.0, 90.0]})

    assert gap_series(race, "AAA", "BBB")["note"] == ""


def test_unknown_driver_is_reported_with_the_valid_codes():
    race = make_race({"AAA": [90.0], "BBB": [90.0]})

    with pytest.raises(Exception, match="Drivers in this race: AAA, BBB"):
        gap_series(race, "ZZZ", "BBB")


def test_a_driver_cannot_be_compared_with_themselves():
    race = make_race({"AAA": [90.0], "BBB": [90.0]})

    with pytest.raises(Exception, match="two different drivers"):
        gap_series(race, "AAA", "AAA")
