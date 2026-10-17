from datetime import date, timedelta

from race_stop_count_v1 import (
    RaceLabel,
    RaceSignals,
    StopCountModel,
    model_stops,
    race_labels,
    stops_wear_and_track,
    track_prior,
)
from race_strategy_timing_v1 import CarStrategy, Stint, predict_from_wear_timed

START = date(2022, 3, 1)


def _labels(spec):
    """spec: list of (track, era, stops) in date order, one week apart -> {race_id: RaceLabel}."""
    return {i + 1: RaceLabel(START + timedelta(weeks=i), track, era, stops) for i, (track, era, stops) in enumerate(spec)}


def test_track_prior_is_shrunk_to_the_era_average_and_walk_forward():
    labels = _labels([(1, "e", 1)] * 10 + [(2, "e", 3), (2, "e", 3)])
    later = START + timedelta(weeks=30)
    # era average = (10*1 + 2*3) / 12; track 2 has two 3-stop races, pulled toward it by 2 races' worth
    era = 16 / 12
    assert abs(track_prior(labels, track_id=2, regulation_era="e", before=later) - (6 + 2 * era) / 4) < 1e-9
    # a track with no history gets the era average
    assert abs(track_prior(labels, track_id=9, regulation_era="e", before=later) - era) < 1e-9
    # only races strictly before the date count: before race 11 track 2 has no history
    assert abs(track_prior(labels, track_id=2, regulation_era="e", before=labels[11].race_date) - 1.0) < 1e-9
    # an earlier era counts half
    mixed = _labels([(1, "old", 1)] * 10 + [(2, "old", 3)] + [(1, "e", 1)] * 8)
    assert abs(track_prior(mixed, track_id=2, regulation_era="e", before=later) - (1.5 + 2 * 1.0) / 2.5) < 1e-9


def test_track_votes_join_the_wear_votes():
    # 10 low-wear 1-stop races elsewhere, then 3 two-stop races at track 7
    labels = _labels([(10 + i, "e", 1) for i in range(10)] + [(7, "e", 2)] * 3)
    wear = {r: 0.001 for r in labels}
    later = START + timedelta(weeks=30)
    # wear alone: 7 votes for 1 stop; track 7 adds 3 races x 2 votes = 6 for 2 stops -> 1 stop still wins
    assert stops_wear_and_track(labels, wear, wear=0.001, track_id=7, regulation_era="e", before=later) == 1
    # with heavier track weight the track history wins
    assert stops_wear_and_track(labels, wear, wear=0.001, track_id=7, regulation_era="e", before=later,
                                weight=3) == 2
    # no wear: track votes alone
    assert stops_wear_and_track(labels, wear, wear=None, track_id=7, regulation_era="e", before=later) == 2
    # no wear and a new track: nothing to go on
    assert stops_wear_and_track(labels, wear, wear=None, track_id=99, regulation_era="e", before=later) is None


def _signals_and_labels(n=40):
    """High practice wear -> 2 stops, low -> 1, alternating; tracks all different."""
    labels, signals = {}, {}
    for i in range(n):
        high = i % 2 == 1
        d = START + timedelta(weeks=i)
        labels[i + 1] = RaceLabel(d, 100 + i, "e", 2 if high else 1)
        signals[i + 1] = RaceSignals(i + 1, d, 100 + i, "e", 0.004 if high else 0.001, 20.0, 35.0)
    return signals, labels


def test_model_learns_from_earlier_races_and_abstains_when_thin():
    signals, labels = _signals_and_labels()
    assert model_stops(signals, labels, race_id=40, feature_set="wear_track") == 2
    assert model_stops(signals, labels, race_id=39, feature_set="all") == 1
    # too few earlier races to train on
    assert model_stops(signals, labels, race_id=10, feature_set="wear_track") is None
    # the target race's own label is never used: flipping it changes nothing
    flipped = dict(labels)
    flipped[40] = RaceLabel(labels[40].race_date, labels[40].track_id, "e", 1)
    assert model_stops(signals, flipped, race_id=40, feature_set="wear_track") == 2


def test_model_imputes_missing_features_with_the_training_median():
    model = StopCountModel(("wear", "pit_loss")).fit(
        [({"wear": 0.001, "pit_loss": None}, 1), ({"wear": 0.004, "pit_loss": 21.0}, 2)] * 10)
    assert model.predict({"wear": 0.0042, "pit_loss": None}) == 2
    assert model.predict({"wear": None, "pit_loss": None}) in (1, 2)
    constant = StopCountModel(("wear",)).fit([({"wear": 0.001}, 2)] * 5)
    assert constant.predict({"wear": 0.009}) == 2


def _car(race, when, track, stints, laps=50):
    return CarStrategy(race, when, when.year, track, "e", 5, False, laps, tuple(stints))


def test_race_labels_use_the_most_common_stop_count_clipped():
    cars = []
    for i in range(6):
        cars.append(_car(1, START, 3, [Stint("MEDIUM", 1, 25), Stint("HARD", 26, 50)]))
        cars.append(_car(2, START + timedelta(weeks=1), 4,
                         [Stint("SOFT", 1, 10), Stint("MEDIUM", 11, 20), Stint("HARD", 21, 30),
                          Stint("MEDIUM", 31, 40), Stint("SOFT", 41, 50)]))
    labels = race_labels(cars)
    assert labels[1].stops == 1 and labels[1].track_id == 3
    assert labels[2].stops == 3                       # 4 stops clipped to 3


def test_given_stop_count_goes_through_m_rules_even_without_wear():
    one = [Stint("MEDIUM", 1, 25), Stint("HARD", 26, 50)]
    two = [Stint("MEDIUM", 1, 15), Stint("HARD", 16, 32), Stint("HARD", 33, 50)]
    history = [_car(r, START + timedelta(weeks=r), r, one if r % 3 else two) for r in range(1, 13) for _ in range(5)]
    base = predict_from_wear_timed(history, wear=None, wear_by_race={}, regulation_era="e", total_laps=50)
    assert len(base.strategy.sequence) == 2                        # era mode: one stop
    given = predict_from_wear_timed(history, wear=None, wear_by_race={}, regulation_era="e", total_laps=50, stops=2)
    assert given.strategy.sequence == ("MEDIUM", "HARD", "HARD") and given.strategy.stop_laps == (15, 32)
    assert "stop count given" in given.source
