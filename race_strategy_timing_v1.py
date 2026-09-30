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


def _stop_laps(levels, sequence: tuple[str, ...], total_laps: int, fallback: Sequence[CarStrategy]) -> list[int]:
    """Median stop laps of `sequence` in the most specific level with enough examples of it."""
    timing_rows = next((
        [h for h in lvl_rows if h.sequence == sequence]
        for _, lvl_rows in levels
        if sum(1 for h in lvl_rows if h.sequence == sequence) >= MIN_SEQUENCE_EXAMPLES
    ), list(fallback))
    return [round(median(h.stop_fractions[i] for h in timing_rows) * total_laps) for i in range(len(sequence) - 1)]


def predict_era_mode_track_timing(history: Sequence[CarStrategy], *, track_id: int, regulation_era: str,
                                  grid: int | None, total_laps: int) -> StrategyPrediction:
    """Method H: the era's most common sequence (exactly B), timed like method C: stop laps
    from the most specific pool (track + grid band, track, era + band, era) with enough
    examples of that sequence. Only the stop laps can differ from production."""
    base = predict_era_mode(history, regulation_era=regulation_era, total_laps=total_laps, top_k=1)
    if base.strategy is None:
        return base
    levels = _pool_levels(history, track_id=track_id, era=regulation_era, grid=grid, start_compound=None, wet=False)
    sequence = base.strategy.sequence
    strategy = build_strategy(sequence, _stop_laps(levels, sequence, total_laps, [h for h in levels[-1][1]
                                                                                if h.sequence == sequence]), total_laps)
    return StrategyPrediction(strategy, base.source + ", track timing", ((strategy, base.alternatives[0][1]),))


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
        strategy = build_strategy(sequence, _stop_laps(levels, sequence, total_laps,
                                                       [h for h in rows if h.sequence == sequence]), total_laps)
        if strategy is not None:
            alternatives.append((strategy, count / len(rows)))
    source = f"{chosen_level}(n={len(rows)})" + (f", start={start_compound}" if start_compound else "")
    return StrategyPrediction(alternatives[0][0] if alternatives else None, source, tuple(alternatives))


def predict_era_mode(history: Sequence[CarStrategy], *, regulation_era: str, total_laps: int,
                     top_k: int = 4) -> StrategyPrediction:
    """The era's most common real-stint dry sequences, stop laps = era median fractions.

    Walk-forward 2024-25 (670 cars): as good as any richer method at stop count and first
    stop, and better than the track/grid-band hierarchy at the full sequence (0.307 vs 0.217).
    """
    pool = _pool_levels(history, track_id=-1, era=regulation_era, grid=None, start_compound=None, wet=False)[-1][1]
    if not pool:
        return StrategyPrediction(None, "none", ())
    alternatives = []
    for sequence, count in Counter(h.sequence for h in pool).most_common(top_k):
        rows = [h for h in pool if h.sequence == sequence]
        stops = [round(median(h.stop_fractions[i] for h in rows) * total_laps) for i in range(len(sequence) - 1)]
        strategy = build_strategy(sequence, stops, total_laps)
        if strategy is not None:
            alternatives.append((strategy, count / len(pool)))
    return StrategyPrediction(alternatives[0][0] if alternatives else None, f"era(n={len(pool)})", tuple(alternatives))


# Track-adjusted prediction (research): the era's answer, moved toward what teams did at
# THIS track in proportion to how much track evidence there is. Constants are set a priori
# (one race's worth of cars), not tuned on the races scored.
TRACK_SHRINK_CARS = 20       # track evidence weighed against 20 cars' worth of the era answer
EARLIER_ERA_WEIGHT = 0.5     # a car at this track in an earlier era counts half


def _stop_count(h: CarStrategy) -> int:
    return len(h.stints) - 1


def predict_track_adjusted(history: Sequence[CarStrategy], *, track_id: int, regulation_era: str,
                           total_laps: int, k: float = TRACK_SHRINK_CARS) -> StrategyPrediction:
    """Method E: stop count, then sequence, then stop laps -- each the era's value pulled
    toward this track's record: (track weighted count + k * era share) / (track weight + k)."""
    legal = [h for h in history if not h.wet and is_legal_dry(h.sequence)]
    era = [h for h in legal if h.regulation_era == regulation_era]
    if not era:
        return StrategyPrediction(None, "none", ())
    track = [(h, 1.0 if h.regulation_era == regulation_era else EARLIER_ERA_WEIGHT)
             for h in legal if h.track_id == track_id]
    track_weight = sum(w for _, w in track)

    def shrunk(key, era_rows, track_rows):
        era_counts = Counter(key(h) for h in era_rows)
        track_counts = defaultdict(float)
        for h, w in track_rows:
            track_counts[key(h)] += w
        weight = sum(track_counts.values())
        options = set(era_counts) | set(track_counts)
        return {o: (track_counts[o] + k * era_counts[o] / len(era_rows)) / (weight + k) for o in options}

    stop_probs = shrunk(_stop_count, era, track)
    stops = max(stop_probs, key=lambda n: (stop_probs[n], -n))
    era_n = [h for h in era if _stop_count(h) == stops] or era
    track_n = [(h, w) for h, w in track if _stop_count(h) == stops]
    seq_probs = shrunk(lambda h: h.sequence, era_n, track_n)
    sequence = max(seq_probs, key=lambda q: (seq_probs[q], q))
    if len(sequence) - 1 != stops:          # the track's only examples had another stop count
        sequence = Counter(h.sequence for h in era_n).most_common(1)[0][0]

    # stop laps: era median for this sequence, pulled toward the track's cars with the same stop count
    era_seq = [h for h in era if h.sequence == sequence] or era_n
    fractions = []
    for i in range(len(sequence) - 1):
        era_f = median(h.stop_fractions[i] for h in era_seq if len(h.stop_fractions) > i)
        rows = [(h.stop_fractions[i], w) for h, w in track_n if len(h.stop_fractions) > i]
        tw = sum(w for _, w in rows)
        fractions.append((sum(f * w for f, w in rows) + k * era_f) / (tw + k))
    strategy = build_strategy(sequence, [round(f * total_laps) for f in fractions], total_laps)
    source = f"track-adjusted(track cars={track_weight:g}, era cars={len(era)}, P({stops} stops)={stop_probs[stops]:.2f})"
    return StrategyPrediction(strategy, source, ((strategy, seq_probs[sequence]),) if strategy else ())


def predict_last_race_at_track(history: Sequence[CarStrategy], *, track_id: int, regulation_era: str,
                               total_laps: int) -> StrategyPrediction:
    """Method F: what most cars did the last time this track was raced (dry); era mode if never."""
    legal = [h for h in history if not h.wet and is_legal_dry(h.sequence) and h.track_id == track_id]
    if not legal:
        return predict_era_mode(history, regulation_era=regulation_era, total_laps=total_laps)
    last = max(h.race_date for h in legal)
    rows = [h for h in legal if h.race_date == last]
    sequence, count = Counter(h.sequence for h in rows).most_common(1)[0]
    same = [h for h in rows if h.sequence == sequence]
    stops = [round(median(h.stop_fractions[i] for h in same) * total_laps) for i in range(len(sequence) - 1)]
    strategy = build_strategy(sequence, stops, total_laps)
    return StrategyPrediction(strategy, f"last race here ({last}, {count}/{len(rows)} cars)",
                              ((strategy, count / len(rows)),) if strategy else ())


# Stop count from this weekend's practice tyre wear (research, method I). The hindsight
# ceiling showed the stop count varies race to race far more than track history explains
# (race mode 75% vs era mode 40%); practice wear is measured on the weekend itself.
WEAR_NEIGHBOURS = 7          # earlier races with the closest practice wear vote on the stop count
MIN_WEAR_RACES = 8           # fewer in this era -> all eras


def race_modes(history: Sequence[CarStrategy], min_cars: int = 5) -> dict[int, tuple[str, date, int]]:
    """race_id -> (era, date, most common stop count among legal dry strategies)."""
    by_race = defaultdict(list)
    for h in history:
        if not h.wet and is_legal_dry(h.sequence):
            by_race[h.race_id].append(h)
    return {r: (cars[0].regulation_era, cars[0].race_date,
                Counter(_stop_count(c) for c in cars).most_common(1)[0][0])
            for r, cars in by_race.items() if len(cars) >= min_cars}


def stops_from_wear(modes: dict[int, tuple[str, date, int]], wear_by_race: dict[int, float | None], *,
                    wear: float, regulation_era: str, k: int = WEAR_NEIGHBOURS) -> int | None:
    """Majority stop count of the k earlier races whose practice wear was closest."""
    known = [(abs(wear_by_race[r] - wear), stops, era) for r, (era, _, stops) in modes.items()
             if wear_by_race.get(r) is not None]
    same_era = [x for x in known if x[2] == regulation_era]
    pool = same_era if len(same_era) >= MIN_WEAR_RACES else known
    if len(pool) < k:
        return None
    votes = Counter(stops for _, stops, _ in sorted(pool)[:k])
    return max(votes, key=lambda n: (votes[n], -n))


def predict_from_wear(history: Sequence[CarStrategy], *, wear: float | None, wear_by_race: dict[int, float | None],
                      regulation_era: str, total_laps: int) -> StrategyPrediction:
    """Method I: stop count from practice wear, then the era's most common sequence with that
    many stops and its median stop laps. Without wear or history: era mode (B)."""
    base = predict_era_mode(history, regulation_era=regulation_era, total_laps=total_laps, top_k=1)
    if wear is None:
        return base
    stops = stops_from_wear(race_modes(history), wear_by_race, wear=wear, regulation_era=regulation_era)
    pool = [h for h in history if h.regulation_era == regulation_era and not h.wet and is_legal_dry(h.sequence)
            and _stop_count(h) == stops]
    if stops is None or not pool:
        return base
    sequence = Counter(h.sequence for h in pool).most_common(1)[0][0]
    same = [h for h in pool if h.sequence == sequence]
    strategy = build_strategy(sequence, [round(median(h.stop_fractions[i] for h in same) * total_laps)
                                         for i in range(stops)], total_laps)
    if strategy is None:
        return base
    others = tuple((s, w) for s, w in predict_era_mode(history, regulation_era=regulation_era,
                                                       total_laps=total_laps).alternatives
                   if s.sequence != sequence)
    return StrategyPrediction(strategy, f"practice wear {wear * 100:+.3f}%/lap -> {stops} stop(s), "
                                        f"{base.source}", ((strategy, len(same) / len(pool)), *others))


def predict_from_wear_timed(history: Sequence[CarStrategy], *, wear: float | None, wear_by_race: dict[int, float | None],
                            regulation_era: str, total_laps: int, k: int = WEAR_NEIGHBOURS) -> StrategyPrediction:
    """Method M: method I's stop count and sequence; stop laps = median stop fractions of cars
    with that many stops in the k earlier races (same era) whose practice wear was closest.
    High wear -> earlier stops. Fewer than MIN_SEQUENCE_EXAMPLES such cars: I's timing."""
    base = predict_from_wear(history, wear=wear, wear_by_race=wear_by_race, regulation_era=regulation_era,
                             total_laps=total_laps)
    if wear is None or base.strategy is None:
        return base
    stops = len(base.strategy.sequence) - 1
    if stops == 0:
        return base
    modes = race_modes(history)
    near = sorted((abs(wear_by_race[r] - wear), r) for r, (era, _, _) in modes.items()
                  if era == regulation_era and wear_by_race.get(r) is not None)[:k]
    races = {r for _, r in near}
    cars = [h for h in history if h.race_id in races and not h.wet and is_legal_dry(h.sequence)
            and _stop_count(h) == stops]
    if len(cars) < MIN_SEQUENCE_EXAMPLES:
        return base
    strategy = build_strategy(base.strategy.sequence,
                              [round(median(h.stop_fractions[i] for h in cars) * total_laps) for i in range(stops)],
                              total_laps)
    if strategy is None:
        return base
    return StrategyPrediction(strategy, f"{base.source}; stop laps from {len(cars)} cars in the {len(races)} "
                                        f"races with the closest practice wear",
                              ((strategy, base.alternatives[0][1]), *base.alternatives[1:]))


# Stop count from Pirelli's compound choice (research, method L). The same C-number set at
# earlier races (e.g. C1/C2/C3: the hardest set, brought to tyre-punishing tracks) votes on the
# stop count. C-numbers are only comparable within one numbering (pirelli_compounds_v1:
# renumbered in 2023), so 2019-22 and 2023+ never mix.
MIN_ALLOCATION_RACES = 3


def allocation_key(season_year: int, numbers: dict[str, int]) -> tuple:
    return ("2019-22" if season_year < 2023 else "2023+", numbers["HARD"], numbers["MEDIUM"], numbers["SOFT"])


def load_allocations(db: Any) -> dict[int, tuple]:
    """race_id -> allocation key for races with a valid HARD/MEDIUM/SOFT nomination."""
    from pirelli_compounds_v1 import c_number, validate_nominations

    rows = defaultdict(list)
    seasons = {}
    for race_id, season, label, c in db.execute(text("""
        SELECT n.race_id, r.season_year, n.label, n.c_compound
        FROM race_compound_nominations n JOIN races r ON r.id = n.race_id""")):
        rows[int(race_id)].append((str(label).upper(), str(c).upper()))
        seasons[int(race_id)] = int(season)
    out = {}
    for race_id, noms in rows.items():
        if not validate_nominations(seasons[race_id], noms):
            out[race_id] = allocation_key(seasons[race_id], {label: c_number(c) for label, c in noms})
    return out


def predict_from_allocation(history: Sequence[CarStrategy], *, allocation: tuple | None,
                            allocations: dict[int, tuple], wear: float | None, wear_by_race: dict,
                            regulation_era: str, total_laps: int, k: int = WEAR_NEIGHBOURS) -> StrategyPrediction:
    """Method L: stop count voted by earlier races with the same compound set (the k closest
    in practice wear when there are more than k); then the era's most common sequence with
    that many stops. Too few same-set races: method I."""
    fallback = predict_from_wear(history, wear=wear, wear_by_race=wear_by_race, regulation_era=regulation_era,
                                 total_laps=total_laps)
    if allocation is None:
        return fallback
    modes = race_modes(history)
    same = [(r, stops) for r, (_, _, stops) in modes.items() if allocations.get(r) == allocation]
    if len(same) < MIN_ALLOCATION_RACES:
        return fallback
    if wear is not None and len(same) > k:
        with_wear = [(abs(wear_by_race[r] - wear), stops) for r, stops in same if wear_by_race.get(r) is not None]
        if len(with_wear) >= k:
            same = [(None, stops) for _, stops in sorted(with_wear)[:k]]
    votes = Counter(stops for _, stops in same)
    stops = max(votes, key=lambda n: (votes[n], -n))
    pool = [h for h in history if h.regulation_era == regulation_era and not h.wet and is_legal_dry(h.sequence)
            and _stop_count(h) == stops]
    if not pool:
        return fallback
    sequence = Counter(h.sequence for h in pool).most_common(1)[0][0]
    rows = [h for h in pool if h.sequence == sequence]
    strategy = build_strategy(sequence, [round(median(h.stop_fractions[i] for h in rows) * total_laps)
                                         for i in range(stops)], total_laps)
    if strategy is None:
        return fallback
    label = f"C{allocation[1]}/C{allocation[2]}/C{allocation[3]}"
    return StrategyPrediction(strategy, f"compounds {label}: {votes[stops]}/{sum(votes.values())} similar races "
                                        f"-> {stops} stop(s)", ((strategy, len(rows) / len(pool)),))


PRACTICE_SHARE_FLOOR = 0.02   # a compound nobody long-ran still gets a small chance


def pick_sequence_from_practice(candidates: Sequence[tuple[tuple[str, ...], float]],
                                shares: dict[str, float]) -> tuple[str, ...]:
    """Among (sequence, era share) candidates, the one whose compounds teams long-ran most
    this weekend: score = log(era share) + mean over its stints of log(practice share)."""
    import math

    def score(item):
        seq, era_share = item
        fit = sum(math.log(max(shares.get(c, 0.0), PRACTICE_SHARE_FLOOR)) for c in seq) / len(seq)
        return (math.log(era_share) + fit, era_share)
    return max(candidates, key=score)[0]


def predict_from_practice(history: Sequence[CarStrategy], *, wear: float | None, wear_by_race: dict,
                          shares: dict[str, float], regulation_era: str, total_laps: int,
                          use_wear: bool = True) -> StrategyPrediction:
    """Method J (use_wear) / K: stop count from practice wear (J) or the era's most common (K);
    then, among the era's 4 most common sequences with that many stops, the one that fits the
    compounds teams long-ran in practice this weekend. Falls back to I / era mode."""
    base = (predict_from_wear(history, wear=wear, wear_by_race=wear_by_race, regulation_era=regulation_era,
                              total_laps=total_laps) if use_wear else
            predict_era_mode(history, regulation_era=regulation_era, total_laps=total_laps))
    if base.strategy is None or not shares:
        return base
    stops = len(base.strategy.sequence) - 1
    pool = [h for h in history if h.regulation_era == regulation_era and not h.wet and is_legal_dry(h.sequence)
            and _stop_count(h) == stops]
    if not pool:
        return base
    counts = Counter(h.sequence for h in pool)
    candidates = [(seq, n / len(pool)) for seq, n in counts.most_common(4)]
    sequence = pick_sequence_from_practice(candidates, shares)
    same = [h for h in pool if h.sequence == sequence]
    strategy = build_strategy(sequence, [round(median(h.stop_fractions[i] for h in same) * total_laps)
                                         for i in range(stops)], total_laps)
    if strategy is None:
        return base
    mix = ", ".join(f"{c[0]} {v:.0%}" for c, v in sorted(shares.items(), key=lambda kv: -kv[1]))
    return StrategyPrediction(strategy, f"{base.source}; practice long runs {mix}",
                              ((strategy, counts[sequence] / len(pool)),
                               *((s, w) for s, w in base.alternatives if s.sequence != sequence)))


# --- database ------------------------------------------------------------------

def load_car_strategies(db: Any, *, before_date: date | None = None, start_year: int = 2018,
                        end_year: int = 2026, min_distance: float = 0.9) -> list[CarStrategy]:
    """Real stints for every car that covered >= min_distance of a race (R sessions)."""
    from race_pace_from_quali_v1 import SESSION_WET_SQL

    races = db.execute(text(f"""
        SELECT r.id, r.race_date, r.season_year, r.track_id, r.regulation_era, s.id AS session_id,
               {SESSION_WET_SQL} AS wet
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
