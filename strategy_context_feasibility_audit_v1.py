
"""
Research-only feasibility audit for the empirical F1 strategy-context prior.

Pre-registered rules:
- Exclude wet races and races with unknown race-session rainfall.
- Grid bands: Top10 / 11+.
- Strategy pattern: compound sequence reconstructed around observed pit stops
  plus pit-timing buckets (early/middle/late thirds).
- Pit count/timing comes from stored FastF1 race_strategy_pit_stops, not
  inferred from stint end_lap, so no pit-lap offset assumption is required.
- Context hierarchy tested here: track x era x grid, track x era, era, pooled.
- No post-race information is used as a context feature.
- The repository currently has a raw overtaking index by track/era but no
  categorical archetype grouping, so archetype levels are reported as a
  prerequisite rather than invented here.

This script never trains a model, modifies the simulator, or writes to the DB.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from race_status import is_classified

SPARSITY_BUCKETS = (
    (0, 5, "<5"),
    (5, 8, "5-7"),
    (8, 10, "8-9"),
    (10, float("inf"), ">=10"),
)

ARCHETYPE_GROUPING_EXISTS = False


def grid_band(position: int) -> str:
    return "Top10" if int(position) <= 10 else "11+"


def pit_timing_bucket(pit_lap: int, total_laps: int) -> str:
    if total_laps <= 0:
        raise ValueError("total_laps must be positive")
    fraction = float(pit_lap) / float(total_laps)
    if fraction < 1.0 / 3.0:
        return "early"
    if fraction < 2.0 / 3.0:
        return "middle"
    return "late"


def sparsity_label(race_count: int) -> str:
    for lo, hi, label in SPARSITY_BUCKETS:
        if lo <= race_count < hi:
            return label
    return ">=10"


def _require_database_url() -> str:
    load_dotenv()
    value = os.getenv("DATABASE_URL")
    if not value:
        raise SystemExit("DATABASE_URL is not set")
    return value


def load_weather_coverage(engine, start_year: int, end_year: int) -> pd.DataFrame:
    query = text(
        """
        SELECT
            r.id AS race_id,
            r.season_year,
            r.round_number,
            r.race_date,
            sw.rainfall
        FROM races r
        JOIN sessions s
          ON s.race_id = r.id
         AND s.session_type = 'R'
        LEFT JOIN session_weather sw
          ON sw.session_id = s.id
        WHERE r.season_year BETWEEN :start_year AND :end_year
          AND r.race_date IS NOT NULL
        ORDER BY r.race_date
        """
    )
    with engine.connect() as conn:
        return pd.read_sql(
            query,
            conn,
            params={"start_year": start_year, "end_year": end_year},
        )


def load_dry_race_context(
    engine,
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    """
    Only confirmed-dry races are admitted to the strategy-prior feasibility
    sample. Unknown rainfall is excluded rather than silently treated as dry.
    """
    query = text(
        """
        SELECT
            r.id AS race_id,
            r.season_year,
            r.regulation_era,
            r.track_id,
            r.race_date,
            re.id AS race_entry_id,
            re.driver_id,
            re.team_id,
            rr.starting_grid_position,
            rr.finishing_position,
            rr.status AS finishing_status
        FROM races r
        JOIN sessions s
          ON s.race_id = r.id
         AND s.session_type = 'R'
        JOIN session_weather sw
          ON sw.session_id = s.id
        JOIN race_entries re
          ON re.race_id = r.id
         AND re.role = 'race_driver'
        JOIN race_results rr
          ON rr.race_entry_id = re.id
         AND rr.session_id = s.id
        WHERE r.season_year BETWEEN :start_year AND :end_year
          AND r.race_date IS NOT NULL
          AND r.regulation_era IS NOT NULL
          AND sw.rainfall IS FALSE
          AND rr.starting_grid_position IS NOT NULL
        ORDER BY r.race_date, re.id
        """
    )
    with engine.connect() as conn:
        df = pd.read_sql(
            query,
            conn,
            params={"start_year": start_year, "end_year": end_year},
        )
    if not df.empty:
        df["grid_band"] = df["starting_grid_position"].map(grid_band)
    return df


def load_stints(engine, dry_race_ids: set[int]) -> pd.DataFrame:
    if not dry_race_ids:
        return pd.DataFrame()

    query = text(
        """
        SELECT
            rs.race_id,
            rs.race_entry_id,
            re.driver_id,
            rs.stint_number,
            rs.compound,
            rs.start_lap,
            rs.end_lap,
            rs.stint_length
        FROM race_stints rs
        JOIN race_entries re
          ON re.id = rs.race_entry_id
        WHERE rs.race_id = ANY(:race_ids)
        ORDER BY rs.race_id, rs.race_entry_id, rs.stint_number
        """
    )
    with engine.connect() as conn:
        return pd.read_sql(query, conn, params={"race_ids": list(dry_race_ids)})


def load_observed_lap_counts(engine, dry_race_ids: set[int]) -> tuple[dict[int, int], dict[tuple[int, int], int]]:
    """Return race-level and driver-level observed lap counts."""
    if not dry_race_ids:
        return {}, {}

    query = text(
        """
        SELECT r.id AS race_id, l.race_entry_id, MAX(l.lap_number) AS driver_laps
        FROM races r
        JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        JOIN laps l ON l.session_id = s.id
        WHERE r.id = ANY(:race_ids) AND l.lap_number IS NOT NULL
        GROUP BY r.id, l.race_entry_id
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {'race_ids': list(dry_race_ids)}).mappings().all()

    driver_laps = {}
    race_laps = {}
    for row in rows:
        race_id = int(row["race_id"])
        entry_id = int(row["race_entry_id"])
        laps = int(row["driver_laps"])
        driver_laps[(race_id, entry_id)] = laps
        race_laps[race_id] = max(race_laps.get(race_id, 0), laps)
    return race_laps, driver_laps

def load_post_pit_compounds(engine, dry_race_ids: set[int]) -> pd.DataFrame:
    """Load the tyre compound on the first recorded lap after each pit."""
    if not dry_race_ids:
        return pd.DataFrame()

    query = text(
        """
        SELECT
            p.race_id,
            p.race_entry_id,
            p.pit_lap,
            l.tire_compound AS post_pit_compound
        FROM race_strategy_pit_stops p
        JOIN sessions s
          ON s.race_id = p.race_id
         AND s.session_type = 'R'
        JOIN laps l
          ON l.session_id = s.id
         AND l.race_entry_id = p.race_entry_id
         AND l.lap_number = p.pit_lap + 1
        WHERE p.race_id = ANY(:race_ids)
          AND p.source = 'fastf1'
          AND l.tire_compound IS NOT NULL
        ORDER BY p.race_id, p.race_entry_id, p.pit_lap
        """
    )
    with engine.connect() as conn:
        return pd.read_sql(query, conn, params={"race_ids": list(dry_race_ids)})

def load_pit_stops(engine, dry_race_ids: set[int]) -> pd.DataFrame:
    if not dry_race_ids:
        return pd.DataFrame()

    query = text(
        """
        SELECT
            p.race_id,
            p.race_entry_id,
            p.pit_lap,
            p.source
        FROM race_strategy_pit_stops p
        WHERE p.race_id = ANY(:race_ids)
          AND p.source = 'fastf1'
        ORDER BY p.race_id, p.race_entry_id, p.pit_lap
        """
    )
    with engine.connect() as conn:
        return pd.read_sql(query, conn, params={"race_ids": list(dry_race_ids)})


def reconstruct_observed_strategy_patterns(
    stints: pd.DataFrame,
    pit_stops: pd.DataFrame,
    context_df: pd.DataFrame,
    race_laps: dict[int, int],
    driver_laps: dict[tuple[int, int], int],
    post_pit_compounds: pd.DataFrame | None = None,
) -> tuple[dict[tuple[int, int], dict[str, Any]], set[tuple[int, int]], set[tuple[int, int]]]:
    """
    Build driver-race strategy observations.

    Compound sequence is aligned to actual pit visits:
      initial compound + compound of the stint that begins after each pit.
    This preserves same-compound pit visits when the pit table has them.

    stop count and exact pit timing come from the stored FastF1 pit table.
    Pit timing is an observed/reactive descriptor only; it is never a
    pre-race context feature.
    """
    patterns: dict[tuple[int, int], dict[str, Any]] = {}
    if post_pit_compounds is None:
        post_pit_compounds = pd.DataFrame()
    all_driver_races: set[tuple[int, int]] = set()
    usable_driver_races: set[tuple[int, int]] = set()

    if stints.empty or context_df.empty:
        return patterns, all_driver_races, usable_driver_races

    context_status = {
        (int(row.race_id), int(row.race_entry_id)): row.finishing_status
        for row in context_df.itertuples(index=False)
    }

    pits_by_entry: dict[tuple[int, int], list[int]] = defaultdict(list)
    if not pit_stops.empty:
        for row in pit_stops.itertuples(index=False):
            pits_by_entry[(int(row.race_id), int(row.race_entry_id))].append(int(row.pit_lap))

    post_pit_compound_by_key: dict[tuple[int, int, int], str] = {}
    if not post_pit_compounds.empty:
        for row in post_pit_compounds.itertuples(index=False):
            compound = str(row.post_pit_compound).upper()
            if compound not in {"SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"}:
                continue
            post_pit_compound_by_key[
                (int(row.race_id), int(row.race_entry_id), int(row.pit_lap))
            ] = compound

    for (race_id, race_entry_id), group in stints.groupby(
        ["race_id", "race_entry_id"], sort=False
    ):
        race_id = int(race_id)
        race_entry_id = int(race_entry_id)
        key = (race_id, race_entry_id)
        all_driver_races.add(key)

        status = context_status.get(key)
        if status is None or not is_classified(status):
            continue

        g = group.sort_values("stint_number").copy()
        g["compound"] = g["compound"].astype(str).str.upper()

        # Use the observed race distance, not nominal track distance. This
        # handles races shortened by red flags or other race-ending events.
        total_laps = race_laps.get(race_id)
        completed_laps = driver_laps.get(key)
        if total_laps is None or total_laps <= 0 or completed_laps is None or completed_laps <= 0:
            continue

        initial = g.iloc[0]["compound"]
        pit_laps = sorted(pits_by_entry.get(key, []))

        # A classified zero-stop race is valid when the single observed stint
        # covers the race and there is no stored pit visit.
        if not pit_laps:
            first_start = int(g.iloc[0]["start_lap"])
            last_end = int(g.iloc[-1]["end_lap"])
            if first_start != 1 or last_end != completed_laps:
                continue
            compounds = (initial,)
            pit_buckets: tuple[str, ...] = ()
        else:
            compounds_list = [initial]
            pit_buckets_list: list[str] = []
            valid = True

            for pit_lap in pit_laps:
                if pit_lap < 1 or pit_lap >= total_laps:
                    valid = False
                    break

                next_compound = post_pit_compound_by_key.get(
                    (race_id, race_entry_id, pit_lap)
                )
                if next_compound is None:
                    valid = False
                    break

                compounds_list.append(next_compound)
                pit_buckets_list.append(pit_timing_bucket(pit_lap, total_laps))

            if not valid:
                continue

            compounds = tuple(compounds_list)
            pit_buckets = tuple(pit_buckets_list)

            # We require the first observed stint to begin at race lap 1 and
            # the final observed stint to reach the recorded race distance.
            if int(g.iloc[0]["start_lap"]) != 1:
                continue
            if int(g.iloc[-1]["end_lap"]) != completed_laps:
                continue

        usable_driver_races.add(key)
        stop_count = len(pit_laps)
        patterns[key] = {
            "compounds": compounds,
            "stop_count": stop_count,
            "pit_buckets": pit_buckets,
        }

    return patterns, all_driver_races, usable_driver_races


@dataclass
class BucketStats:
    race_ids: set[int] = field(default_factory=set)
    all_obs: int = 0
    usable_obs: int = 0
    patterns: Counter = field(default_factory=Counter)
    stop_counts: Counter = field(default_factory=Counter)
    compound_seqs: Counter = field(default_factory=Counter)
    obs_per_race: Counter = field(default_factory=Counter)

    def sparsity(self) -> str:
        return sparsity_label(len(self.race_ids))

    def median_obs_per_race(self) -> float:
        vals = list(self.obs_per_race.values())
        return float(statistics.median(vals)) if vals else 0.0


def build_buckets(
    context_df: pd.DataFrame,
    patterns: dict[tuple[int, int], dict[str, Any]],
    all_driver_races: set[tuple[int, int]],
    usable_driver_races: set[tuple[int, int]],
    key_fn: Callable[[Any], tuple[Any, ...] | None],
) -> dict[tuple[Any, ...], BucketStats]:
    buckets: dict[tuple[Any, ...], BucketStats] = defaultdict(BucketStats)

    for row in context_df.itertuples(index=False):
        dr = (int(row.race_id), int(row.race_entry_id))
        if dr not in all_driver_races:
            continue

        key = key_fn(row)
        if key is None or any(value is None for value in key):
            continue

        bucket = buckets[key]
        bucket.race_ids.add(int(row.race_id))
        bucket.all_obs += 1
        bucket.obs_per_race[int(row.race_id)] += 1

        if dr in usable_driver_races:
            bucket.usable_obs += 1
            pattern = patterns.get(dr)
            if pattern:
                pattern_key = (
                    pattern["compounds"],
                    pattern["stop_count"],
                    pattern["pit_buckets"],
                )
                bucket.patterns[pattern_key] += 1
                bucket.stop_counts[pattern["stop_count"]] += 1
                bucket.compound_seqs[pattern["compounds"]] += 1

    return dict(buckets)


def summarize_bucket(
    level_name: str,
    key: tuple[Any, ...],
    stats: BucketStats,
) -> dict[str, Any]:
    return {
        "hierarchy_level": level_name,
        "bucket_key": str(key),
        "race_count": len(stats.race_ids),
        "all_driver_race_obs": stats.all_obs,
        "usable_obs": stats.usable_obs,
        "unique_strategy_patterns": len(stats.patterns),
        "stop_count_distribution": dict(stats.stop_counts),
        "compound_seq_distribution": {
            str(k): v for k, v in stats.compound_seqs.items()
        },
        "min_obs_per_race": (
            min(stats.obs_per_race.values()) if stats.obs_per_race else 0
        ),
        "median_obs_per_race": round(stats.median_obs_per_race(), 1),
        "max_obs_per_race": (
            max(stats.obs_per_race.values()) if stats.obs_per_race else 0
        ),
        "sparsity_category": stats.sparsity(),
    }


def report_level(
    level_name: str,
    buckets: dict[tuple[Any, ...], BucketStats],
) -> tuple[list[dict[str, Any]], Counter]:
    print(f"\n=== {level_name} ===")
    tally: Counter = Counter()
    rows: list[dict[str, Any]] = []

    ordered = sorted(
        buckets.items(),
        key=lambda item: (-len(item[1].race_ids), str(item[0])),
    )

    for key, stats in ordered:
        row = summarize_bucket(level_name, key, stats)
        tally[row["sparsity_category"]] += 1
        rows.append(row)
        print(
            f"{key}: races={row['race_count']} "
            f"all_obs={row['all_driver_race_obs']} "
            f"usable_obs={row['usable_obs']} "
            f"unique_patterns={row['unique_strategy_patterns']} "
            f"min/median/max_obs_per_race="
            f"{row['min_obs_per_race']}/"
            f"{row['median_obs_per_race']}/"
            f"{row['max_obs_per_race']} "
            f"[{row['sparsity_category']}]"
        )

    print(f"Sparsity summary: {dict(tally)}")
    return rows, tally


def load_raw_overtaking_index(
    engine,
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    """
    Diagnostic only. This reproduces the repository's raw track/era index
    definition. It does NOT create categorical archetypes.
    """
    query = text(
        """
        SELECT
            r.track_id,
            t.name AS track_name,
            r.regulation_era,
            AVG(ABS(rr.starting_grid_position - rr.finishing_position))
                AS avg_position_change,
            COUNT(DISTINCT r.id) AS races_counted
        FROM race_results rr
        JOIN race_entries re ON re.id = rr.race_entry_id
        JOIN races r ON r.id = re.race_id
        JOIN tracks t ON t.id = r.track_id
        JOIN sessions s
          ON s.id = rr.session_id
         AND s.session_type = 'R'
        WHERE r.season_year BETWEEN :start_year AND :end_year
          AND rr.starting_grid_position IS NOT NULL
          AND rr.finishing_position IS NOT NULL
          AND r.regulation_era IS NOT NULL
        GROUP BY r.track_id, t.name, r.regulation_era
        ORDER BY r.regulation_era, r.track_id
        """
    )
    with engine.connect() as conn:
        return pd.read_sql(
            query,
            conn,
            params={"start_year": start_year, "end_year": end_year},
        )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        json.dumps(value, sort_keys=True)
                        if isinstance(value, (dict, list, tuple))
                        else value
                    )
                    for key, value in row.items()
                }
            )


def run_audit(
    engine,
    *,
    start_year: int = 2018,
    end_year: int = 2026,
    output_dir: str = ".",
) -> dict[str, Any]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    weather = load_weather_coverage(engine, start_year, end_year)
    context = load_dry_race_context(engine, start_year, end_year)

    dry_race_ids = set(context["race_id"].astype(int)) if not context.empty else set()
    stints = load_stints(engine, dry_race_ids)
    pits = load_pit_stops(engine, dry_race_ids)
    post_pit_compounds = load_post_pit_compounds(engine, dry_race_ids)
    race_laps, driver_laps = load_observed_lap_counts(engine, dry_race_ids)

    patterns, all_driver_races, usable_driver_races = (
        reconstruct_observed_strategy_patterns(
            stints,
            pits,
            context,
            race_laps,
            driver_laps,
            post_pit_compounds,
        )
    )

    levels = {
        "track x era x grid_band": lambda r: (
            int(r.track_id),
            str(r.regulation_era),
            str(r.grid_band),
        ),
        "track x era (grid_band dropped)": lambda r: (
            int(r.track_id),
            str(r.regulation_era),
        ),
        "track only (era dropped)": lambda r: (int(r.track_id),),
        "era only (track dropped)": lambda r: (str(r.regulation_era),),
        "fully pooled": lambda r: ("ALL",),
    }

    all_rows: list[dict[str, Any]] = []
    level_summaries: dict[str, dict[str, int]] = {}

    for name, key_fn in levels.items():
        buckets = build_buckets(
            context,
            patterns,
            all_driver_races,
            usable_driver_races,
            key_fn,
        )
        rows, tally = report_level(name, buckets)
        all_rows.extend(rows)
        level_summaries[name] = dict(tally)

    bucket_csv = out_dir / "strategy_context_feasibility_v1.csv"
    write_csv(bucket_csv, all_rows)

    overtaking = load_raw_overtaking_index(engine, start_year, end_year)
    overtaking_csv = out_dir / "strategy_context_overtaking_index_raw_v1.csv"
    overtaking.to_csv(overtaking_csv, index=False)

    weather_total = len(weather)
    wet_count = int(weather["rainfall"].fillna(False).eq(True).sum())
    dry_count = int(weather["rainfall"].eq(False).sum())
    unknown_count = int(weather["rainfall"].isna().sum())

    summary = {
        "start_year": start_year,
        "end_year": end_year,
        "race_sessions_seen": weather_total,
        "confirmed_dry_races": dry_count,
        "wet_races_excluded": wet_count,
        "unknown_weather_races_excluded": unknown_count,
        "dry_races_used": len(dry_race_ids),
        "all_driver_race_stint_records": len(all_driver_races),
        "usable_driver_race_strategy_records": len(usable_driver_races),
        "pit_stop_records_used": int(len(pits)),
        "post_pit_compound_records_used": int(len(post_pit_compounds)),
        "races_with_observed_lap_distance": len(race_laps),
        "driver_race_lap_counts": len(driver_laps),
        "archetype_grouping_exists": ARCHETYPE_GROUPING_EXISTS,
        "archetype_levels_tested": False,
        "level_sparsity": level_summaries,
        "files": {
            "bucket_csv": str(bucket_csv),
            "raw_overtaking_index_csv": str(overtaking_csv),
        },
    }

    summary_path = out_dir / "strategy_context_feasibility_summary_v1.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print("\n=== COVERAGE ===")
    print(f"race_sessions_seen={weather_total}")
    print(f"confirmed_dry_races={dry_count}")
    print(f"wet_races_excluded={wet_count}")
    print(f"unknown_weather_races_excluded={unknown_count}")
    print(f"dry_races_used={len(dry_race_ids)}")
    print(f"all_driver_race_stint_records={len(all_driver_races)}")
    print(f"usable_driver_race_strategy_records={len(usable_driver_races)}")
    print(f"pit_stop_records_used={len(pits)}")
    print(f"post_pit_compound_records_used={len(post_pit_compounds)}")
    print(f"races_with_observed_lap_distance={len(race_laps)}")
    print(f"driver_race_lap_counts={len(driver_laps)}")

    print("\n=== PREREQUISITE GAP ===")
    print(
        "No categorical overtaking-index archetype grouping exists in the "
        "current repository. Raw track/era overtaking index was exported only "
        "as a diagnostic. No archetype thresholds were invented."
    )

    print("\n=== OUTPUT ===")
    print(bucket_csv)
    print(overtaking_csv)
    print(summary_path)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit strategy-context bucket feasibility."
    )
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--output-dir", default=".")

    args = parser.parse_args()
    engine = create_engine(_require_database_url())

    run_audit(
        engine,
        start_year=args.start_year,
        end_year=args.end_year,
        output_dir=args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
