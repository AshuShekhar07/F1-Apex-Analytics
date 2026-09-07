from collections import Counter
from sqlalchemy import create_engine, text
import os

engine = create_engine(os.environ["DATABASE_URL"])

FINISHED = [
    "Finished","+1 Lap","+2 Laps","+3 Laps","+4 Laps","+5 Laps","+6 Laps"
]
ERA = "era2_18inch_groundeffect"

def q(sql, p=None):
    with engine.connect() as c:
        return c.execute(text(sql), p or {}).mappings().all()

races = q("""
SELECT id, track_id, season_year, round_number, race_date
FROM races
WHERE regulation_era=:era
  AND race_date IS NOT NULL
  AND season_year BETWEEN 2022 AND 2025
  AND EXISTS (
      SELECT 1 FROM sessions s
      JOIN session_weather sw ON sw.session_id=s.id
      WHERE s.race_id=races.id
        AND s.session_type='R'
        AND sw.rainfall=FALSE
  )
ORDER BY race_date
""", {"era": ERA})

stops = {}
for r in races:
    x = q("""
    SELECT COUNT(*) AS stints
    FROM race_stints rs
    JOIN sessions s
      ON s.race_id=rs.race_id
     AND s.session_type='R'
    JOIN race_results rr
      ON rr.race_entry_id=rs.race_entry_id
     AND rr.session_id=s.id
    WHERE rs.race_id=:rid
      AND rs.finishing_position=1
      AND rr.status=ANY(:finished)
    """, {"rid":r["id"], "finished":FINISHED})
    if x:
        stops[r["id"]] = max(0, int(x[0]["stints"])-1)

races=[r for r in races if r["id"] in stops]

methods = Counter()
details = {m: [] for m in ["global","track_majority","track_recent","track_2recent"]}

for r in races:
    hist = [x for x in races if x["race_date"] < r["race_date"]]
    if not hist:
        continue

    # 1. Global dominant prior
    g = Counter(stops[x["id"]] for x in hist)
    global_pred = g.most_common(1)[0][0]

    # 2. Same-track majority
    th = [x for x in hist if x["track_id"] == r["track_id"]]
    track_majority = None
    track_recent = None
    track_2recent = None

    if th:
        c = Counter(stops[x["id"]] for x in th)
        track_majority = c.most_common(1)[0][0]
        track_recent = stops[th[-1]["id"]]
        track_2recent = Counter(
            stops[x["id"]] for x in th[-2:]
        ).most_common(1)[0][0]

    actual = stops[r["id"]]

    preds = {
        "global": global_pred,
        "track_majority": track_majority,
        "track_recent": track_recent,
        "track_2recent": track_2recent,
    }

    for name,pred in preds.items():
        if pred is not None:
            details[name].append({
                "race_id":r["id"],
                "season":r["season_year"],
                "round":r["round_number"],
                "actual":actual,
                "pred":pred,
                "match":pred==actual
            })

print("\n===== SIMPLE LEAKAGE-FREE BASELINES =====")
for name, rows in details.items():
    if not rows:
        print(name, "no data")
        continue

    ok = sum(x["match"] for x in rows)
    print(
        f"{name}: {ok}/{len(rows)} = "
        f"{100*ok/len(rows):.1f}%"
    )

    for season in sorted(set(x["season"] for x in rows)):
        s=[x for x in rows if x["season"]==season]
        m=sum(x["match"] for x in s)
        print(
            f"  {season}: {m}/{len(s)} "
            f"= {100*m/len(s):.1f}%"
        )

print("\n===== TRACK COVERAGE =====")
for season in sorted(set(r["season_year"] for r in races)):
    rs=[r for r in races if r["season_year"]==season]
    with_prior=sum(
        any(
            x["track_id"]==r["track_id"] and
            x["race_date"]<r["race_date"]
            for x in races
        )
        for r in rs
    )
    print(season, with_prior, "/", len(rs))
