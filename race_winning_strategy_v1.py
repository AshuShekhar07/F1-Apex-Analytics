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
        return sequence, (_windows(window_rows, stops, total_laps) if stops else ()), window_source

    ranked = sorted(stop_chances, key=lambda n: (-stop_chances[n], n))
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


# --- command line -------------------------------------------------------------------------


def load_race_context(db: Any, race_id: int) -> dict | None:
    from sqlalchemy import text

    return db.execute(text("""
        SELECT r.id, r.race_date, r.season_year, r.track_id, r.regulation_era, t.name AS track_name,
               t.total_race_laps
        FROM races r JOIN tracks t ON t.id = r.track_id WHERE r.id = :r
    """), {"r": race_id}).mappings().first()


def predict_for_race(db: Any, race_id: int) -> tuple[WinningStrategy | None, list[TrackRaceRecord], RaceReview | None, dict]:
    """Prediction from races strictly before this one, the track record and, when the race has
    been run, its review."""
    from race_strategy_timing_v1 import load_car_strategies

    race = load_race_context(db, race_id)
    if race is None:
        raise ValueError(f"race {race_id} not found")
    cars = load_car_strategies(db, start_year=2018, end_year=race["season_year"])
    history = [c for c in cars if c.race_date < race["race_date"]]
    race_cars = [c for c in cars if c.race_id == race_id]
    total_laps = (race_cars[0].race_laps if race_cars else race["total_race_laps"]
                  or max((c.race_laps for c in history if c.track_id == race["track_id"]), default=None))
    if not total_laps or not race["regulation_era"]:
        return None, [], None, dict(race)
    neutralised = load_neutralised_laps(db, {c.race_id for c in cars if c.track_id == race["track_id"]
                                             or c.race_date < race["race_date"]})
    prediction = predict_winning_strategy(clean_front_cars(history, neutralised), track_id=race["track_id"],
                                          regulation_era=race["regulation_era"], total_laps=int(total_laps))
    record = track_record(history, neutralised, track_id=race["track_id"])
    review = None
    if prediction is not None and race_cars and not race_cars[0].wet:
        review = review_race(prediction, race_cars, neutralised.get(race_id))
    return prediction, record, review, dict(race)


def main() -> int:
    import argparse
    import os

    from dotenv import load_dotenv
    from sqlalchemy import create_engine

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--race-id", type=int, required=True)
    args = parser.parse_args()
    with create_engine(os.environ["DATABASE_URL"]).connect() as db:
        prediction, record, review, race = predict_for_race(db, args.race_id)
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
