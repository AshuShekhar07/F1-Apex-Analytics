"""Pit loss v2: race time actually lost to a pit stop, by track, era and condition.

v1 (race_strategy_pit_calibration_v1) calibrates PitInTime -> PitOutTime, the
time spent between the pit entry and exit lines. That is not the loss: the car
would have covered that stretch of track anyway, so v1 overstates the cost, and
it treats a stop under Safety Car the same as a green-flag stop.

v2 measures, per stop, the in-lap + out-lap delta RELATIVE TO THE FIELD on
those same laps:

    gap(lap)  = driver lap time - median lap time of non-stopping cars on that lap
    loss      = gap(in_lap) + gap(out_lap) - 2 * reference_gap

reference_gap = mean of (median clean gap before the stop, median clean gap
after it), same driver and race, so old-tyre and new-tyre pace are balanced.
Reference laps never cross the driver's previous or next stop.
Clean = green/yellow-only track status, not lap 1, not an in/out lap.

Measuring against the field on the same lap is what makes Safety Car stops
come out right: under SC every car's lap is slow, so comparing with green-flag
pace (the first v2 draft) charged the SC's slowness to the stop (62-83 s on
real data). It also absorbs fuel burn and track evolution.

Each stop is classified from the in/out laps' FastF1 track status: green, sc,
vsc, red_flag (no loss: free tyre change), unknown (no status stored),
no_timing (in/out lap time or field median missing), no_reference (too few
clean laps).

Known limits (documented, not modelled): drive-through / stop-go penalties are
pit visits too and are not separated; out-lap traffic is included in the loss.
Both are reduced by using medians.

    python race_strategy_pit_loss_v2.py --start-year 2018 --end-year 2026 --csv pit_loss_v2.csv
"""

from __future__ import annotations

import argparse
import csv
import os
from dataclasses import asdict, dataclass
from datetime import date
from statistics import median
from typing import Any, Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

REFERENCE_WINDOW = 6       # laps either side of the stop searched for clean pace
MIN_REFERENCE_LAPS = 2     # required on EACH side
MIN_FIELD_CARS = 3         # non-stopping cars needed for a lap's field median
GREEN_CODES = frozenset("12")  # all clear, local yellow


@dataclass(frozen=True)
class LapRecord:
    lap_number: int
    lap_time: float | None
    track_status: str | None
    pit_in: bool
    pit_out: bool


@dataclass(frozen=True)
class PitLossObservation:
    race_id: int
    race_date: date
    track_id: int
    regulation_era: str
    race_entry_id: int
    pit_lap: int
    condition: str  # green | sc | vsc | red_flag | unknown | no_timing | no_reference
    loss_seconds: float | None
    reference_gap_seconds: float | None


@dataclass(frozen=True)
class PitLossEstimate:
    median_seconds: float
    robust_std_seconds: float
    n_stops: int
    n_races: int
    source: str  # track_era | era_fallback


def classify_condition(*statuses: str | None) -> str:
    if any(s is None or s == "" for s in statuses):
        return "unknown"
    codes = set("".join(statuses))
    if "5" in codes:
        return "red_flag"
    if "4" in codes:
        return "sc"
    if codes & {"6", "7"}:
        return "vsc"
    if codes <= GREEN_CODES:
        return "green"
    return "unknown"


def _is_clean(lap: LapRecord, stop_laps: set[int]) -> bool:
    return (
        lap.lap_number > 1
        and lap.lap_time is not None
        and not lap.pit_in and not lap.pit_out
        and lap.lap_number not in stop_laps
        and lap.track_status is not None
        and set(lap.track_status) <= GREEN_CODES
    )


def field_medians(laps_by_entry: dict[int, list[LapRecord]], stop_laps_by_entry: dict[int, set[int]]) -> dict[int, float]:
    """Median lap time per lap number over cars not pitting on that lap."""
    times: dict[int, list[float]] = {}
    for entry_id, laps in laps_by_entry.items():
        stopping = stop_laps_by_entry.get(entry_id, set())
        for lap in laps:
            if lap.lap_time is None or lap.pit_in or lap.pit_out or lap.lap_number in stopping:
                continue
            times.setdefault(lap.lap_number, []).append(lap.lap_time)
    return {n: median(v) for n, v in times.items() if len(v) >= MIN_FIELD_CARS}


def stop_laps(pit_laps: Iterable[int]) -> set[int]:
    return {p for pit in pit_laps for p in (int(pit), int(pit) + 1)}


def stop_losses(
    laps: Iterable[LapRecord],
    pit_laps: Iterable[int],
    field: dict[int, float],
) -> list[tuple[int, str, float | None, float | None]]:
    """(pit_lap, condition, loss_seconds, reference_gap_seconds) for one driver's race."""
    by_number = {lap.lap_number: lap for lap in laps}
    pit_laps = sorted(set(int(p) for p in pit_laps))
    excluded = stop_laps(pit_laps)

    def gap(n: int) -> float | None:
        lap = by_number.get(n)
        if lap is None or lap.lap_time is None or n not in field:
            return None
        return lap.lap_time - field[n]

    results = []
    for index, pit in enumerate(pit_laps):
        # reference pace comes only from the stints either side of THIS stop
        window_start = max(pit - REFERENCE_WINDOW, pit_laps[index - 1] + 2 if index > 0 else 1)
        window_end = min(pit + 2 + REFERENCE_WINDOW, pit_laps[index + 1] if index + 1 < len(pit_laps) else pit + 2 + REFERENCE_WINDOW)
        in_lap, out_lap = by_number.get(pit), by_number.get(pit + 1)
        if in_lap is None or out_lap is None:
            results.append((pit, "no_timing", None, None))
            continue
        condition = classify_condition(in_lap.track_status, out_lap.track_status)
        if condition in ("unknown", "red_flag"):
            results.append((pit, condition, None, None))
            continue
        gap_in, gap_out = gap(pit), gap(pit + 1)
        if gap_in is None or gap_out is None:
            results.append((pit, "no_timing", None, None))
            continue
        before = [g for n in range(window_start, pit)
                  if n in by_number and _is_clean(by_number[n], excluded) and (g := gap(n)) is not None]
        after = [g for n in range(pit + 2, window_end)
                 if n in by_number and _is_clean(by_number[n], excluded) and (g := gap(n)) is not None]
        if len(before) < MIN_REFERENCE_LAPS or len(after) < MIN_REFERENCE_LAPS:
            results.append((pit, "no_reference", None, None))
            continue
        reference = (median(before) + median(after)) / 2
        loss = gap_in + gap_out - 2 * reference
        results.append((pit, condition, round(loss, 3), round(reference, 3)))
    return results


def load_observations(db: Any, *, start_year: int, end_year: int) -> list[PitLossObservation]:
    races = db.execute(text("""
        SELECT r.id, r.race_date, r.track_id, r.regulation_era, s.id AS session_id
        FROM races r JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        WHERE r.season_year BETWEEN :a AND :b AND r.regulation_era IS NOT NULL
        ORDER BY r.race_date
    """), {"a": start_year, "b": end_year}).mappings().all()

    observations: list[PitLossObservation] = []
    for race in races:
        laps_by_entry: dict[int, list[LapRecord]] = {}
        pits_by_entry: dict[int, set[int]] = {}
        for row in db.execute(text("""
            SELECT race_entry_id, lap_number, lap_time, track_status_code,
                   pit_in_time_seconds IS NOT NULL AS pit_in, pit_out_time_seconds IS NOT NULL AS pit_out
            FROM laps WHERE session_id = :s
        """), {"s": race["session_id"]}):
            laps_by_entry.setdefault(row.race_entry_id, []).append(LapRecord(
                int(row.lap_number), float(row.lap_time) if row.lap_time is not None else None,
                row.track_status_code, bool(row.pit_in), bool(row.pit_out),
            ))
            if row.pit_in:
                pits_by_entry.setdefault(row.race_entry_id, set()).add(int(row.lap_number))
        # stops reconstructed by the pit ingestion backfill cover races without lap enrichment flags
        for row in db.execute(text("""
            SELECT race_entry_id, pit_lap FROM race_strategy_pit_stops WHERE race_id = :r
        """), {"r": race["id"]}):
            pits_by_entry.setdefault(row.race_entry_id, set()).add(int(row.pit_lap))

        field = field_medians(laps_by_entry, {e: stop_laps(p) for e, p in pits_by_entry.items()})
        for entry_id, pit_laps in pits_by_entry.items():
            for pit, condition, loss, reference in stop_losses(laps_by_entry.get(entry_id, []), pit_laps, field):
                observations.append(PitLossObservation(
                    race["id"], race["race_date"], race["track_id"], race["regulation_era"],
                    entry_id, pit, condition, loss, reference,
                ))
    return observations


def _robust(values: list[float]) -> tuple[float, float]:
    centre = median(values)
    return centre, 1.4826 * median(abs(v - centre) for v in values)


def estimate_pit_loss(
    observations: Iterable[PitLossObservation],
    *,
    track_id: int,
    regulation_era: str,
    condition: str = "green",
    before_date: date | None = None,
    min_stops: int = 8,
    min_races: int = 2,
) -> PitLossEstimate | None:
    """Median loss for track x era, falling back to the era-wide median when thin.

    before_date enforces walk-forward use: only races strictly before it count.
    """
    pool = [
        o for o in observations
        if o.condition == condition and o.loss_seconds is not None and o.regulation_era == regulation_era
        and (before_date is None or o.race_date < before_date)
    ]
    for source, rows in (("track_era", [o for o in pool if o.track_id == track_id]), ("era_fallback", pool)):
        races = {o.race_id for o in rows}
        if len(rows) >= min_stops and len(races) >= min_races:
            centre, spread = _robust([o.loss_seconds for o in rows])
            return PitLossEstimate(round(centre, 3), round(spread, 3), len(rows), len(races), source)
    return None


def summarise(observations: list[PitLossObservation], pit_lane_medians: dict[tuple[int, str], float]) -> list[dict]:
    keys = sorted({(o.track_id, o.regulation_era) for o in observations})
    rows = []
    for track_id, era in keys:
        row = {"track_id": track_id, "regulation_era": era}
        for condition in ("green", "sc", "vsc"):
            est = estimate_pit_loss(observations, track_id=track_id, regulation_era=era, condition=condition)
            row[f"{condition}_median"] = est.median_seconds if est else None
            row[f"{condition}_n"] = est.n_stops if est else 0
            row[f"{condition}_source"] = est.source if est else None
        subset = [o for o in observations if (o.track_id, o.regulation_era) == (track_id, era)]
        row["stops_total"] = len(subset)
        row["unknown_status"] = sum(o.condition == "unknown" for o in subset)
        row["no_reference"] = sum(o.condition in ("no_reference", "no_timing") for o in subset)
        row["pit_lane_time_median_v1"] = pit_lane_medians.get((track_id, era))
        rows.append(row)
    return rows


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--csv", default="")
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        observations = load_observations(db, start_year=args.start_year, end_year=args.end_year)
        pit_lane = {
            (r.track_id, r.regulation_era): float(r.m)
            for r in db.execute(text("""
                SELECT r.track_id, r.regulation_era,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY p.total_pit_lane_seconds) AS m
                FROM race_strategy_pit_stops p JOIN races r ON r.id = p.race_id
                WHERE r.season_year BETWEEN :a AND :b GROUP BY r.track_id, r.regulation_era
            """), {"a": args.start_year, "b": args.end_year})
        }
        names = dict(db.execute(text("SELECT id, name FROM tracks")).all())

    counts: dict[str, int] = {}
    for o in observations:
        counts[o.condition] = counts.get(o.condition, 0) + 1
    print(f"stops={len(observations)} by condition: {dict(sorted(counts.items()))}")

    rows = summarise(observations, pit_lane)
    print(f"\n{'track':32} {'era':26} green(n)        sc(n)          vsc(n)        pit-lane v1")
    for row in rows:
        def cell(c):
            if row[c + "_median"] is None:
                return "     -      "
            mark = "*" if row[c + "_source"] == "era_fallback" else " "
            return f"{row[c + '_median']:6.2f}({row[c + '_n']:3}){mark}"
        v1 = f"{row['pit_lane_time_median_v1']:6.2f}" if row["pit_lane_time_median_v1"] is not None else "   -"
        print(f"{names.get(row['track_id'], row['track_id'])[:32]:32} {row['regulation_era'][:26]:26} "
              f"{cell('green')}  {cell('sc')}  {cell('vsc')}  {v1}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(asdict(observations[0]).keys()) if observations else ["race_id"])
            writer.writeheader()
            writer.writerows(asdict(o) for o in observations)
        print(f"\nwrote {len(observations)} stop observations to {args.csv}")
    print("* = too few stops at this track/era; era-wide value shown (n is the era sample).")
    print("Read-only; database unchanged. Not yet wired into the simulator.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
