"""Race stop count from what is known before the race (research, methods Q1-Q3).

The stop count is where strategy prediction has the most room: production (method M,
practice tyre wear) gets 47% of cars right, while the race's own most common stop count
would get 75%. M looks at one signal, this weekend's practice wear, through the 7 earlier
races with the closest wear. This module adds signals that are also known before the race:

  track history   : the most common stop count in earlier races at this track, shrunk
                    toward the era average by how many such races there are
  pit loss        : race time lost to a green-flag stop here (race_strategy_pit_loss_v2,
                    earlier races only) -- an expensive stop should mean fewer stops
  track temperature: average practice track temperature this weekend (session_weather)
  era average     : mean stop count of earlier races in the era (all eras when too few)

The target is the race's most common stop count among dry legal strategies (clipped to
1-3; 0- and 4-stop race modes are vanishingly rare). Every signal of a race uses only
races before it, and a model for a target race is trained only on races before it.

  Q1 wear + track votes : M's 7 wear-neighbour votes plus TRACK_VOTE_WEIGHT votes for each
                          earlier race at this track (half for an earlier era)
  Q2 model: wear + track: multinomial logistic regression on wear, track history, era average
  Q3 model: all signals : Q2 + pit loss + practice track temperature

The stop count then goes through M's sequence and stop-lap rules (predict_from_wear_timed
with `stops`), so only the stop count differs from M.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from math import isfinite
from statistics import mean, median
from typing import Any, Iterable, Sequence

from sqlalchemy import text

from race_strategy_timing_v1 import WEAR_NEIGHBOURS, MIN_WEAR_RACES

CLASSES = (1, 2, 3)
TRACK_VOTE_WEIGHT = 2        # an earlier race here counts as two wear neighbours (earlier era: one)
TRACK_SHRINK_RACES = 2.0     # track history weighed against two races' worth of the era average
MIN_ERA_RACES = 8            # fewer earlier races in the era -> era average over all eras
MIN_TRAIN_RACES = 30         # fewer earlier races with a label -> the model abstains (caller falls back)
REGULARISATION_C = 0.5


def clip_stops(stops: int) -> int:
    return min(max(stops, CLASSES[0]), CLASSES[-1])


@dataclass(frozen=True)
class RaceSignals:
    race_id: int
    race_date: date
    track_id: int
    regulation_era: str
    wear: float | None
    pit_loss: float | None
    track_temp: float | None


@dataclass(frozen=True)
class RaceLabel:
    race_date: date
    track_id: int
    regulation_era: str
    stops: int                # race's most common stop count, clipped to 1-3


def era_average(labels: dict[int, RaceLabel], *, regulation_era: str, before: date) -> float | None:
    earlier = [l for l in labels.values() if l.race_date < before]
    same = [l.stops for l in earlier if l.regulation_era == regulation_era]
    pool = same if len(same) >= MIN_ERA_RACES else [l.stops for l in earlier]
    return mean(pool) if pool else None


def track_history(labels: dict[int, RaceLabel], *, track_id: int, regulation_era: str,
                  before: date) -> list[tuple[int, float]]:
    """(stops, weight) of earlier races at this track: 1 in this era, 0.5 in an earlier one."""
    return [(l.stops, 1.0 if l.regulation_era == regulation_era else 0.5)
            for l in labels.values() if l.track_id == track_id and l.race_date < before]


def track_prior(labels: dict[int, RaceLabel], *, track_id: int, regulation_era: str, before: date,
                k: float = TRACK_SHRINK_RACES) -> float | None:
    """Weighted mean stop count here, pulled toward the era average by k races' worth."""
    centre = era_average(labels, regulation_era=regulation_era, before=before)
    if centre is None:
        return None
    rows = track_history(labels, track_id=track_id, regulation_era=regulation_era, before=before)
    return (sum(s * w for s, w in rows) + k * centre) / (sum(w for _, w in rows) + k)


def stops_wear_and_track(labels: dict[int, RaceLabel], wear_by_race: dict[int, float | None], *,
                         wear: float | None, track_id: int, regulation_era: str, before: date,
                         k: int = WEAR_NEIGHBOURS, weight: float = TRACK_VOTE_WEIGHT) -> int | None:
    """Method Q1: M's wear-neighbour votes plus votes from earlier races at this track.
    Ties go to fewer stops, as in stops_from_wear."""
    votes: Counter = Counter()
    if wear is not None:
        known = [(abs(wear_by_race[r] - wear), l.stops, l.regulation_era) for r, l in labels.items()
                 if l.race_date < before and wear_by_race.get(r) is not None]
        same_era = [x for x in known if x[2] == regulation_era]
        pool = same_era if len(same_era) >= MIN_WEAR_RACES else known
        if len(pool) >= k:
            for _, stops, _ in sorted(pool)[:k]:
                votes[stops] += 1.0
    for stops, w in track_history(labels, track_id=track_id, regulation_era=regulation_era, before=before):
        votes[stops] += weight * w
    if not votes:
        return None
    return max(votes, key=lambda n: (votes[n], -n))


FEATURE_SETS = {
    "wear_track": ("wear", "track_prior", "era_average"),
    "all": ("wear", "track_prior", "era_average", "pit_loss", "track_temp"),
}


def race_features(signal: RaceSignals, labels: dict[int, RaceLabel]) -> dict[str, float | None]:
    """Every feature of a race from data before it."""
    return {
        "wear": signal.wear,
        "track_prior": track_prior(labels, track_id=signal.track_id, regulation_era=signal.regulation_era,
                                   before=signal.race_date),
        "era_average": era_average(labels, regulation_era=signal.regulation_era, before=signal.race_date),
        "pit_loss": signal.pit_loss,
        "track_temp": signal.track_temp,
    }


class StopCountModel:
    """Multinomial logistic regression on standardised features; missing values take the
    training median (so a race with no practice wear still gets a prediction)."""

    def __init__(self, names: Sequence[str], c: float = REGULARISATION_C):
        self.names, self.c = tuple(names), c
        self.fitted = None

    def _row(self, features: dict[str, float | None]) -> list[float]:
        out = []
        for name, centre, scale in zip(self.names, self.centres, self.scales):
            value = features.get(name)
            value = centre if value is None or not isfinite(value) else value
            out.append((value - centre) / scale)
        return out

    def fit(self, rows: Sequence[tuple[dict[str, float | None], int]]) -> "StopCountModel":
        from sklearn.linear_model import LogisticRegression

        self.centres, self.scales = [], []
        for name in self.names:
            values = [f[name] for f, _ in rows if f.get(name) is not None and isfinite(f[name])]
            centre = median(values) if values else 0.0
            spread = (sum((v - centre) ** 2 for v in values) / len(values)) ** 0.5 if values else 0.0
            self.centres.append(centre)
            self.scales.append(spread or 1.0)
        labels = [y for _, y in rows]
        if len(set(labels)) < 2:
            self.fitted, self.constant = None, labels[0] if labels else None
            return self
        self.fitted = LogisticRegression(C=self.c, max_iter=2000).fit([self._row(f) for f, _ in rows], labels)
        return self

    def probabilities(self, features: dict[str, float | None]) -> dict[int, float]:
        if self.fitted is None:
            return {self.constant: 1.0} if self.constant is not None else {}
        probs = self.fitted.predict_proba([self._row(features)])[0]
        return {int(c): float(p) for c, p in zip(self.fitted.classes_, probs)}

    def predict(self, features: dict[str, float | None]) -> int | None:
        probs = self.probabilities(features)
        return max(probs, key=lambda n: (probs[n], -n)) if probs else None


def model_stops(signals: dict[int, RaceSignals], labels: dict[int, RaceLabel], *, race_id: int,
                feature_set: str, features_by_race: dict[int, dict] | None = None) -> int | None:
    """Q2/Q3: train on earlier labelled races, predict this one. None with too little history."""
    features_by_race = features_by_race if features_by_race is not None else {}

    def features(r: int) -> dict:
        if r not in features_by_race:
            features_by_race[r] = race_features(signals[r], labels)
        return features_by_race[r]

    target = signals[race_id]
    train = [(features(r), l.stops) for r, l in labels.items()
             if l.race_date < target.race_date and r in signals]
    if len(train) < MIN_TRAIN_RACES:
        return None
    return StopCountModel(FEATURE_SETS[feature_set]).fit(train).predict(features(race_id))


# --- loading -------------------------------------------------------------------------------


def race_labels(cars: Iterable) -> dict[int, RaceLabel]:
    """race_id -> the race's most common stop count among dry legal strategies (>= 5 cars)."""
    from race_strategy_timing_v1 import race_modes

    cars = list(cars)
    track = {c.race_id: c.track_id for c in cars}
    return {r: RaceLabel(d, track[r], era, clip_stops(stops)) for r, (era, d, stops) in race_modes(cars).items()}


def load_practice_track_temps(db: Any) -> dict[int, float]:
    """race_id -> mean track temperature over its practice sessions (session_weather)."""
    return {int(r): float(t) for r, t in db.execute(text("""
        SELECT s.race_id, AVG(sw.track_temp_avg)
        FROM sessions s JOIN session_weather sw ON sw.session_id = s.id
        WHERE s.session_type IN ('FP1', 'FP2', 'FP3') AND sw.track_temp_avg IS NOT NULL
        GROUP BY s.race_id
    """)) if t is not None}


def load_signals(db: Any, cars: Sequence, wear_by_race: dict[int, float | None], *,
                 end_year: int) -> dict[int, RaceSignals]:
    """Signals for every race in `cars`; pit loss from races strictly before each race."""
    from race_strategy_pit_loss_v2 import estimate_pit_loss, load_observations

    observations = load_observations(db, start_year=2018, end_year=end_year)
    temps = load_practice_track_temps(db)
    races = {c.race_id: c for c in cars}
    out = {}
    for race_id, c in races.items():
        loss = estimate_pit_loss(observations, track_id=c.track_id, regulation_era=c.regulation_era,
                                 before_date=c.race_date)
        out[race_id] = RaceSignals(race_id, c.race_date, c.track_id, c.regulation_era, wear_by_race.get(race_id),
                                   loss.median_seconds if loss else None, temps.get(race_id))
    return out


def signal_report(signals: dict[int, RaceSignals], labels: dict[int, RaceLabel], race_ids: Iterable[int]) -> None:
    """Median of each signal by the race's real stop count (scored races only)."""
    by_stops = defaultdict(list)
    for r in race_ids:
        if r in labels and r in signals:
            by_stops[labels[r].stops].append(race_features(signals[r], labels))
    print("\nsignal medians by the race's most common stop count (scored races):")
    print("stops  races   wear %/lap   track history   era average   pit loss s   track temp C")
    for stops in sorted(by_stops):
        rows = by_stops[stops]

        def med(name, scale=1.0):
            values = [f[name] * scale for f in rows if f[name] is not None]
            return f"{median(values):8.3f} (n={len(values)})" if values else "   -"
        print(f"{stops:5} {len(rows):6}   {med('wear', 100)}   {med('track_prior')}   {med('era_average')}   "
              f"{med('pit_loss')}   {med('track_temp')}")
