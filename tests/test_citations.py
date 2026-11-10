from analysis import EvidenceLog, check_citations, drop_unknown_citations
from tests.builders import make_race


def log_with(n):
    race = make_race({"AAA": [90.0] * 3, "BBB": [90.0] * 3})
    log = EvidenceLog()
    for _ in range(n):
        log.add(source="OpenF1 laps", race=race, drivers=["AAA"], lap_start=1, lap_end=3, title="t", columns=[], rows=[])
    return log


def test_citations_that_exist_are_listed_in_order_of_first_use():
    report = "AAA lost 3.0s on lap 1 [E2]. The stop cost 22s [E1, E2]."

    assert check_citations(report, log_with(2)) == {"cited": ["E2", "E1"], "unknown": []}


def test_citations_that_were_never_registered_are_reported():
    report = "AAA was faster [E1]. BBB had older tyres [E7]."

    assert check_citations(report, log_with(2))["unknown"] == ["E7"]


def test_unknown_citations_are_removed_and_known_ones_kept():
    report = "AAA was faster [E1]. BBB had older tyres [E7]. Both pitted [E2, E9]."

    cleaned = drop_unknown_citations(report, log_with(2))

    assert cleaned == "AAA was faster [E1]. BBB had older tyres. Both pitted [E2]."
