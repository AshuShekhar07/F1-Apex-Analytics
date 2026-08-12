import os
import fastf1
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ["DATABASE_URL"]
SEASONS = range(2018, 2027)


def classify_format(year, event_format):
    if event_format in ("sprint_shootout", "sprint_qualifying", "sprint"):
        return "sprint_legacy" if year <= 2022 else "sprint_shootout"
    return "conventional"


def main():
    engine = create_engine(DATABASE_URL)
    updated = 0
    skipped = 0

    with engine.begin() as conn:
        for year in SEASONS:
            try:
                schedule = fastf1.get_event_schedule(year, include_testing=False)
            except Exception as e:
                print(f"  [WARN] Could not fetch schedule for {year}: {e}")
                continue

            for _, event in schedule.iterrows():
                round_num = int(event["RoundNumber"])
                if round_num == 0:
                    continue

                event_format = str(event.get("EventFormat", "conventional"))
                weekend_format = classify_format(year, event_format)

                result = conn.execute(
                    text("""
                        UPDATE races
                        SET weekend_format = :fmt
                        WHERE season_year = :year AND round_number = :rnd
                    """),
                    {"fmt": weekend_format, "year": year, "rnd": round_num},
                )
                if result.rowcount:
                    updated += result.rowcount
                    if weekend_format != "conventional":
                        print(f"  {year} R{round_num}: {weekend_format} ({event.get('EventName', '')})")
                else:
                    skipped += 1

    print(f"\nDone. Updated {updated} race rows, {skipped} rounds not found in DB.")


if __name__ == "__main__":
    main()
