"""Strategy prediction v2: real stints, stop timing, and a pre-start stage.

Three changes over race_strategy_precedent_v1 (which used race_stints):

1. REAL stints. race_stints splits on compound changes only, so same-compound
   stops vanish (MEDIUM-HARD-HARD is stored as a one-stop MEDIUM-HARD) -- both
   the history and the answers it was scored against undercounted stops. Stints
   here are rebuilt from lap data: a new stint starts on a pit out-lap or when
   tyre life resets (red-flag tyre change). A compound change alone only splits
   when the lap has no tyre-life data, so one mislabelled lap is not a pit stop.

2. Stop timing from what teams did at this track: stop laps are the median stop
   laps (as a fraction of race distance) of the SAME sequence in the most
   specific pool with enough examples: track + grid band, track, era + band, era.

3. A pre-start stage. Teams declare starting tyres before the start; when the
   starting compound is known, history is filtered to strategies that started
   on it. Forecasts can therefore be issued after qualifying (compound unknown)
   and again before the start (compound known).

Walk-forward: only races strictly before the target date are used.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any, Iterable, Sequence

from sqlalchemy import text

from race_strategy_precedent_v1 import build_strategy, grid_band
from race_strategy_simulator_v1 import DRY_COMPOUNDS, Strategy

WET_COMPOUNDS = ("INTERMEDIATE", "WET")
KNOWN_COMPOUNDS = DRY_COMPOUNDS + WET_COMPOUNDS
MIN_POOL_CARS = 15        # cars needed before a pool level is trusted
MIN_SEQUENCE_EXAMPLES = 5  # examples of the chosen sequence needed for its stop timing


@dataclass(frozen=True)
class LapTyre:
    lap_number: int
    compound: str | None
    tyre_life: float | None
    pit_out: bool


@dataclass(frozen=True)
class Stint:
    compound: str
    start_lap: int
    end_lap: int


@dataclass(frozen=True)
class CarStrategy:
    race_id: int
    race_date: date
    season_year: int
    track_id: int
    regulation_era: str
    grid: int | None
    wet: bool
    race_laps: int
    stints: tuple[Stint, ...]

    @property
    def sequence(self) -> tuple[str, ...]:
        return tuple(s.compound for s in self.stints)

    @property
    def stop_fractions(self) -> tuple[float, ...]:
        return tuple(s.end_lap / self.race_laps for s in self.stints[:-1])

    def as_strategy(self) -> Strategy:
        bounds = [s.end_lap for s in self.stints[:-1]]
        return build_strategy(self.sequence, bounds, self.stints[-1].end_lap) or Strategy(
            " → ".join(self.sequence), (), source="realised")


def normalise_compound(value: Any) -> str | None:
    if value is None:
        return None
    name = str(value).strip().upper()
    return name if name in KNOWN_COMPOUNDS else None


def stints_from_laps(laps: Iterable[LapTyre]) -> list[Stint]:
    """Split a car's race into stints: pit out-lap, compound change, or tyre-life reset."""
    ordered = sorted(laps, key=lambda l: l.lap_number)
    stints: list[list] = []  # [compounds seen, start_lap, end_lap]
    previous: LapTyre | None = None
    for lap in ordered:
        compound = normalise_compound(lap.compound)
        new_stint = previous is None
        if previous is not None:
            if lap.pit_out and lap.lap_number > 1:
                new_stint = True
            elif (lap.tyre_life is not None and previous.tyre_life is not None
                  and lap.tyre_life < previous.tyre_life):
                new_stint = True          # tyres changed without a pit out-lap (red flag)
            elif (lap.tyre_life is None and compound and stints[-1][0]
                  and compound != Counter(stints[-1][0]).most_common(1)[0][0]):
                new_stint = True          # no tyre-life data: fall back to a compound change
        if new_stint:
            stints.append([[], lap.lap_number, lap.lap_number])
        if compound:
            stints[-1][0].append(compound)
        stints[-1][2] = lap.lap_number
        previous = lap
    out = []
    for compounds, start, end in stints:
        if not compounds:
            if out:          # no compound info (e.g. a lone lap): extend the previous stint
                out[-1] = Stint(out[-1].compound, out[-1].start_lap, end)
            continue
        out.append(Stint(Counter(compounds).most_common(1)[0][0], start, end))
    return out


def is_legal_dry(sequence: Sequence[str]) -> bool:
    return len(sequence) >= 2 and all(c in DRY_COMPOUNDS for c in sequence) and len(set(sequence)) >= 2


def _pool_levels(history: Sequence[CarStrategy], *, track_id: int, era: str, grid: int | None,
                 start_compound: str | None, wet: bool):
    pool = [h for h in history if h.regulation_era == era and h.wet == wet
            and (is_legal_dry(h.sequence) if not wet else len(h.stints) >= 1)]
    if start_compound:
        pool = [h for h in pool if h.sequence and h.sequence[0] == start_compound]
    band = grid_band(grid)
    return (
        ("track_band", [h for h in pool if h.track_id == track_id and grid_band(h.grid) == band]),
        ("track", [h for h in pool if h.track_id == track_id]),
        ("era_band", [h for h in pool if grid_band(h.grid) == band]),
        ("era", pool),
    )


@dataclass(frozen=True)
class StrategyPrediction:
    strategy: Strategy | None
    source: str
    alternatives: tuple[tuple[Strategy, float], ...]


def predict_strategy(
    history: Sequence[CarStrategy],
    *,
    track_id: int,
    regulation_era: str,
    grid: int | None,
    total_laps: int,
    start_compound: str | None = None,
    wet: bool = False,
    top_k: int = 4,
) -> StrategyPrediction:
    """Most likely strategy + weighted alternatives from the most specific adequate pool."""
    start_compound = normalise_compound(start_compound)
    levels = _pool_levels(history, track_id=track_id, era=regulation_era, grid=grid,
                          start_compound=start_compound, wet=wet)
    chosen_level, rows = next(((name, rows) for name, rows in levels if len(rows) >= MIN_POOL_CARS),
                              levels[-1])
    if not rows:
        return StrategyPrediction(None, "none", ())
    counts = Counter(h.sequence for h in rows)
    alternatives = []
    for sequence, count in counts.most_common(top_k):
        # stop timing: the most specific level with enough examples of THIS sequence
        timing_rows = next((
            [h for h in lvl_rows if h.sequence == sequence]
            for _, lvl_rows in levels
            if sum(1 for h in lvl_rows if h.sequence == sequence) >= MIN_SEQUENCE_EXAMPLES
        ), [h for h in rows if h.sequence == sequence])
        stops = [round(median(h.stop_fractions[i] for h in timing_rows) * total_laps)
                 for i in range(len(sequence) - 1)]
        strategy = build_strategy(sequence, stops, total_laps)
        if strategy is not None:
            alternatives.append((strategy, count / len(rows)))
    source = f"{chosen_level}(n={len(rows)})" + (f", start={start_compound}" if start_compound else "")
    return StrategyPrediction(alternatives[0][0] if alternatives else None, source, tuple(alternatives))


# --- database ------------------------------------------------------------------

def load_car_strategies(db: Any, *, before_date: date | None = None, start_year: int = 2018,
                        end_year: int = 2026, min_distance: float = 0.9) -> list[CarStrategy]:
    """Real stints for every car that covered >= min_distance of a race (R sessions)."""
    races = db.execute(text("""
        SELECT r.id, r.race_date, r.season_year, r.track_id, r.regulation_era, s.id AS session_id,
               COALESCE(sw.rainfall, FALSE) AS wet
        FROM races r
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        LEFT JOIN session_weather sw ON sw.session_id = s.id
        WHERE r.season_year BETWEEN :a AND :b AND r.regulation_era IS NOT NULL
          AND (CAST(:before AS DATE) IS NULL OR r.race_date < :before)
        ORDER BY r.race_date
    """), {"a": start_year, "b": end_year, "before": before_date}).mappings().all()
    out: list[CarStrategy] = []
    for race in races:
        laps = defaultdict(list)
        for entry, lap_number, compound, tyre_life, pit_out in db.execute(text("""
            SELECT race_entry_id, lap_number, tire_compound, tyre_life_laps, pit_out_time_seconds IS NOT NULL
            FROM laps WHERE session_id = :s
        """), {"s": race["session_id"]}):
            laps[entry].append(LapTyre(int(lap_number), compound,
                                       float(tyre_life) if tyre_life is not None else None, bool(pit_out)))
        if not laps:
            continue
        race_laps = max(l.lap_number for rows in laps.values() for l in rows)
        grids = dict(db.execute(text("""
            SELECT rr.race_entry_id, rr.starting_grid_position FROM race_results rr WHERE rr.session_id = :s
        """), {"s": race["session_id"]}).all())
        for entry, rows in laps.items():
            if max(l.lap_number for l in rows) < min_distance * race_laps:
                continue
            stints = stints_from_laps(rows)
            if stints:
                out.append(CarStrategy(race["id"], race["race_date"], race["season_year"], race["track_id"],
                                       race["regulation_era"], grids.get(entry), bool(race["wet"]),
                                       race_laps, tuple(stints)))
    return out
