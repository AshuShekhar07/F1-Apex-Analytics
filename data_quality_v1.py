"""Data-quality checks per race weekend, used as a gate before a forecast is stored.

Stages:
  scheduled  no qualifying yet -- nothing to check
  qualified  qualifying known, race not run: must be forecastable
  raced      race results stored: must be complete enough to score and learn from

A FAIL blocks the forecast for that race (update_latest_races.py) and is printed so it can
be fixed at the source; WARN is reported but does not block.

    python data_quality_v1.py --season 2026
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

MIN_FIELD = 15            # fewer classified entries than this = incomplete load
MIN_SHARE = 0.9           # share of starters that must have grid / race laps
MIN_QUALI_SHARE = 0.75    # 2-4 drivers without a qualifying time (crash, failure, no run) is normal

# Gaps confirmed to be missing at the source: reported as a warning with the reason, never
# as a failure. Add an entry only after a repair script showed the data does not exist.
KNOWN_GAPS: dict[tuple[int, int], dict[str, str]] = {
    (2018, 14): {"race laps": "FastF1 has no race timing for 2018 Monza (results were entered "
                              "from the official classification; insert_2018_monza_results.py)"},
}


@dataclass
class RaceCheck:
    race_id: int
    round_number: int
    track: str
    stage: str
    fails: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fails


def evaluate(facts: dict) -> RaceCheck:
    """Pure rule set over one race's facts (see load_facts for the keys); failures listed in
    KNOWN_GAPS for this weekend become warnings that carry the documented reason."""
    c = _evaluate(facts)
    known = KNOWN_GAPS.get((facts.get("season_year"), facts["round_number"]), {})
    for key, reason in known.items():
        hits = [f for f in c.fails if f.startswith(key)]
        for f in hits:
            c.fails.remove(f)
            c.warns.append(f"known gap: {reason}")
    return c


def _evaluate(facts: dict) -> RaceCheck:
    c = RaceCheck(facts["race_id"], facts["round_number"], facts["track"], "scheduled")
    if facts["results"] > 0:
        c.stage = "raced"
    elif facts["quali_entries"] > 0:
        c.stage = "qualified"
        if facts["race_date_passed"]:
            c.fails.append("race date has passed but no race results are loaded (interrupted load?)")
    elif facts["race_date_passed"]:
        c.stage = "not loaded"
        c.fails.append("race date has passed but no qualifying or results are loaded"
                       + (f" ({facts['sessions']} empty sessions)" if facts["sessions"] else ""))
        return c
    else:
        return c

    if not facts["regulation_era"]:
        c.fails.append("no regulation era")
    if not facts["race_distance_known"]:
        c.fails.append("race distance unknown (track has no total_race_laps and no earlier race here)")
    q, qt = facts["quali_entries"], facts["quali_with_time"]
    if q < MIN_FIELD:
        c.fails.append(f"only {q} qualifying entries")
    elif qt < MIN_QUALI_SHARE * q:
        c.fails.append(f"qualifying times for only {qt}/{q} drivers")
    if facts["duplicate_car_numbers"]:
        c.fails.append(f"car numbers used by two drivers: {facts['duplicate_car_numbers']}")
    if facts["identity_mismatches"]:
        c.fails.append(f"driver identity differs from FastF1: {facts['identity_mismatches']}")

    if c.stage == "raced":
        n = facts["results"]
        if n < MIN_FIELD:
            c.fails.append(f"only {n} race results")
        if facts["results_without_status"]:
            c.fails.append(f"{facts['results_without_status']} results without a status")
        if facts["grid_known"] < MIN_SHARE * n:
            c.fails.append(f"starting grid known for only {facts['grid_known']}/{n}")
        starters = facts["starters"]
        if facts["drivers_with_race_laps"] < MIN_SHARE * starters:
            c.fails.append(f"race laps for only {facts['drivers_with_race_laps']}/{starters} starters "
                           "(fix: python repair_session_laps_v1.py --season YEAR --apply)")
        if not facts["race_weather"]:
            c.warns.append("no race weather (wet/dry unknown; treated as dry)")
        if facts["safety_car_periods"] is None:
            c.warns.append("Safety Car periods not loaded")
        if facts["winner_time"] is None:
            c.warns.append("winner time not loaded")
        if facts["lap_metadata_share"] < 0.5:
            c.warns.append("tyre-life / pit metadata missing for most race laps (strategy models degrade)")
    if not facts["practice_laps"]:
        c.warns.append("no practice laps (forecast uses qualifying pace only)")
    return c


def load_facts(db: Any, race: dict, identity: dict[int, list] | None = None) -> dict:
    r = race["id"]
    one = lambda sql: db.execute(text(sql), {"r": r}).scalar()
    return {
        "race_id": r, "round_number": race["round_number"], "track": race["track"],
        "season_year": race["season_year"],
        "regulation_era": race["regulation_era"],
        "race_distance_known": bool(race["total_race_laps"]) or bool(one("""
            SELECT 1 FROM races r2 JOIN races r ON r.id = :r
            JOIN sessions s ON s.race_id = r2.id AND s.session_type = 'R'
            JOIN laps l ON l.session_id = s.id
            WHERE r2.track_id = r.track_id AND r2.race_date < r.race_date LIMIT 1""")),
        "quali_entries": one("""SELECT COUNT(*) FROM qualifying_results q
            JOIN sessions s ON s.id = q.session_id AND s.session_type = 'Q' WHERE s.race_id = :r"""),
        "quali_with_time": one("""SELECT COUNT(*) FROM qualifying_results q
            JOIN sessions s ON s.id = q.session_id AND s.session_type = 'Q'
            WHERE s.race_id = :r AND COALESCE(q.q1_time, q.q2_time, q.q3_time) IS NOT NULL"""),
        "duplicate_car_numbers": [n for (n,) in db.execute(text("""
            SELECT car_number FROM race_entries WHERE race_id = :r AND car_number IS NOT NULL
            GROUP BY car_number HAVING COUNT(DISTINCT driver_id) > 1"""), {"r": r})],
        "identity_mismatches": (identity or {}).get(r, []),
        "results": one("""SELECT COUNT(*) FROM race_results rr
            JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R' WHERE s.race_id = :r"""),
        "results_without_status": one("""SELECT COUNT(*) FROM race_results rr
            JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
            WHERE s.race_id = :r AND (rr.status IS NULL OR rr.status = '')"""),
        "grid_known": one("""SELECT COUNT(*) FROM race_results rr
            JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
            WHERE s.race_id = :r AND rr.starting_grid_position IS NOT NULL"""),
        "drivers_with_race_laps": one("""SELECT COUNT(DISTINCT l.race_entry_id) FROM laps l
            JOIN sessions s ON s.id = l.session_id AND s.session_type = 'R'
            JOIN race_results rr ON rr.session_id = s.id AND rr.race_entry_id = l.race_entry_id
            WHERE s.race_id = :r AND lower(COALESCE(rr.status, '')) NOT IN
                  ('did not start', 'withdrew', 'did not qualify', 'excluded')"""),
        "starters": one("""SELECT COUNT(*) FROM race_results rr
            JOIN sessions s ON s.id = rr.session_id AND s.session_type = 'R'
            WHERE s.race_id = :r AND lower(COALESCE(rr.status, '')) NOT IN
                  ('did not start', 'withdrew', 'did not qualify', 'excluded')"""),
        "race_date_passed": bool(one("SELECT race_date < CURRENT_DATE FROM races WHERE id = :r")),
        "sessions": one("SELECT COUNT(*) FROM sessions WHERE race_id = :r"),
        "lap_metadata_share": one("""SELECT COALESCE(AVG((l.tyre_life_laps IS NOT NULL)::int), 0) FROM laps l
            JOIN sessions s ON s.id = l.session_id AND s.session_type = 'R' WHERE s.race_id = :r""") or 0.0,
        "race_weather": bool(one("""SELECT 1 FROM session_weather sw
            JOIN sessions s ON s.id = sw.session_id AND s.session_type = 'R' WHERE s.race_id = :r""")),
        "safety_car_periods": race["safety_car_periods"],
        "winner_time": race["winner_time_seconds"],
        "practice_laps": bool(one("""SELECT 1 FROM laps l JOIN sessions s ON s.id = l.session_id
            WHERE s.race_id = :r AND s.session_type IN ('FP1', 'FP2') LIMIT 1""")),
    }


def season_races(db: Any, season: int) -> list[dict]:
    return [dict(r) for r in db.execute(text("""
        SELECT r.id, r.season_year, r.round_number, r.race_date, r.regulation_era, r.safety_car_periods,
               r.winner_time_seconds, t.name AS track, t.total_race_laps
        FROM races r JOIN tracks t ON t.id = r.track_id
        WHERE r.season_year = :s ORDER BY r.round_number
    """), {"s": season}).mappings()]


def identity_mismatches(db: Any, season: int, races: list[dict]) -> dict[int, list[str]]:
    """race_id -> ['#3 DB: X FastF1: Y', ...] for weekends with qualifying (FastF1, cached)."""
    from audit_driver_identity_v1 import compare, official_drivers

    out: dict[int, list[str]] = {}
    for race in races:
        entries = db.execute(text("""
            SELECT re.id, re.car_number, d.name FROM race_entries re JOIN drivers d ON d.id = re.driver_id
            WHERE re.race_id = :r AND re.car_number IS NOT NULL"""), {"r": race["id"]}).all()
        if not entries:
            continue
        try:
            official = official_drivers(season, race["round_number"], race["race_date"])
        except RuntimeError:
            continue
        bad = [f"#{c.car_number} DB: {c.db_driver} FastF1: {c.fastf1_driver}"
               for c in compare(race["id"], entries, official) if c.status in ("mismatch", "conflict")]
        if bad:
            out[race["id"]] = bad
    return out


def check_season(db: Any, season: int, *, identity: bool = True) -> list[RaceCheck]:
    races = season_races(db, season)
    ids = identity_mismatches(db, season, races) if identity else {}
    return [evaluate(load_facts(db, race, ids)) for race in races]


def print_report(checks: list[RaceCheck]) -> None:
    for c in checks:
        if c.stage == "scheduled":
            continue
        verdict = "FAIL" if c.fails else ("WARN" if c.warns else "ok")
        print(f"  R{c.round_number:<2} {c.track:<22} {c.stage:<9} {verdict}"
              + "".join(f"\n        FAIL {m}" for m in c.fails)
              + "".join(f"\n        warn {m}" for m in c.warns))


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--no-identity", action="store_true", help="skip the FastF1 driver-identity check")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        checks = check_season(db, args.season, identity=not args.no_identity)
    print(f"DATA QUALITY {args.season}")
    print_report(checks)
    return 1 if any(c.fails for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
