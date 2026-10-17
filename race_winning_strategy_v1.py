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

Shown with it (display only -- none of this changes the predicted plan):
  alternative  : the plan for the next most common stop count, with its windows and share
  confidence   : high / medium / low by rule (confidence_level); its walk-forward record is
                 measured by winning_strategy_backtest_v1 and shown only once measured
  track record : the last TRACK_RECORD_RACES races here -- the winner's real plan and stop laps
                 (flagged when a stop was under SC/VSC) and the front grid's stop counts
  review       : after the race, the front grid's plans and stops against the prediction

    python race_winning_strategy_v1.py --race-id 190
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
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
class AlternativePlan:
    sequence: tuple[str, ...]
    windows: tuple[PitWindow, ...]
    share: float                            # weighted front-grid share of its stop count

    @property
    def stops(self) -> int:
        return len(self.sequence) - 1


@dataclass(frozen=True)
class WinningStrategy:
    sequence: tuple[str, ...]
    windows: tuple[PitWindow, ...]
    stop_chances: dict[int, float]          # stop count -> weighted front-grid share
    track_cars: float                       # weighted front-grid cars at this track behind it
    track_races: int
    window_source: str                      # track | era
    source: str
    alternative: AlternativePlan | None = None
    confidence: str = "low"                 # high | medium | low (confidence_level)

    @property
    def stops(self) -> int:
        return len(self.sequence) - 1

    @property
    def share(self) -> float:
        return self.stop_chances.get(self.stops, 0.0)


# Confidence rule. The levels are a RULE, not a measured probability: the backtest
# (winning_strategy_backtest_v1) reports how often each level was right walk-forward, and
# that measured record is what may be shown next to the label.
HIGH_SHARE, MEDIUM_SHARE = 0.75, 0.55
HIGH_MIN_TRACK_RACES = 2


def confidence_level(share: float, track_races: int) -> str:
    if share >= HIGH_SHARE and track_races >= HIGH_MIN_TRACK_RACES:
        return "high"
    if share >= MEDIUM_SHARE:
        return "medium"
    return "low"


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


def cap_window(first: int, last: int, typical: int, max_width: int | None) -> tuple[int, int]:
    """Narrow a window wider than max_width laps to max_width laps around the typical lap,
    staying inside the original window."""
    if max_width is None or last - first + 1 <= max_width:
        return first, last
    start = min(max(first, typical - (max_width - 1) // 2), last - max_width + 1)
    return start, start + max_width - 1


def _windows(rows: Sequence[tuple[CarStrategy, float]], stops: int, total_laps: int,
             max_width: int | None = None) -> tuple[PitWindow, ...]:
    windows = []
    for i in range(stops):
        pairs = [(c.stop_fractions[i], w) for c, w in rows]
        lo, mid, hi = (_weighted_quantile(pairs, q) for q in (WINDOW_LOW, 0.5, WINDOW_HIGH))
        first, typical, last = (max(1, min(total_laps - 1, round(f * total_laps))) for f in (lo, mid, hi))
        first, last = cap_window(first, last, typical, max_width)
        windows.append(PitWindow(i + 1, first, last, typical))
    return tuple(windows)


def predict_winning_strategy(history: Sequence[CarStrategy], *, track_id: int, regulation_era: str,
                             total_laps: int, k: float = TRACK_SHRINK, stops: int | None = None,
                             max_window: int | None = None) -> WinningStrategy | None:
    """history: clean front-grid cars from strictly earlier races (clean_front_cars).
    stops: use this stop count instead of the front grid's most common one (the hybrid);
    None when no front-grid car in the history ran it. max_window: cap on window width (laps)."""
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
    def plan_for(stops: int):
        """(sequence, windows, window source) for the plan with this many stops."""
        track_same = [(c, w) for c, w in track if stop_count(c) == stops]
        era_same = [c for c in era_cars if stop_count(c) == stops]
        if not track_same and not era_same:
            return None
        sequence_chances = blended(lambda c: c.sequence, track_same, era_same) if era_same else \
            {k: v for k, v in _shares(track_same, lambda c: c.sequence).items()}
        sequence = max(sequence_chances, key=lambda s: (sequence_chances[s], s))
        if sum(w for _, w in track_same) >= MIN_WINDOW_CARS or not era_same:
            window_rows, window_source = track_same, "track"
        else:
            window_rows, window_source = [(c, 1.0) for c in era_same], "era"
        return sequence, (_windows(window_rows, stops, total_laps, max_window) if stops else ()), window_source

    ranked = sorted(stop_chances, key=lambda n: (-stop_chances[n], n))
    if stops is not None:
        if stops not in stop_chances:
            return None
        ranked = [stops, *(n for n in ranked if n != stops)]
    stops = ranked[0]
    sequence, windows, window_source = plan_for(stops)
    alternative = None
    if len(ranked) > 1 and (alt := plan_for(ranked[1])) is not None:
        alternative = AlternativePlan(alt[0], alt[1], stop_chances[ranked[1]])
    races = len({c.race_id for c, _ in track})
    source = (f"{track_total:g} front-grid cars in {races} earlier race(s) here" if races
              else "no earlier race here: the era's front-grid plans")
    return WinningStrategy(sequence, windows, dict(sorted(stop_chances.items())), track_total, races,
                           window_source, source, alternative,
                           confidence_level(stop_chances[stops], races))


MAX_WINDOW = 10            # hybrid: no pit window wider than this many laps
FALLBACK_HALF_WIDTH = 3    # hybrid without front-grid history: production's stop lap +- 3


def predict_hybrid(front_history: Sequence[CarStrategy], all_history: Sequence[CarStrategy], *,
                   wear: float | None, wear_by_race: dict[int, float | None], track_id: int,
                   regulation_era: str, total_laps: int) -> WinningStrategy | None:
    """The hybrid: stop count from production (method M -- this weekend's practice tyre wear,
    else the era's usual count), tyre order and pit windows from front-grid cars with that
    stop count at this track (era fallback), windows capped at MAX_WINDOW laps. When no
    front-grid car ever ran that stop count: production's plan with +-FALLBACK_HALF_WIDTH
    windows. front_history: clean_front_cars(...); all_history: every earlier car (for M)."""
    from race_strategy_timing_v1 import predict_from_wear_timed

    m = predict_from_wear_timed(all_history, wear=wear, wear_by_race=wear_by_race, regulation_era=regulation_era,
                                total_laps=total_laps)
    if m.strategy is None:
        return predict_winning_strategy(front_history, track_id=track_id, regulation_era=regulation_era,
                                        total_laps=total_laps, max_window=MAX_WINDOW)
    stops = len(m.strategy.sequence) - 1
    why = (f"stop count from practice tyre wear ({wear * 100:+.3f}%/lap)" if wear is not None
           else "stop count: the era's usual (no practice tyre wear measured)")
    p = predict_winning_strategy(front_history, track_id=track_id, regulation_era=regulation_era,
                                 total_laps=total_laps, stops=stops, max_window=MAX_WINDOW)
    if p is not None:
        return replace(p, source=f"{why}; tyre order and windows from {p.source}")
    windows = tuple(PitWindow(i + 1, max(1, lap - FALLBACK_HALF_WIDTH), min(total_laps - 1, lap + FALLBACK_HALF_WIDTH), lap)
                    for i, lap in enumerate(m.strategy.stop_laps))
    return WinningStrategy(m.strategy.sequence, windows, {}, 0.0, 0, "production",
                           f"{why}; no front-grid car ran {stops} stop(s): production's plan", None, "low")


# --- pre-start stage: the starting tyre is declared ------------------------------------------
# Teams fit the starting tyres before the formation lap. Knowing a front-runner's starting
# compound narrows the plan: history is cut to front-grid cars that STARTED on it. The race
# strategy backtest found this the one clear strategy gain (method D: tyre order 0.255 -> 0.287
# in 2022-25). Two stop-count rules are compared in winning_strategy_backtest_v1:
#   "hybrid"  keep the practice-wear stop count (predict_hybrid) and pick that count's plan among
#             cars on this starting tyre (if none ran it: their most common count)
#   "start"   the stop count of the front-grid cars that started on this tyre

def predict_pre_start(front_history: Sequence[CarStrategy], all_history: Sequence[CarStrategy], *,
                      start_compound: str, wear: float | None, wear_by_race: dict[int, float | None],
                      track_id: int, regulation_era: str, total_laps: int,
                      stop_rule: str = "hybrid") -> WinningStrategy | None:
    """The plan for front-grid cars that start on start_compound; predict_hybrid when no
    front-grid car in the history started on it."""
    kwargs = dict(track_id=track_id, regulation_era=regulation_era, total_laps=total_laps)
    hybrid = predict_hybrid(front_history, all_history, wear=wear, wear_by_race=wear_by_race, **kwargs)
    same_start = [c for c in front_history if c.sequence and c.sequence[0] == start_compound.upper()]
    if not same_start:
        return hybrid
    p = None
    if stop_rule == "hybrid" and hybrid is not None:
        p = predict_winning_strategy(same_start, stops=hybrid.stops, max_window=MAX_WINDOW, **kwargs)
    if p is None:
        p = predict_winning_strategy(same_start, max_window=MAX_WINDOW, **kwargs)
    if p is None:
        return hybrid
    return replace(p, source=f"starting on {start_compound.upper()}: {p.source}")


def load_neutralised_laps(db: Any, race_ids: Iterable[int]) -> dict[int, frozenset[int] | None]:
    """race_id -> SC/VSC/red-flag leader laps; None when the windows are unknown."""
    from race_neutralisations_v1 import load_race_neutralisations

    out = {}
    for race_id in race_ids:
        n = load_race_neutralisations(db, race_id)
        out[race_id] = n.neutralised_laps(NEUTRALISATIONS) if n.coverage == "ok" else None
    return out


# --- the track's record: what won here before --------------------------------------------

TRACK_RECORD_RACES = 3


@dataclass(frozen=True)
class TrackRaceRecord:
    race_id: int
    season_year: int
    winner_grid: int | None
    winner_sequence: tuple[str, ...] | None
    winner_stop_laps: tuple[int, ...]
    winner_neutralised_stop: bool          # the winner made a stop under SC/VSC/red flag
    front_stop_counts: dict[int, int]      # stop count -> front-grid cars (all, SC or not)


def track_record(history: Sequence[CarStrategy], neutralised_laps: dict[int, frozenset[int] | None], *,
                 track_id: int, n: int = TRACK_RECORD_RACES) -> list[TrackRaceRecord]:
    """The last n dry races at this track (most recent first), from ALL cars of those races:
    the winner's real plan and the front grid's stop counts. Facts, no filtering."""
    by_race: dict[int, list[CarStrategy]] = defaultdict(list)
    for c in history:
        if c.track_id == track_id and not c.wet:
            by_race[c.race_id].append(c)
    out = []
    for race_id, cars in sorted(by_race.items(), key=lambda kv: kv[1][0].race_date, reverse=True)[:n]:
        winner = next((c for c in cars if c.finish == 1), None)
        laps = neutralised_laps.get(race_id) or frozenset()
        stops = defaultdict(int)
        for c in cars:
            if is_front(c):
                stops[len(c.stints) - 1] += 1
        out.append(TrackRaceRecord(
            race_id, cars[0].season_year, winner.grid if winner else None,
            winner.sequence if winner else None, tuple(stop_in_laps(winner)) if winner else (),
            bool(winner and any(lap in laps for lap in stop_in_laps(winner))), dict(sorted(stops.items()))))
    return out


# --- after the race: did it play out? ----------------------------------------------------


@dataclass(frozen=True)
class CarReview:
    grid: int
    finish: int | None
    sequence: tuple[str, ...]
    stop_laps: tuple[int, ...]
    plan_match: bool                       # ran the predicted tyre order
    in_window: tuple[bool | None, ...]     # per stop: inside its window; None = SC stop or no window


@dataclass(frozen=True)
class RaceReview:
    cars: tuple[CarReview, ...]            # front grid, by grid slot
    winner: CarReview | None               # the race winner, wherever they started

    @property
    def plan_matches(self) -> int:
        return sum(c.plan_match for c in self.cars)

    @property
    def window_hits(self) -> tuple[int, int]:
        """(stops inside their window, green stops that had a window) over the front grid."""
        flags = [f for c in self.cars for f in c.in_window if f is not None]
        return sum(flags), len(flags)


def review_race(prediction: WinningStrategy, race_cars: Sequence[CarStrategy],
                neutralised: frozenset[int] | None) -> RaceReview:
    laps = neutralised or frozenset()

    def review(c: CarStrategy) -> CarReview:
        flags = []
        for i, lap in enumerate(stop_in_laps(c)):
            if lap in laps or i >= len(prediction.windows):
                flags.append(None)
            else:
                w = prediction.windows[i]
                flags.append(w.first_lap <= lap <= w.last_lap)
        return CarReview(c.grid, c.finish, c.sequence, tuple(stop_in_laps(c)), c.sequence == prediction.sequence,
                         tuple(flags))

    front = sorted((c for c in race_cars if is_front(c)), key=lambda c: c.grid)
    winner = next((c for c in race_cars if c.finish == 1), None)
    return RaceReview(tuple(review(c) for c in front), review(winner) if winner else None)


# --- text ---------------------------------------------------------------------------------

STOP_NAMES = ("First", "Second", "Third", "Fourth")


def _plan_text(sequence: Sequence[str]) -> str:
    stops = len(sequence) - 1
    return f"{' -> '.join(sequence)} ({stops} stop{'s' if stops != 1 else ''})"


def _windows_text(windows: Sequence[PitWindow], indent: str = "  ") -> list[str]:
    return [f"{indent}{STOP_NAMES[w.stop - 1] if w.stop <= 4 else f'Stop {w.stop}'} pit window: "
            f"laps {w.first_lap}-{w.last_lap} (typical lap {w.typical_lap})" for w in windows]


def format_strategy(p: WinningStrategy, record: Sequence[TrackRaceRecord] = (),
                    confidence_record: dict[str, str] | None = None) -> str:
    """confidence_record: level -> measured walk-forward record text (shown only when given)."""
    lines = [f"Predicted winning strategy (front of the grid): {_plan_text(p.sequence)}"]
    lines += _windows_text(p.windows)
    measured = f" -- {confidence_record[p.confidence]}" if confidence_record and p.confidence in confidence_record else ""
    lines.append(f"  Confidence: {p.confidence}{measured}")
    if p.stop_chances:
        lines.append(f"  Front-grid share: {p.share:.0%} of comparable front-grid cars ran "
                     f"{p.stops} stop{'s' if p.stops != 1 else ''}")
    if p.alternative is not None:
        lines.append(f"  Alternative: {_plan_text(p.alternative.sequence)}, share {p.alternative.share:.0%}")
        lines += _windows_text(p.alternative.windows, indent="    ")
    if record:
        lines.append("  Track record (last races here):")
        for r in record:
            if r.winner_sequence is None:
                won = "winner's plan not recorded"
            else:
                sc = " (a stop under SC/VSC)" if r.winner_neutralised_stop else ""
                laps = ", ".join(str(x) for x in r.winner_stop_laps) or "no stop"
                won = f"winner from P{r.winner_grid}: {_plan_text(r.winner_sequence)}, laps {laps}{sc}"
            front = ", ".join(f"{n} stop{'s' if n != 1 else ''} x{k}" for n, k in r.front_stop_counts.items())
            lines.append(f"    {r.season_year}: {won}; front grid {front}")
    lines.append(f"  Based on: {p.source}; windows from the {p.window_source}")
    lines.append("  Green-flag plan: a Safety Car or VSC can open an earlier, cheaper stop.")
    return "\n".join(lines)


def format_review(p: WinningStrategy, review: RaceReview) -> str:
    hits, chances = review.window_hits
    lines = [f"Did it play out? Predicted {_plan_text(p.sequence)}",
             f"  Front grid on the predicted tyre order: {review.plan_matches} of {len(review.cars)}",
             f"  Green-flag stops inside their window: {hits} of {chances}"]

    def car_line(c: CarReview, label: str) -> str:
        flags = " ".join("in" if f else "SC/none" if f is None else "out" for f in c.in_window) or "-"
        laps = ", ".join(str(x) for x in c.stop_laps) or "no stop"
        finish = f"P{c.finish}" if c.finish else "DNF/unclassified"
        return (f"    {label}: {' -> '.join(c.sequence)}, stops {laps} [{flags}] "
                f"{'MATCH' if c.plan_match else 'different plan'}, finished {finish}")
    for c in review.cars:
        lines.append(car_line(c, f"P{c.grid}"))
    if review.winner is not None:
        lines.append(car_line(review.winner, f"Winner (from P{review.winner.grid})"))
    return "\n".join(lines)


# --- JSON (API and live record) -----------------------------------------------------------


def _windows_json(windows: Sequence[PitWindow]) -> list[dict]:
    return [{"stop": w.stop, "first_lap": w.first_lap, "last_lap": w.last_lap, "typical_lap": w.typical_lap}
            for w in windows]


def _windows_from_json(rows: Sequence[dict]) -> tuple[PitWindow, ...]:
    return tuple(PitWindow(r["stop"], r["first_lap"], r["last_lap"], r["typical_lap"]) for r in rows)


def strategy_to_json(p: WinningStrategy) -> dict:
    return {
        "sequence": list(p.sequence), "stops": p.stops, "pit_windows": _windows_json(p.windows),
        "confidence": p.confidence, "front_grid_share": round(p.share, 4) if p.stop_chances else None,
        "stop_chances": {str(n): round(v, 4) for n, v in p.stop_chances.items()},
        "alternative": None if p.alternative is None else {
            "sequence": list(p.alternative.sequence), "stops": p.alternative.stops,
            "pit_windows": _windows_json(p.alternative.windows), "front_grid_share": round(p.alternative.share, 4)},
        "track_cars": p.track_cars, "track_races": p.track_races, "window_source": p.window_source,
        "source": p.source,
        "note": "Green-flag plan for the front of the grid (P1-P5): a Safety Car or VSC can open an "
                "earlier, cheaper stop.",
    }


def strategy_from_json(d: dict) -> WinningStrategy:
    alt = d.get("alternative")
    return WinningStrategy(
        tuple(d["sequence"]), _windows_from_json(d["pit_windows"]),
        {int(n): v for n, v in d.get("stop_chances", {}).items()}, d.get("track_cars", 0.0),
        d.get("track_races", 0), d.get("window_source", ""), d.get("source", ""),
        None if alt is None else AlternativePlan(tuple(alt["sequence"]), _windows_from_json(alt["pit_windows"]),
                                                 alt["front_grid_share"]),
        d.get("confidence", "low"))


def record_to_json(record: Sequence[TrackRaceRecord]) -> list[dict]:
    return [{"race_id": r.race_id, "season": r.season_year, "winner_grid": r.winner_grid,
             "winner_sequence": list(r.winner_sequence) if r.winner_sequence else None,
             "winner_stop_laps": list(r.winner_stop_laps), "winner_stopped_under_sc": r.winner_neutralised_stop,
             "front_grid_stop_counts": {str(n): k for n, k in r.front_stop_counts.items()}} for r in record]


def review_to_json(review: RaceReview) -> dict:
    def car(c: CarReview) -> dict:
        return {"grid": c.grid, "finish": c.finish, "sequence": list(c.sequence), "stop_laps": list(c.stop_laps),
                "plan_match": c.plan_match, "in_window": list(c.in_window)}
    hits, chances = review.window_hits
    return {"front_grid_plan_matches": review.plan_matches, "front_grid_cars": len(review.cars),
            "green_stops_in_window": hits, "green_stops_with_window": chances,
            "cars": [car(c) for c in review.cars], "winner": car(review.winner) if review.winner else None}


# --- command line -------------------------------------------------------------------------


def load_race_context(db: Any, race_id: int) -> dict | None:
    from sqlalchemy import text

    return db.execute(text("""
        SELECT r.id, r.race_date, r.season_year, r.track_id, r.regulation_era, t.name AS track_name,
               t.total_race_laps
        FROM races r JOIN tracks t ON t.id = r.track_id WHERE r.id = :r
    """), {"r": race_id}).mappings().first()


def _race_inputs(db: Any, race_id: int) -> dict:
    """Everything a prediction for this race needs, from races strictly before it."""
    from dataclasses import replace

    from race_strategy_timing_v1 import load_car_strategies, race_wet_flags

    race = load_race_context(db, race_id)
    if race is None:
        raise ValueError(f"race {race_id} not found")
    # facts (track record, review) call a race wet by its tyres; the prediction's history keeps
    # the rain-flag rule (the tyre rule was tested for prediction and rejected, see RACE_WET_SQL)
    fact_cars = load_car_strategies(db, start_year=2018, end_year=race["season_year"], wet_rule="tyres")
    flags = race_wet_flags(db, "rain_flag")
    cars = [replace(c, wet=flags.get(c.race_id, c.wet)) for c in fact_cars]
    history = [c for c in cars if c.race_date < race["race_date"]]
    race_cars = [c for c in cars if c.race_id == race_id]
    total_laps = (race_cars[0].race_laps if race_cars else race["total_race_laps"]
                  or max((c.race_laps for c in history if c.track_id == race["track_id"]), default=None))
    out = {"race": dict(race), "fact_cars": fact_cars, "history": history, "race_cars": race_cars,
           "total_laps": int(total_laps) if total_laps else None}
    if not total_laps or not race["regulation_era"]:
        return out
    out["neutralised"] = load_neutralised_laps(db, {c.race_id for c in cars if c.track_id == race["track_id"]
                                                    or c.race_date < race["race_date"]})
    out["front"] = clean_front_cars(history, out["neutralised"])
    out["record"] = track_record([c for c in fact_cars if c.race_date < race["race_date"]], out["neutralised"],
                                 track_id=race["track_id"])
    return out


def _wear_by_race(db: Any, history: Sequence[CarStrategy], race_id: int) -> dict[int, float | None]:
    from race_practice_pace_v1 import load_weekend_wear

    return {r: load_weekend_wear(db, r) for r in {c.race_id for c in history} | {race_id}}


def predict_for_race(db: Any, race_id: int, method: str = "track"
                     ) -> tuple[WinningStrategy | None, list[TrackRaceRecord], RaceReview | None, dict]:
    """Prediction from races strictly before this one, the track record and, when the race has
    been run, its review. method: "track" (front-grid history) or "hybrid" (predict_hybrid)."""
    inputs = _race_inputs(db, race_id)
    race = {**inputs["race"], "total_laps": inputs["total_laps"]}
    if "front" not in inputs:
        return None, [], None, race
    front, history = inputs["front"], inputs["history"]
    kwargs = dict(track_id=race["track_id"], regulation_era=race["regulation_era"], total_laps=inputs["total_laps"])
    if method == "hybrid":
        wear_by_race = _wear_by_race(db, history, race_id)
        prediction = predict_hybrid(front, history, wear=wear_by_race.get(race_id), wear_by_race=wear_by_race,
                                    **kwargs)
        race = {**race, "practice_wear": wear_by_race.get(race_id)}
    else:
        prediction = predict_winning_strategy(front, **kwargs)
    review = None
    fact_race = [c for c in inputs["fact_cars"] if c.race_id == race_id]
    if prediction is not None and fact_race and not fact_race[0].wet:
        review = review_race(prediction, inputs["race_cars"], inputs["neutralised"].get(race_id))
    return prediction, inputs["record"], review, race


# --- pre-start stage for one race (starting tyres stored by start_tyres_v1) --------------------

def front_grid_order(db: Any, race_id: int) -> dict[int, int]:
    """race_entry_id -> grid slot for the front grid: the provisional grid (penalties applied)
    when stored, else the qualifying order."""
    from sqlalchemy import text

    rows = db.execute(text("""
        SELECT race_entry_id, grid_position FROM race_provisional_grid WHERE race_id = :r"""), {"r": race_id}).all()
    if not rows:
        rows = db.execute(text("""
            SELECT q.race_entry_id, q.final_position FROM qualifying_results q
            JOIN sessions s ON s.id = q.session_id AND s.session_type = 'Q'
            WHERE s.race_id = :r AND q.final_position IS NOT NULL"""), {"r": race_id}).all()
    return {int(e): int(g) for e, g in rows if g is not None and 1 <= int(g) <= FRONT_GRID}


def predict_pre_start_for_race(db: Any, race_id: int) -> dict | None:
    """Per front-grid car: its declared starting tyre and the plan for it (predict_pre_start,
    PS1 rule). None when no front-grid car has a stored starting tyre."""
    from sqlalchemy import text

    from start_tyres_v1 import load_start_tyres

    starts = load_start_tyres(db, race_id)
    grid = front_grid_order(db, race_id)
    declared = {e: starts[e] for e in grid if e in starts}
    if not declared:
        return None
    inputs = _race_inputs(db, race_id)
    race = {**inputs["race"], "total_laps": inputs["total_laps"]}
    if "front" not in inputs:
        return None
    history = inputs["history"]
    wear_by_race = _wear_by_race(db, history, race_id)
    plans = {c: predict_pre_start(inputs["front"], history, start_compound=c, wear=wear_by_race.get(race_id),
                                  wear_by_race=wear_by_race, track_id=race["track_id"],
                                  regulation_era=race["regulation_era"], total_laps=inputs["total_laps"])
             for c in sorted(set(declared.values()))}
    names = dict(db.execute(text("""
        SELECT re.id, d.name FROM race_entries re JOIN drivers d ON d.id = re.driver_id
        WHERE re.race_id = :r"""), {"r": race_id}).all())
    cars = [{"grid": grid[e], "race_entry_id": e, "driver": names.get(e), "start": declared[e]}
            for e in sorted(declared, key=grid.get)]
    return {"race": {**race, "practice_wear": wear_by_race.get(race_id)}, "cars": cars, "plans": plans,
            "record": inputs["record"]}


def pre_start_to_json(result: dict) -> dict:
    return {"cars": result["cars"],
            "plans": {c: strategy_to_json(p) for c, p in result["plans"].items() if p is not None}}


def format_pre_start(result: dict) -> str:
    lines = ["Pre-start winning strategy (starting tyres declared):"]
    for car in result["cars"]:
        lines.append(f"  P{car['grid']} {car['driver'] or car['race_entry_id']}: starts on {car['start']}")
    for compound, plan in result["plans"].items():
        if plan is None:
            continue
        lines.append(f"\nFront-runners starting on {compound}:")
        lines += ["  " + line for line in format_strategy(plan).splitlines()]
    return "\n".join(lines)


def main() -> int:
    import argparse
    import os

    from dotenv import load_dotenv
    from sqlalchemy import create_engine

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--race-id", type=int, required=True)
    parser.add_argument("--method", choices=("track", "hybrid", "pre_start"), default="track")
    args = parser.parse_args()
    if args.method == "pre_start":
        with create_engine(os.environ["DATABASE_URL"]).connect() as db:
            result = predict_pre_start_for_race(db, args.race_id)
        if result is None:
            print("No starting tyres stored for the front grid yet: run start_tyres_v1.py --race-id first.")
            return 1
        print(format_pre_start(result))
        return 0
    with create_engine(os.environ["DATABASE_URL"]).connect() as db:
        prediction, record, review, race = predict_for_race(db, args.race_id, args.method)
    print(f"{race['track_name']} {race['season_year']} (race {args.race_id}, {race['race_date']})")
    if prediction is None:
        print("No prediction: race distance or regulation era unknown, or no history.")
        return 1
    print(format_strategy(prediction, record))
    if review is not None:
        print()
        print(format_review(prediction, review))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
