"""Guard rails for Pirelli C-compound data (race_compound_nominations).

Facts this module encodes (and nothing more):
  * 2018 had no C-number system -- any 2018 nomination is an error.
  * Pirelli renumbered the range in 2023: the same C-number before and after
    2023 is not necessarily the same rubber.
  * C6 existed only in 2025.
  * Within one race, HARD has the lowest C-number and SOFT the highest.

Because no validated cross-season equivalence exists, compound identity is
SEASON-SCOPED: compound_key(2024, "C3") == "2024:C3". Pooling different seasons
under one C-number needs an explicit, sourced mapping; this module refuses to
guess one. For strategy work, the per-race label (SOFT/MEDIUM/HARD) plus
same-season C-numbers are the comparable quantities.

    python pirelli_compounds_v1.py --start-year 2018 --end-year 2026
"""

from __future__ import annotations

import argparse
import os
import re
from typing import Any, Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

LABELS = ("HARD", "MEDIUM", "SOFT")
C_PATTERN = re.compile(r"^C([0-6])$")
FIRST_C_NUMBER_SEASON = 2019
RENUMBERING_SEASON = 2023
C6_SEASONS = frozenset({2025})


def c_number(c_compound: str) -> int:
    match = C_PATTERN.match(str(c_compound).strip().upper())
    if not match:
        raise ValueError(f"Not a Pirelli C-compound: {c_compound!r}")
    return int(match.group(1))


def compound_key(season_year: int, c_compound: str) -> str:
    """Season-scoped identity: only equal keys are known to be the same rubber."""
    return f"{int(season_year)}:C{c_number(c_compound)}"


def same_rubber_known(season_a: int, c_a: str, season_b: int, c_b: str) -> bool:
    """True only when two nominations are provably the same compound."""
    return compound_key(season_a, c_a) == compound_key(season_b, c_b)


def validate_nominations(season_year: int, nominations: Iterable[tuple[str, str]]) -> list[str]:
    """Issues for one race's (label, c_compound) rows; [] means valid."""
    rows = [(str(label).upper(), str(c).upper()) for label, c in nominations]
    issues: list[str] = []
    if season_year < FIRST_C_NUMBER_SEASON:
        return [f"{season_year} predates C-numbers but has nominations"] if rows else []
    labels = sorted(label for label, _ in rows)
    if labels != sorted(LABELS):
        issues.append(f"labels {labels} != {list(LABELS)}")
    numbers: dict[str, int] = {}
    for label, c in rows:
        try:
            numbers[label] = c_number(c)
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if numbers[label] == 6 and season_year not in C6_SEASONS:
            issues.append(f"C6 nominated in {season_year}; C6 existed only in 2025")
    if set(LABELS) <= numbers.keys() and not numbers["HARD"] < numbers["MEDIUM"] < numbers["SOFT"]:
        issues.append(f"order not HARD < MEDIUM < SOFT: {numbers}")
    return issues


def run_audit(db: Any, *, start_year: int, end_year: int) -> tuple[dict[int, dict[str, int]], list[str]]:
    races = db.execute(text("""
        SELECT r.id, r.season_year, r.round_number
        FROM races r JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        WHERE r.season_year BETWEEN :a AND :b
        ORDER BY r.season_year, r.round_number
    """), {"a": start_year, "b": end_year}).mappings().all()
    nominations: dict[int, list[tuple[str, str]]] = {}
    for row in db.execute(text("SELECT race_id, label, c_compound FROM race_compound_nominations")):
        nominations.setdefault(row.race_id, []).append((row.label, row.c_compound))

    by_season: dict[int, dict[str, int]] = {}
    problems: list[str] = []
    for race in races:
        season = by_season.setdefault(race["season_year"], {"races": 0, "nominated": 0, "invalid": 0})
        season["races"] += 1
        rows = nominations.get(race["id"], [])
        season["nominated"] += int(bool(rows))
        issues = validate_nominations(race["season_year"], rows) if rows else []
        if issues:
            season["invalid"] += 1
            problems.append(f"{race['season_year']} R{race['round_number']} (race {race['id']}): {'; '.join(issues)}")
    return by_season, problems


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2026)
    args = parser.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        by_season, problems = run_audit(db, start_year=args.start_year, end_year=args.end_year)

    print("season races nominated invalid")
    for season, s in sorted(by_season.items()):
        print(f"{season}   {s['races']:5} {s['nominated']:9} {s['invalid']:7}")
    for problem in problems:
        print(f"INVALID {problem}")
    print("Read-only audit; database unchanged.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
