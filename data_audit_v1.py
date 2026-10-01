"""What data do the prediction models actually have, season by season?

Measured from the database, never assumed. One row per input the models use; a cell
is the share of what could exist that does exist (races, drivers or laps), so a gap is
visible before it degrades a forecast. Rows marked * feed the production forecast.

    python data_audit_v1.py                   # 2018 to this year
    python data_audit_v1.py --start-year 2022

Read-only.
"""

from __future__ import annotations

import argparse
import os
from datetime import date

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# name, what it measures, SQL returning (season_year, have, could_have); :a/:b = season range.
RACED = """SELECT r.id, r.season_year FROM races r JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
           WHERE r.season_year BETWEEN :a AND :b AND EXISTS (SELECT 1 FROM race_results rr WHERE rr.session_id = s.id)"""
STARTERS = """SELECT r.season_year, rr.*, s.race_id FROM race_results rr
              JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R' JOIN races r ON r.id = s.race_id
              WHERE r.season_year BETWEEN :a AND :b
                AND lower(COALESCE(rr.status, '')) NOT IN ('did not start', 'withdrew', 'did not qualify', 'excluded')"""

CHECKS: list[tuple[str, str]] = [
    ("* qualifying results", f"""
        SELECT x.season_year, COUNT(DISTINCT sq.race_id), COUNT(DISTINCT x.id) FROM ({RACED}) x
        LEFT JOIN sessions sq ON sq.race_id = x.id AND sq.session_type = 'Q'
             AND EXISTS (SELECT 1 FROM qualifying_results q WHERE q.session_id = sq.id)
        GROUP BY 1"""),
    ("* drivers with a qualifying time", """
        SELECT r.season_year, COUNT(*) FILTER (WHERE COALESCE(q.q1_time, q.q2_time, q.q3_time) IS NOT NULL), COUNT(*)
        FROM qualifying_results q JOIN sessions s ON s.id = q.session_id AND s.session_type = 'Q'
        JOIN races r ON r.id = s.race_id WHERE r.season_year BETWEEN :a AND :b GROUP BY 1"""),
    ("* official starting grid", f"""
        SELECT season_year, COUNT(*) FILTER (WHERE starting_grid_position IS NOT NULL), COUNT(*)
        FROM ({STARTERS}) x GROUP BY 1"""),
    ("* race weather (wet/dry known)", f"""
        SELECT x.season_year, COUNT(DISTINCT sw.session_id), COUNT(DISTINCT x.id) FROM ({RACED}) x
        JOIN sessions s ON s.race_id = x.id AND s.session_type = 'R'
        LEFT JOIN session_weather sw ON sw.session_id = s.id GROUP BY 1"""),
    ("* qualifying weather", f"""
        SELECT x.season_year, COUNT(DISTINCT sw.session_id), COUNT(DISTINCT x.id) FROM ({RACED}) x
        LEFT JOIN sessions s ON s.race_id = x.id AND s.session_type = 'Q'
        LEFT JOIN session_weather sw ON sw.session_id = s.id GROUP BY 1"""),
    ("* starters with race laps", f"""
        SELECT x.season_year, COUNT(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM laps l WHERE l.session_id = x.session_id AND l.race_entry_id = x.race_entry_id)), COUNT(*)
        FROM ({STARTERS}) x GROUP BY 1"""),
    ("* race laps with a lap time", """
        SELECT r.season_year, COUNT(l.lap_time), COUNT(*) FROM laps l
        JOIN sessions s ON s.id = l.session_id AND s.session_type = 'R' JOIN races r ON r.id = s.race_id
        WHERE r.season_year BETWEEN :a AND :b GROUP BY 1"""),
    ("  race laps with a tyre compound", """
        SELECT r.season_year, COUNT(l.tire_compound), COUNT(*) FROM laps l
        JOIN sessions s ON s.id = l.session_id AND s.session_type = 'R' JOIN races r ON r.id = s.race_id
        WHERE r.season_year BETWEEN :a AND :b GROUP BY 1"""),
    ("  race laps with tyre life (strategy truth)", """
        SELECT r.season_year, COUNT(l.tyre_life_laps), COUNT(*) FROM laps l
        JOIN sessions s ON s.id = l.session_id AND s.session_type = 'R' JOIN races r ON r.id = s.race_id
        WHERE r.season_year BETWEEN :a AND :b GROUP BY 1"""),
    ("  race laps with a position (lap-1 order)", """
        SELECT r.season_year, COUNT(l.position), COUNT(*) FROM laps l
        JOIN sessions s ON s.id = l.session_id AND s.session_type = 'R' JOIN races r ON r.id = s.race_id
        WHERE r.season_year BETWEEN :a AND :b GROUP BY 1"""),
    ("  races with pit stops recorded", f"""
        SELECT x.season_year, COUNT(DISTINCT p.race_id), COUNT(DISTINCT x.id) FROM ({RACED}) x
        LEFT JOIN race_strategy_pit_stops p ON p.race_id = x.id GROUP BY 1"""),
    ("* weekends with FP2 laps (practice pace)", f"""
        SELECT x.season_year, COUNT(DISTINCT s.race_id), COUNT(DISTINCT x.id) FROM ({RACED}) x
        LEFT JOIN sessions s ON s.race_id = x.id AND s.session_type = 'FP2'
             AND EXISTS (SELECT 1 FROM laps l WHERE l.session_id = s.id) GROUP BY 1"""),
    ("* weekends with FP1 laps", f"""
        SELECT x.season_year, COUNT(DISTINCT s.race_id), COUNT(DISTINCT x.id) FROM ({RACED}) x
        LEFT JOIN sessions s ON s.race_id = x.id AND s.session_type = 'FP1'
             AND EXISTS (SELECT 1 FROM laps l WHERE l.session_id = s.id) GROUP BY 1"""),
    ("  practice laps with a compound", """
        SELECT r.season_year, COUNT(l.tire_compound), COUNT(*) FROM laps l
        JOIN sessions s ON s.id = l.session_id AND s.session_type IN ('FP1', 'FP2') JOIN races r ON r.id = s.race_id
        WHERE r.season_year BETWEEN :a AND :b GROUP BY 1"""),
    ("  practice laps with tyre life", """
        SELECT r.season_year, COUNT(l.tyre_life_laps), COUNT(*) FROM laps l
        JOIN sessions s ON s.id = l.session_id AND s.session_type IN ('FP1', 'FP2') JOIN races r ON r.id = s.race_id
        WHERE r.season_year BETWEEN :a AND :b GROUP BY 1"""),
    ("  Pirelli compound nominations (C-numbers)", f"""
        SELECT x.season_year, COUNT(DISTINCT n.race_id), COUNT(DISTINCT x.id) FROM ({RACED}) x
        LEFT JOIN race_compound_nominations n ON n.race_id = x.id GROUP BY 1"""),
    ("  sprint weekends with sprint laps", f"""
        SELECT x.season_year, COUNT(DISTINCT s.race_id) FILTER (WHERE EXISTS (
                   SELECT 1 FROM laps l WHERE l.session_id = s.id)),
               COUNT(DISTINCT s.race_id)
        FROM ({RACED}) x JOIN races r ON r.id = x.id
        LEFT JOIN sessions s ON s.race_id = x.id AND s.session_type = 'S'
        WHERE r.weekend_format <> 'conventional' GROUP BY 1"""),
    ("  Safety Car periods loaded", f"""
        SELECT x.season_year, COUNT(r.safety_car_periods), COUNT(*) FROM ({RACED}) x JOIN races r ON r.id = x.id
        GROUP BY 1"""),
    ("  regulation era set", f"""
        SELECT x.season_year, COUNT(r.regulation_era), COUNT(*) FROM ({RACED}) x JOIN races r ON r.id = x.id
        GROUP BY 1"""),
]

# counts, not shares
FACTS: list[tuple[str, str]] = [
    ("races raced", f"SELECT season_year, COUNT(*) FROM ({RACED}) x GROUP BY 1"),
    ("sprint weekends", f"""SELECT x.season_year, COUNT(*) FROM ({RACED}) x JOIN races r ON r.id = x.id
                            WHERE r.weekend_format <> 'conventional' GROUP BY 1"""),
    ("wet races", f"""SELECT x.season_year, COUNT(*) FROM ({RACED}) x
                      JOIN sessions s ON s.race_id = x.id AND s.session_type = 'R'
                      JOIN session_weather sw ON sw.session_id = s.id AND sw.rainfall GROUP BY 1"""),
    ("wet qualifying sessions", f"""SELECT x.season_year, COUNT(*) FROM ({RACED}) x
                      JOIN sessions s ON s.race_id = x.id AND s.session_type = 'Q'
                      JOIN session_weather sw ON sw.session_id = s.id AND sw.rainfall GROUP BY 1"""),
    ("grid penalties (start >= 3 places behind quali)", """
        SELECT r.season_year, COUNT(*) FROM race_results rr
        JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R' JOIN races r ON r.id = s.race_id
        JOIN sessions sq ON sq.race_id = r.id AND sq.session_type = 'Q'
        JOIN qualifying_results q ON q.session_id = sq.id AND q.race_entry_id = rr.race_entry_id
        WHERE r.season_year BETWEEN :a AND :b
          AND (rr.starting_grid_position = 0 OR rr.starting_grid_position - q.final_position >= 3)
        GROUP BY 1"""),
    ("retirements (DNF statuses)", f"""
        SELECT season_year, COUNT(*) FILTER (WHERE status = ANY(:dnf)) FROM ({STARTERS}) x GROUP BY 1"""),
    ("results with an unknown status", f"""
        SELECT season_year, COUNT(*) FILTER (WHERE NOT (COALESCE(status, '') = ANY(:known))) FROM ({STARTERS}) x
        GROUP BY 1"""),
    ("stored forecasts", """SELECT r.season_year, COUNT(*) FROM race_forecasts f JOIN races r ON r.id = f.race_id
                            WHERE r.season_year BETWEEN :a AND :b GROUP BY 1"""),
    ("  of them from the qualifying order (no grid yet)", """
        SELECT r.season_year, COUNT(*) FROM race_forecasts f JOIN races r ON r.id = f.race_id
        WHERE r.season_year BETWEEN :a AND :b AND f.grid_source <> 'starting_grid' GROUP BY 1"""),
]


def _params(start: int, end: int) -> dict:
    from race_status import CLASSIFIED_STATUSES, DNF_STATUSES, NON_RACE_STATUSES
    return {"a": start, "b": end, "dnf": list(DNF_STATUSES),
            "known": list(CLASSIFIED_STATUSES + DNF_STATUSES + NON_RACE_STATUSES)}


def run(db, start: int, end: int) -> tuple[list[int], list[tuple[str, dict]], list[tuple[str, dict]]]:
    seasons = list(range(start, end + 1))
    shares, counts = [], []
    for name, sql in CHECKS:
        try:
            rows = db.execute(text(sql), _params(start, end)).all()
            shares.append((name, {int(y): (int(h), int(n)) for y, h, n in rows}))
        except Exception as exc:                        # a missing table/column is itself a finding
            db.rollback()
            shares.append((name, {"error": str(exc).splitlines()[0][:80]}))
    for name, sql in FACTS:
        try:
            rows = db.execute(text(sql), _params(start, end)).all()
            counts.append((name, {int(y): int(n) for y, n in rows}))
        except Exception as exc:
            db.rollback()
            counts.append((name, {"error": str(exc).splitlines()[0][:80]}))
    return seasons, shares, counts


def cell(have_total) -> str:
    if have_total is None:
        return "-"
    have, total = have_total
    if total == 0:
        return "-"
    share = have / total
    return f"{share:4.0%}" + ("!" if share < 0.9 else " ")


def print_report(seasons, shares, counts) -> None:
    head = f"{'':52}" + "".join(f"{y:>7}" for y in seasons)
    print("DATA AUDIT (share of what could exist; ! = below 90%; * = used by the production forecast)")
    print(head)
    for name, by_year in shares:
        if "error" in by_year:
            print(f"{name:52} ERROR {by_year['error']}")
            continue
        print(f"{name:52}" + "".join(f"{cell(by_year.get(y)):>7}" for y in seasons))
    print("\nCOUNTS")
    print(head)
    for name, by_year in counts:
        if "error" in by_year:
            print(f"{name:52} ERROR {by_year['error']}")
            continue
        print(f"{name:52}" + "".join(f"{by_year.get(y, 0):>7}" for y in seasons))


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        print_report(*run(db, args.start_year, args.end_year))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
