from sqlalchemy import create_engine, text
import os
from strategy_model_v2 import validate_race

engine = create_engine(os.environ["DATABASE_URL"])

with engine.connect() as conn:
    races = conn.execute(text("""
        SELECT
            r.id,
            r.season_year,
            r.round_number,
            r.race_date,
            r.regulation_era
        FROM races r
        WHERE r.season_year BETWEEN 2022 AND 2025
          AND r.race_date IS NOT NULL
          AND r.regulation_era = 'era2_18inch_groundeffect'
          AND EXISTS (
              SELECT 1
              FROM sessions s
              JOIN session_weather sw ON sw.session_id = s.id
              WHERE s.race_id = r.id
                AND s.session_type = 'R'
                AND sw.rainfall = FALSE
          )
        ORDER BY r.race_date
    """)).mappings().all()

results = []

for i, race in enumerate(races, 1):
    try:
        result = validate_race(race["id"])
        if result.get("status") == "ok":
            results.append(result)
            print(
                f"[{i}/{len(races)}] "
                f"{result['race_id']} "
                f"{result['season']}-R{result['round']} "
                f"actual={result['actual_stops']} "
                f"pred={result['predicted_stops']} "
                f"match={result['stop_match']} "
                f"conf={result['confidence_pct']}"
            )
        else:
            print(
                f"[{i}/{len(races)}] "
                f"{race['id']} unavailable"
            )
    except Exception as e:
        print(
            f"[{i}/{len(races)}] "
            f"{race['id']} ERROR {type(e).__name__}: {e}"
        )

if not results:
    raise SystemExit("No validation results.")

matches = sum(r["stop_match"] for r in results)
accuracy = 100 * matches / len(results)

print("\n===== V2 WALK-FORWARD RESULT =====")
print("validated:", len(results))
print("stop-count matches:", matches)
print("stop-count accuracy:", round(accuracy, 1), "%")

for season in sorted(set(r["season"] for r in results)):
    subset = [r for r in results if r["season"] == season]
    m = sum(r["stop_match"] for r in subset)
    print(
        season,
        f"{m}/{len(subset)}",
        f"{100*m/len(subset):.1f}%"
    )

print("\nFailures:")
for r in results:
    if not r["stop_match"]:
        print(
            r["race_id"],
            f"{r['season']}-R{r['round']}",
            "actual=", r["actual_stops"],
            "pred=", r["predicted_stops"],
            "prob=", r["probabilities"],
        )
