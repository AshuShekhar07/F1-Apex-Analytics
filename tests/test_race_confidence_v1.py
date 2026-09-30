import pytest

from race_confidence_v1 import COVERAGE, MIN_CELL, ErrorSample, ErrorTable, band, coverage_check


def _samples(season, races, error, wet_quali=False, wet_race=False, start=0):
    """Races of 20 drivers; driver p predicted Pp, finishes `error` places away (alternating)."""
    out = []
    for race in range(start, start + races):
        for p in range(1, 21):
            finish = p + error if p + error <= 20 else p - error
            out.append(ErrorSample(race, season, p, finish, wet_quali, wet_race))
    return out


def test_bands():
    assert [band(p) for p in (1, 3, 4, 10, 11, 20)] == ["P1-3", "P1-3", "P4-10", "P4-10", "P11+", "P11+"]


def test_wet_qualifying_measured_separately_and_flagged_low():
    dry = _samples(2023, 20, 2)
    wet = _samples(2023, 20, 5, wet_quali=True, start=100)
    table = ErrorTable.fit(dry + wet)
    assert table.expected_error["dry_quali"] == pytest.approx(2.0)
    assert table.expected_error["wet_quali"] == pytest.approx(5.0)
    label = table.label(wet_quali=True, rain_expected=False, predicted={7: 1, 8: 12}, field=20)
    assert label["level"] == "low" and label["condition"] == "wet_quali"
    assert label["ranges"] == {"7": [1, 6], "8": [7, 17]}
    normal = table.label(wet_quali=False, rain_expected=False, predicted={7: 1}, field=20)
    assert normal["level"] == "normal" and normal["ranges"] == {"7": [1, 3]}


def test_too_few_wet_races_falls_back_to_dry_numbers_and_says_so():
    table = ErrorTable.fit(_samples(2023, 20, 2) + _samples(2023, 1, 6, wet_race=True, start=100))
    assert table.samples["rain_forecast"] < MIN_CELL
    label = table.label(wet_quali=False, rain_expected=True, predicted={1: 5}, field=20)
    assert label["condition"] == "dry_quali" and label["level"] == "normal"
    assert "only 20 earlier drivers measured" in label["reason"]


def test_no_history_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        ErrorTable.fit([])


def test_coverage_check_fits_on_earlier_seasons_only():
    # 2022: errors 1; 2023: errors 8 -> ranges fitted on 2022 are far too narrow for 2023
    check = coverage_check(_samples(2022, 10, 1) + _samples(2023, 10, 8))
    n, inside, said, actual = check["all"]
    assert n == 200 and inside == 0.0 and said == pytest.approx(1.0) and actual == pytest.approx(8.0)
    # same errors every season: the range holds the actual finish
    n, inside, *_ = coverage_check(_samples(2022, 10, 2) + _samples(2023, 10, 2))["all"]
    assert inside >= COVERAGE


def test_range_width_is_the_whole_place_closest_to_the_target():
    from race_confidence_v1 import _half_width

    # errors <= 3: 79%, <= 4: 90% -> 3 (1 point from 80%), not 4 (rounding the quantile up)
    errors = [0] * 40 + [2] * 20 + [3] * 19 + [4] * 11 + [9] * 10
    assert _half_width(errors) == 3
    table = ErrorTable.fit(_samples(2023, 20, 2))
    assert table.inside["dry_quali"] == 1.0          # every error is exactly 2 -> width 2 holds all
