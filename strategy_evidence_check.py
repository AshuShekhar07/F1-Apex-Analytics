from pathlib import Path
from datetime import date
from sqlalchemy import create_engine, text

engine = create_engine(__import__("os").environ["DATABASE_URL"])


def q(sql, params=None):
    with engine.connect() as c:
        return c.execute(
            text(sql),
            params or {}
        ).mappings().all()


# ============================================================
# 1. EXACT MONZA M-H SUPPORTING RACES
# ============================================================

print("\n========================================")
print("MONZA 2026 — EXACT M-H SUPPORTING RACES")
print("========================================")

rows = q("""
    SELECT
        r.id AS race_id,
        r.season_year,
        r.round_number,
        r.race_date,
        t.name AS track_name,
        rs.compound,
        rs.stint_number,
        rs.start_lap,
        rs.end_lap
    FROM races r
    JOIN tracks t
      ON t.id = r.track_id
    JOIN race_stints rs
      ON rs.race_id = r.id
     AND rs.finishing_position = 1
    JOIN sessions s
      ON s.race_id = r.id
     AND s.session_type = 'R'
    JOIN race_results rr
      ON rr.race_entry_id = rs.race_entry_id
     AND rr.session_id = s.id
    WHERE r.regulation_era = 'era3_2026regs'
      AND r.race_date < DATE '2026-09-06'
      AND rr.status IN (
          'Finished',
          '+1 Lap',
          '+2 Laps',
          '+3 Laps',
          '+4 Laps',
          '+5 Laps',
          '+6 Laps'
      )
    ORDER BY
        r.race_date,
        rs.stint_number
""")

by_race = {}

for r in rows:
    rid = int(r["race_id"])

    by_race.setdefault(
        rid,
        {
            "race_id": rid,
            "season": r["season_year"],
            "round": r["round_number"],
            "date": r["race_date"],
            "track": r["track_name"],
            "sequence": [],
            "stints": [],
        }
    )

    by_race[rid]["sequence"].append(
        str(r["compound"]).upper()
    )

    by_race[rid]["stints"].append({
        "compound": str(r["compound"]).upper(),
        "start": r["start_lap"],
        "end": r["end_lap"],
    })


mh = []

for r in by_race.values():
    if tuple(r["sequence"]) == ("MEDIUM", "HARD"):
        mh.append(r)

print("Exact M-H races:", len(mh))

for r in mh:
    print(
        r["race_id"],
        f"{r['season']}-R{r['round']}",
        r["date"],
        r["track"],
        r["stints"],
    )


# ============================================================
# 2. PATCH PRODUCTION API WITH TRANSPARENT EVIDENCE SCOPE
# ============================================================

p = Path("strategy_production_v6.py")
s = p.read_text()

marker = "\ndef confidence(\n"

if "def get_supporting_races(" not in s:
    idx = s.index(marker)

    helper = r'''

def get_supporting_races(
    sequence,
    target_date,
    era,
    limit=5,
):
    """
    Return the actual distinct historical races supporting
    the selected strategy. Evidence is one winner strategy
    per race, never one row per driver/stint.
    """
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                r.id AS race_id,
                r.season_year,
                r.round_number,
                r.race_date,
                t.name AS track_name,
                rs.stint_number,
                rs.compound,
                rs.start_lap,
                rs.end_lap
            FROM races r
            JOIN tracks t
              ON t.id = r.track_id
            JOIN race_stints rs
              ON rs.race_id = r.id
             AND rs.finishing_position = 1
            JOIN sessions s
              ON s.race_id = r.id
             AND s.session_type = 'R'
            JOIN race_results rr
              ON rr.race_entry_id = rs.race_entry_id
             AND rr.session_id = s.id
            WHERE r.regulation_era = :era
              AND r.race_date < :target_date
              AND rr.status = ANY(:finished)
            ORDER BY
                r.race_date,
                rs.stint_number
        """), {
            "era": era,
            "target_date": target_date,
            "finished": [
                "Finished",
                "+1 Lap",
                "+2 Laps",
                "+3 Laps",
                "+4 Laps",
                "+5 Laps",
                "+6 Laps",
            ],
        }).mappings().all()

    grouped = {}

    for row in rows:
        rid = int(row["race_id"])

        grouped.setdefault(
            rid,
            {
                "race_id": rid,
                "season": row["season_year"],
                "round": row["round_number"],
                "race_date": row["race_date"],
                "track_name": row["track_name"],
                "sequence": [],
                "stints": [],
            },
        )

        grouped[rid]["sequence"].append(
            str(row["compound"]).upper()
        )

        grouped[rid]["stints"].append({
            "compound": str(
                row["compound"]
            ).upper(),
            "start_lap": row["start_lap"],
            "end_lap": row["end_lap"],
        })

    wanted = tuple(sequence)

    matches = [
        r for r in grouped.values()
        if tuple(r["sequence"]) == wanted
    ]

    # Newest supporting races first.
    matches.sort(
        key=lambda x: x["race_date"],
        reverse=True,
    )

    return matches[:limit]
'''

    s = s[:idx] + helper + s[idx:]


# Add evidence scope + supporting races after compound_nominations.
needle = '''        "compound_nominations": compounds,

        "selection_method": (
'''

replacement = '''        "compound_nominations": compounds,

        "evidence_scope": "cross-track, same-era, pre-target-date",

        "primary_strategy_evidence": {
            "distinct_races": ranked[0]["races"],
            "scope": "cross-track, same-era, pre-target-date",
            "supporting_races": get_supporting_races(
                primary,
                race_date,
                era,
                limit=5,
            ),
        },

        "selection_method": (
'''

if needle in s and '"evidence_scope": "cross-track' not in s:
    s = s.replace(
        needle,
        replacement,
        1,
    )

p.write_text(s)
print("\nProduction API transparency fields added.")


# ============================================================
# 3. HISTORICAL SILVERSTONE SANITY CHECK
# ============================================================

print("\n========================================")
print("2024 SILVERSTONE SANITY CHECK")
print("========================================")

silverstone = q("""
    SELECT
        r.id,
        r.track_id,
        r.season_year,
        r.round_number,
        r.race_date,
        r.regulation_era
    FROM races r
    JOIN tracks t
      ON t.id = r.track_id
    WHERE r.season_year = 2024
      AND r.race_date = DATE '2024-07-07'
      AND LOWER(t.name) LIKE '%silverstone%'
       OR (
          r.season_year = 2024
          AND r.race_date = DATE '2024-07-07'
          AND LOWER(t.name) LIKE '%british%'
      )
    LIMIT 1
""")

if not silverstone:
    # Fallback: find by date alone and show candidates.
    silverstone = q("""
        SELECT
            r.id,
            r.track_id,
            r.season_year,
            r.round_number,
            r.race_date,
            r.regulation_era,
            t.name AS track_name
        FROM races r
        JOIN tracks t ON t.id = r.track_id
        WHERE r.race_date = DATE '2024-07-07'
        ORDER BY r.id
    """)

print("Race lookup:", silverstone)

if silverstone:
    target = silverstone[0]

    try:
        from strategy_production_v6 import predict

        result = predict(
            int(target["track_id"]),
            target["race_date"],
        )

        print("\nPrediction result:")
        print(result)

    except Exception as exc:
        print(
            "SANITY CHECK ERROR:",
            type(exc).__name__,
            exc,
        )

print("\n========================================")
print("DONE")
print("========================================")
