"""Confidence label for a race forecast: how far off the prediction usually is, measured.

The finishing-order forecast is less accurate in some races than others. The scoreboard
showed the extra error in wet races cannot be predicted away (prediction_scoreboard_v1:
even knowing the race was wet gains nothing), so the forecast says so instead of looking
equally sure every weekend.

Everything here is measured, never set by hand:
  samples   the production blend replayed walk-forward on every earlier season
            (predicted position, actual finish, wet qualifying?, wet race?)
  condition known before the race:
              dry_quali      qualifying was dry and no rain is forecast
              wet_quali      qualifying was wet (session_weather)
              rain_forecast  qualifying dry but rain forecast for the race; measured on
                             earlier races that were actually wet
  expected_error  average |predicted - finish| per driver in earlier races of that condition
  range       per driver: predicted position +/- the whole number of places that held
              closest to COVERAGE of drivers predicted in the same band (P1-3, P4-10, P11+)
              and condition; the forecast shows the share that actually held
  level       "normal", or "low" when the condition's expected error is at least
              LOW_RATIO x the dry-qualifying one

The scoreboard checks it walk-forward: each season's ranges are fitted on earlier seasons
only, and the share of drivers who actually finished inside their range is reported
(should be close to COVERAGE).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import numpy as np

COVERAGE = 0.8          # ranges should contain the actual finish for 80% of drivers
MIN_CELL = 60           # drivers needed in a band x condition cell, else the band across conditions
LOW_RATIO = 1.1         # condition error >= 1.1 x dry-qualifying error -> "low" confidence
BANDS = (("P1-3", 1, 3), ("P4-10", 4, 10), ("P11+", 11, 99))
CONDITIONS = ("dry_quali", "wet_quali", "rain_forecast")
LABELS = {"dry_quali": "dry qualifying", "wet_quali": "wet qualifying", "rain_forecast": "rain forecast for the race"}


@dataclass(frozen=True)
class ErrorSample:
    race_id: int
    season_year: int
    predicted: int
    finish: int
    wet_quali: bool
    wet_race: bool

    @property
    def error(self) -> int:
        return abs(self.predicted - self.finish)


def band(predicted: int) -> str:
    return next(name for name, lo, hi in BANDS if lo <= predicted <= hi)


def in_condition(s: ErrorSample, condition: str) -> bool:
    if condition == "dry_quali":
        return not s.wet_quali
    if condition == "wet_quali":
        return s.wet_quali
    return s.wet_race and not s.wet_quali


def _half_width(errors: Sequence[int]) -> int:
    """Whole-place width whose share of errors <= width is closest to COVERAGE (ties: wider).
    Rounding the quantile up instead overshot: 84.5% inside on 2018-2026 for an 80% target."""
    errors = np.asarray(errors)
    return min(range(int(errors.max()) + 1), key=lambda w: (abs(float((errors <= w).mean()) - COVERAGE), -w))


@dataclass(frozen=True)
class ErrorTable:
    half_width: dict[tuple[str, str], int]     # (condition, band) -> places
    expected_error: dict[str, float]           # condition -> mean error per driver
    samples: dict[str, int]                    # condition -> drivers
    races: dict[str, int]                      # condition -> races
    inside: dict[str, float]                   # condition -> share of its drivers inside their range

    @classmethod
    def fit(cls, samples: Iterable[ErrorSample]) -> "ErrorTable":
        samples = list(samples)
        if not samples:
            raise ValueError("no earlier races to measure the forecast error on")
        by_band = defaultdict(list)
        for s in samples:
            by_band[band(s.predicted)].append(s.error)
        half, expected, counts, races, inside = {}, {}, {}, {}, {}
        for condition in CONDITIONS:
            chosen = [s for s in samples if in_condition(s, condition)]
            counts[condition] = len(chosen)
            races[condition] = len({s.race_id for s in chosen})
            expected[condition] = float(np.mean([s.error for s in chosen])) if chosen else float("nan")
            for name, _, _ in BANDS:
                cell = [s.error for s in chosen if band(s.predicted) == name]
                pool = cell if len(cell) >= MIN_CELL else by_band.get(name, [])
                half[(condition, name)] = _half_width(pool) if pool else 0
            inside[condition] = (float(np.mean([s.error <= half[(condition, band(s.predicted))] for s in chosen]))
                                 if chosen else float("nan"))
        return cls(half, expected, counts, races, inside)

    def condition_for(self, wet_quali: bool, rain_expected: bool) -> str:
        condition = "wet_quali" if wet_quali else "rain_forecast" if rain_expected else "dry_quali"
        # too few earlier wet races measured: say so through the dry numbers, never invent
        return condition if self.samples.get(condition, 0) >= MIN_CELL else "dry_quali"

    def label(self, *, wet_quali: bool, rain_expected: bool, predicted: dict[int, int], field: int) -> dict:
        """Forecast block: level, reason, expected error and a range per driver."""
        condition = self.condition_for(wet_quali, rain_expected)
        wanted = "wet_quali" if wet_quali else "rain_forecast" if rain_expected else "dry_quali"
        expected, dry = self.expected_error[condition], self.expected_error["dry_quali"]
        level = "low" if condition != "dry_quali" and expected >= LOW_RATIO * dry else "normal"
        ranges = {}
        for driver, position in predicted.items():
            w = self.half_width[(condition, band(position))]
            ranges[driver] = [max(1, position - w), min(field, position + w)]
        reason = LABELS[condition]
        if wanted != condition:
            reason = f"{LABELS[wanted]}, but only {self.samples.get(wanted, 0)} earlier drivers measured; dry numbers shown"
        return {
            "level": level, "condition": condition, "reason": reason,
            "expected_error_places": round(expected, 2), "dry_expected_error_places": round(dry, 2),
            "coverage": round(self.inside[condition], 3), "measured_on_races": self.races[condition],
            "half_width": {name: self.half_width[(condition, name)] for name, _, _ in BANDS},
            "ranges": {str(d): r for d, r in ranges.items()},
        }


def backtest_samples(rows, transform_for: Callable, choose_weight: Callable, predictions: Callable,
                     wet_races: set[int]) -> list[ErrorSample]:
    """Replay the production blend on every season in `rows` exactly as the scoreboard does:
    pace transform fitted on earlier seasons, blend weight chosen on the previous season."""
    by_season = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_season[r.season_year][r.race_id].append(r)
    out = []
    for season in sorted(by_season):
        history = [r for r in rows if r.season_year < season]
        transform = transform_for(history)
        previous = by_season.get(season - 1)
        weight = choose_weight({k: transform(v) for k, v in previous.items()}) if previous else 0.5
        for race_id, race_rows in by_season[season].items():
            ranked = predictions(transform(race_rows), weight)["blend"]
            wet_q = any(r.quali_wet for r in race_rows)
            out += [ErrorSample(race_id, season, p, r.finish, wet_q, race_id in wet_races)
                    for r, p in zip(race_rows, ranked)]
    return out


def coverage_check(samples: Sequence[ErrorSample]) -> dict[str, tuple[int, float, float, float]]:
    """Walk-forward check: for each season, fit on earlier seasons and test on it.
    condition -> (drivers, share inside range, expected error said, actual error)."""
    seasons = sorted({s.season_year for s in samples})
    tally = defaultdict(lambda: [0, 0, 0.0, 0.0])
    for season in seasons[1:]:
        earlier = [s for s in samples if s.season_year < season]
        try:
            table = ErrorTable.fit(earlier)
        except ValueError:
            continue
        for s in samples:
            if s.season_year != season:
                continue
            condition = table.condition_for(s.wet_quali, s.wet_race and not s.wet_quali)
            for name in ("all", condition):
                t = tally[name]
                t[0] += 1
                t[1] += s.error <= table.half_width[(condition, band(s.predicted))]
                t[2] += table.expected_error[condition]
                t[3] += s.error
    return {k: (n, inside / n, said / n, actual / n) for k, (n, inside, said, actual) in tally.items() if n}
