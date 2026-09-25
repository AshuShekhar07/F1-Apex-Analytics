"""Strategy precedent: predict a car's race strategy from what teams actually did.

Layer 1 of the production strategy stack. No tyre model is involved, so the
unidentifiable tyre-wear problem cannot corrupt it. Measured on 2024-2025 pole
sitters, the era's most common strategy already matched the realised sequence
68% of the time and the stop count 79%.

Evidence hierarchy (most specific pool with >= min_cars wins; the source is
always reported):
  1. same track, same era, same grid band (front: grid 1-10, back: 11+)
  2. same track, same era
  3. same era, same grid band
  4. same era

Only dry races, cars covering >= 90% of the race distance, legal dry
strategies (>= 2 different dry compounds), and seasons strictly before the
target season are used.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable, Sequence

from sqlalchemy import text

from race_strategy_simulator_v1 import DRY_COMPOUNDS, Strategy, StrategyStint

FRONT_BAND_MAX_GRID = 10


@dataclass(frozen=True)
class HistoricalStrategy:
    sequence: tuple[str, ...]
    stop_fractions: tuple[float, ...]  # stop lap / race laps


@dataclass(frozen=True)
class PrecedentRecord:
    season_year: int
    track_id: int
    regulation_era: str
    grid: int | None
    strategy: HistoricalStrategy


def grid_band(grid: int | None) -> str:
    return "front" if grid is not None and 0 < grid <= FRONT_BAND_MAX_GRID else "back"


def is_legal_dry(sequence: Sequence[str]) -> bool:
    return len(sequence) >= 2 and all(c in DRY_COMPOUNDS for c in sequence) and len(set(sequence)) >= 2


def build_strategy(sequence: Sequence[str], stops: Sequence[int], total_laps: int) -> Strategy | None:
    stops = [max(2, min(total_laps - 1, int(s))) for s in stops]
    if any(b <= a for a, b in zip(stops, stops[1:])):
        return None
    bounds = [0, *stops, total_laps]
    stints = tuple(StrategyStint(sequence[i], bounds[i] + 1, bounds[i + 1]) for i in range(len(sequence)))
    return Strategy(f"{' → '.join(sequence)} [{','.join(map(str, stops))}]", stints, source="precedent")


def historical_strategy_options(
    history: Iterable[HistoricalStrategy], total_laps: int, *, top_k: int = 6
) -> list[tuple[Strategy, float]]:
    """Most frequent legal dry sequences with median stop laps, weighted by frequency."""
    by_sequence: dict[tuple[str, ...], list[tuple[float, ...]]] = defaultdict(list)
    for h in history:
        if is_legal_dry(h.sequence) and len(h.stop_fractions) == len(h.sequence) - 1:
            by_sequence[h.sequence].append(h.stop_fractions)
    ranked = sorted(by_sequence.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top_k]
    options = []
    for sequence, fractions in ranked:
        stops = [round(median(f[i] for f in fractions) * total_laps) for i in range(len(sequence) - 1)]
        strategy = build_strategy(sequence, stops, total_laps)
        if strategy is not None:
            options.append((strategy, float(len(fractions))))
    return options


def candidates_from_options(
    options: Sequence[tuple[Strategy, float]], total_laps: int, offsets: Sequence[int] = (-6, -3, 0, 3, 6)
) -> list[Strategy]:
    """Each precedent sequence with its stops shifted by each offset (for the optimiser)."""
    seen, out = set(), []
    for strategy, _ in options:
        for offset in offsets:
            candidate = build_strategy(strategy.sequence, [s + offset for s in strategy.stop_laps], total_laps)
            if candidate is None:
                continue
            key = tuple((s.compound, s.start_lap, s.end_lap) for s in candidate.stints)
            if key not in seen:
                seen.add(key)
                out.append(candidate)
    return out


def precedent_options(
    records: Iterable[PrecedentRecord],
    *,
    track_id: int,
    regulation_era: str,
    grid: int | None,
    before_year: int,
    total_laps: int,
    min_cars: int = 20,
    top_k: int = 6,
) -> tuple[list[tuple[Strategy, float]], str]:
    """Weighted strategy options from the most specific pool with enough evidence."""
    pool = [r for r in records if r.regulation_era == regulation_era and r.season_year < before_year
            and is_legal_dry(r.strategy.sequence)]
    band = grid_band(grid)
    levels = (
        ("track_band", [r for r in pool if r.track_id == track_id and grid_band(r.grid) == band]),
        ("track", [r for r in pool if r.track_id == track_id]),
        ("era_band", [r for r in pool if grid_band(r.grid) == band]),
        ("era", pool),
    )
    for source, rows in levels:
        if len(rows) >= min_cars or source == "era":
            options = historical_strategy_options((r.strategy for r in rows), total_laps, top_k=top_k)
            if options:
                return options, f"{source}(n={len(rows)})"
    return [], "none"


def load_precedents(db: Any, *, before_year: int) -> list[PrecedentRecord]:
    """Realised dry-race strategies of cars covering >= 90% of the race distance."""
    rows = db.execute(text("""
        SELECT rs.race_id, rs.race_entry_id, rs.stint_number, UPPER(rs.compound) AS compound, rs.end_lap,
               MAX(rs.end_lap) OVER (PARTITION BY rs.race_id) AS race_laps,
               r.season_year, r.track_id, r.regulation_era, rr.starting_grid_position AS grid
        FROM race_stints rs
        JOIN races r ON r.id = rs.race_id
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        JOIN session_weather sw ON sw.session_id = s.id AND sw.rainfall = FALSE
        LEFT JOIN race_results rr ON rr.session_id = s.id AND rr.race_entry_id = rs.race_entry_id
        WHERE r.season_year < :year AND r.regulation_era IS NOT NULL
        ORDER BY rs.race_id, rs.race_entry_id, rs.stint_number
    """), {"year": before_year}).mappings().all()
    by_car: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in rows:
        by_car[(row["race_id"], row["race_entry_id"])].append(row)
    records = []
    for stints in by_car.values():
        first, race_laps = stints[0], stints[0]["race_laps"]
        if not race_laps or stints[-1]["end_lap"] < 0.9 * race_laps:
            continue
        records.append(PrecedentRecord(
            first["season_year"], first["track_id"], first["regulation_era"], first["grid"],
            HistoricalStrategy(tuple(s["compound"] for s in stints),
                               tuple(s["end_lap"] / race_laps for s in stints[:-1])),
        ))
    return records
