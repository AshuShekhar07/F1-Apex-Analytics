"""Predicted winning tyre strategy: the plan for the FRONT of the grid, with pit windows.

Not a plan for every car. The cars that can win start at the front, so the prediction is
the strategy the top FRONT_GRID starters have used at this track, with a pit WINDOW per
stop ("first stop laps 21-27, second 50-57") instead of a single lap.

Safety Cars are left out on purpose: nobody can know before the race when (or if) one
comes. Any front-grid car that made a stop under an SC, VSC or red flag is dropped from
the history, so plans and windows describe green-flag racing only.

History (strictly earlier races, dry, legal plans, grid 1..FRONT_GRID, no neutralised stop):
  * this track: same regulation era weight 1, earlier era EARLIER_ERA_WEIGHT
  * the era's front-grid cars at all tracks, worth TRACK_SHRINK cars, so a track with
    little history leans on the era (a new track gets the era's answer)

  stop count : highest weighted share (ties -> fewer stops)
  sequence   : highest weighted share among plans with that stop count
  windows    : weighted WINDOW_LOW-WINDOW_HIGH quantiles of each stop's lap (as a share of
               race distance, scaled to this race) of the cars that ran that stop count here;
               the era's when fewer than MIN_WINDOW_CARS (weighted) did
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from race_strategy_timing_v1 import CarStrategy, is_legal_dry

FRONT_GRID = 5
EARLIER_ERA_WEIGHT = 0.5
TRACK_SHRINK = 5.0          # the era's answer counts as 5 front-grid cars
MIN_WINDOW_CARS = 4.0       # weighted track cars needed for track-based windows
WINDOW_LOW, WINDOW_HIGH = 0.25, 0.75
NEUTRALISATIONS = ("SC", "VSC", "RED_FLAG")


@dataclass(frozen=True)
class PitWindow:
    stop: int                 # 1 = first stop
    first_lap: int
    last_lap: int
    typical_lap: int          # weighted median


@dataclass(frozen=True)
class WinningStrategy:
    sequence: tuple[str, ...]
    windows: tuple[PitWindow, ...]
    stop_chances: dict[int, float]          # stop count -> weighted share
    track_cars: float                       # weighted front-grid cars at this track behind it
    track_races: int
    window_source: str                      # track | era
    source: str

    @property
    def stops(self) -> int:
        return len(self.sequence) - 1


def stop_in_laps(car: CarStrategy) -> list[int]:
    return [s.end_lap for s in car.stints[:-1]]


def is_front(car: CarStrategy, front_grid: int = FRONT_GRID) -> bool:
    return car.grid is not None and 1 <= car.grid <= front_grid


def clean_front_cars(cars: Iterable[CarStrategy], neutralised_laps: dict[int, frozenset[int] | None], *,
                     front_grid: int = FRONT_GRID) -> list[CarStrategy]:
    """Dry front-grid cars on legal plans with no stop under SC/VSC/red flag. A race whose
    windows are unknown (None or missing) is kept: unknown is not evidence of a neutralisation."""
    out = []
    for c in cars:
        if c.wet or not is_front(c, front_grid) or not is_legal_dry(c.sequence):
            continue
        laps = neutralised_laps.get(c.race_id) or frozenset()
        if any(lap in laps for lap in stop_in_laps(c)):
            continue
        out.append(c)
    return out


def _weighted_quantile(pairs: Sequence[tuple[float, float]], q: float) -> float:
    ordered = sorted(pairs)
    total = sum(w for _, w in ordered)
    running = 0.0
    for value, w in ordered:
        running += w
        if running >= q * total - 1e-12:
            return value
    return ordered[-1][0]


def _weights(history: Sequence[CarStrategy], *, track_id: int, regulation_era: str) -> list[tuple[CarStrategy, float]]:
    return [(c, 1.0 if c.regulation_era == regulation_era else EARLIER_ERA_WEIGHT)
            for c in history if c.track_id == track_id]


def _shares(rows: Sequence[tuple[CarStrategy, float]], key) -> dict:
    out: dict = defaultdict(float)
    for c, w in rows:
        out[key(c)] += w
    return out


def _windows(rows: Sequence[tuple[CarStrategy, float]], stops: int, total_laps: int) -> tuple[PitWindow, ...]:
    windows = []
    for i in range(stops):
        pairs = [(c.stop_fractions[i], w) for c, w in rows]
        lo, mid, hi = (_weighted_quantile(pairs, q) for q in (WINDOW_LOW, 0.5, WINDOW_HIGH))
        first, typical, last = (max(1, min(total_laps - 1, round(f * total_laps))) for f in (lo, mid, hi))
        windows.append(PitWindow(i + 1, first, last, typical))
    return tuple(windows)


def predict_winning_strategy(history: Sequence[CarStrategy], *, track_id: int, regulation_era: str,
                             total_laps: int, k: float = TRACK_SHRINK) -> WinningStrategy | None:
    """history: clean front-grid cars from strictly earlier races (clean_front_cars)."""
    era_cars = [c for c in history if c.regulation_era == regulation_era]
    if not era_cars:
        era_cars = list(history)
    if not era_cars:
        return None
    track = _weights(history, track_id=track_id, regulation_era=regulation_era)
    track_total = sum(w for _, w in track)

    def blended(key, track_rows, era_rows):
        era_share = _shares([(c, 1.0) for c in era_rows], key)
        era_n = sum(era_share.values())
        track_share = _shares(track_rows, key)
        keys = set(era_share) | set(track_share)
        total = sum(w for _, w in track_rows) + k
        return {x: (track_share.get(x, 0.0) + k * era_share.get(x, 0.0) / era_n) / total for x in keys} if era_n else {}

    def stop_count(c):
        return len(c.sequence) - 1

    stop_chances = blended(stop_count, track, era_cars)
    if not stop_chances:
        return None
    stops = max(stop_chances, key=lambda n: (stop_chances[n], -n))
    track_same = [(c, w) for c, w in track if stop_count(c) == stops]
    era_same = [c for c in era_cars if stop_count(c) == stops]
    sequence_chances = blended(lambda c: c.sequence, track_same, era_same)
    sequence = max(sequence_chances, key=lambda s: (sequence_chances[s], s))

    if sum(w for _, w in track_same) >= MIN_WINDOW_CARS:
        window_rows, window_source = track_same, "track"
    else:
        window_rows, window_source = [(c, 1.0) for c in era_same], "era"
    windows = _windows(window_rows, stops, total_laps) if stops else ()
    races = len({c.race_id for c, _ in track})
    source = (f"{track_total:g} front-grid cars in {races} earlier race(s) here" if races
              else "no earlier race here: the era's front-grid plans")
    return WinningStrategy(sequence, windows, dict(sorted(stop_chances.items())), track_total, races,
                           window_source, source)


def load_neutralised_laps(db: Any, race_ids: Iterable[int]) -> dict[int, frozenset[int] | None]:
    """race_id -> SC/VSC/red-flag leader laps; None when the windows are unknown."""
    from race_neutralisations_v1 import load_race_neutralisations

    out = {}
    for race_id in race_ids:
        n = load_race_neutralisations(db, race_id)
        out[race_id] = n.neutralised_laps(NEUTRALISATIONS) if n.coverage == "ok" else None
    return out


def format_strategy(p: WinningStrategy) -> str:
    plan = " -> ".join(p.sequence)
    stops = "1 stop" if p.stops == 1 else f"{p.stops} stops"
    lines = [f"Predicted winning strategy: {plan} ({stops})"]
    names = ("First", "Second", "Third", "Fourth")
    for w in p.windows:
        lines.append(f"  {names[w.stop - 1] if w.stop <= 4 else f'Stop {w.stop}'} pit window: "
                     f"laps {w.first_lap}-{w.last_lap} (typical lap {w.typical_lap})")
    chances = ", ".join(f"{n} stop{'s' if n != 1 else ''} {share:.0%}" for n, share in p.stop_chances.items())
    lines.append(f"  Stop count chances (front grid): {chances}")
    lines.append(f"  Based on: {p.source}; windows from the {'track' if p.window_source == 'track' else 'era'}")
    lines.append("  Green-flag plan: a Safety Car or VSC can open an earlier, cheaper stop.")
    return "\n".join(lines)
