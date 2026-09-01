import os
import time
import fastf1
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

with engine.connect() as conn:
    races = conn.execute(text("""
        SELECT r.id AS race_id, r.season_year, r.round_number
        FROM races r
        WHERE r.winner_time_seconds IS NULL AND r.race_date <= CURRENT_DATE
        ORDER BY r.season_year, r.round_number
    """)).mappings().all()

print(f"{len(races)} races need winner-time backfill")

with engine.connect() as conn:
    for i, r in enumerate(races):
        year, rnd = r["season_year"], r["round_number"]
        try:
            session = fastf1.get_session(year, rnd, "R")
            session.load(laps=False, telemetry=False, weather=False, messages=False)

            winners = session.results[session.results['Position'] == 1]
            if len(winners) == 0:
                print(f"  [{i+1}/{len(races)}] {year} R{rnd}: no winner found, skipping")
                continue
            winner_time = winners.iloc[0]['Time']
            if winner_time is None or str(winner_time) == 'NaT':
                print(f"  [{i+1}/{len(races)}] {year} R{rnd}: winner time is NaT, skipping")
                continue
            seconds = winner_time.total_seconds()

            conn.execute(text("""
                UPDATE races SET winner_time_seconds = :s WHERE id = :rid
            """), {"s": round(seconds, 3), "rid": r["race_id"]})
            conn.commit()
            print(f"  [{i+1}/{len(races)}] {year} R{rnd}: OK ({seconds:.3f}s)")
        except Exception as e:
            print(f"  [{i+1}/{len(races)}] {year} R{rnd}: FAILED ({e})")
        time.sleep(0.3)

print("Done.")
