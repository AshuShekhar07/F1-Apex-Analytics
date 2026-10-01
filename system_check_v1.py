"""End-to-end check of the real database, every API endpoint and the models. Read-only.

  1 DATA       per season 2018..: races, results, qualifying, sprints, laps, pit stops, stints,
               weather, eras -- and what is missing
  2 QUALITY    data_quality_v1 per season (FAIL / WARN counts and the FAIL messages)
  3 FACTS      champions from the stored points vs the real ones, 2018-2025
  4 API        every endpoint called through the app on this database: status and time
  5 MODELS     title chances now, the next race's predicted winner (both stages), the
               stored forecasts' record this season

Exit code 1 if anything FAILS.

    python system_check_v1.py > system_check.txt 2>&1
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import date

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

FIRST_SEASON = 2018
KNOWN_CHAMPIONS = {   # (driver surname, team) -- used only to CHECK the stored points
    2018: ("hamilton", "mercedes"), 2019: ("hamilton", "mercedes"), 2020: ("hamilton", "mercedes"),
    2021: ("verstappen", "mercedes"), 2022: ("verstappen", "red bull"), 2023: ("verstappen", "red bull"),
    2024: ("verstappen", "mclaren"), 2025: ("norris", "mclaren"),
}
problems: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)
    print(f"  FAIL {msg}")


def table_exists(db, name: str) -> bool:
    return bool(db.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": name}).scalar())


# --- 1 data ------------------------------------------------------------------------

def coverage(db, last: int) -> None:
    print("=== 1 DATA COVERAGE (raced = race results loaded) ===")
    opt = {t: table_exists(db, t) for t in ("race_strategy_pit_stops", "race_stints", "session_weather")}
    print(f"  {'season':6} {'races':>5} {'raced':>5} {'quali':>5} {'results':>7} {'per race':>8} "
          f"{'sprints':>9} {'laps ok':>7} {'pit stops':>9} {'stints':>6} {'weather':>7} {'no era':>6}")
    for season in range(FIRST_SEASON, last + 1):
        q = lambda sql: db.execute(text(sql), {"s": season}).scalar() or 0   # noqa: E731
        races = q("SELECT COUNT(*) FROM races WHERE season_year = :s")
        raced = q("""SELECT COUNT(DISTINCT s.race_id) FROM sessions s JOIN races r ON r.id = s.race_id
                     JOIN race_results rr ON rr.session_id = s.id WHERE r.season_year = :s AND s.session_type = 'R'""")
        quali = q("""SELECT COUNT(DISTINCT s.race_id) FROM sessions s JOIN races r ON r.id = s.race_id
                     JOIN qualifying_results qr ON qr.session_id = s.id WHERE r.season_year = :s AND s.session_type = 'Q'""")
        results = q("""SELECT COUNT(*) FROM race_results rr JOIN sessions s ON s.id = rr.session_id
                       JOIN races r ON r.id = s.race_id WHERE r.season_year = :s AND s.session_type = 'R'""")
        sprint_weekends = q("""SELECT COUNT(*) FROM races WHERE season_year = :s
                               AND COALESCE(weekend_format, 'conventional') <> 'conventional'""")
        sprints = q("""SELECT COUNT(DISTINCT s.race_id) FROM sessions s JOIN races r ON r.id = s.race_id
                       JOIN race_results rr ON rr.session_id = s.id WHERE r.season_year = :s AND s.session_type = 'S'""")
        # races where at least 90% of starters have race laps
        laps_ok = q("""SELECT COUNT(*) FROM (
                         SELECT s.id, COUNT(DISTINCT rr.race_entry_id) AS starters,
                                COUNT(DISTINCT l.race_entry_id) AS with_laps
                         FROM sessions s JOIN races r ON r.id = s.race_id
                         JOIN race_results rr ON rr.session_id = s.id
                         LEFT JOIN laps l ON l.session_id = s.id AND l.race_entry_id = rr.race_entry_id
                         WHERE r.season_year = :s AND s.session_type = 'R' GROUP BY s.id) x
                       WHERE with_laps >= 0.9 * starters""")
        pits = q("""SELECT COUNT(DISTINCT p.race_id) FROM race_strategy_pit_stops p JOIN races r ON r.id = p.race_id
                    WHERE r.season_year = :s""") if opt["race_strategy_pit_stops"] else "-"
        stints = q("""SELECT COUNT(DISTINCT st.race_id) FROM race_stints st JOIN races r ON r.id = st.race_id
                      WHERE r.season_year = :s""") if opt["race_stints"] else "-"
        weather = q("""SELECT COUNT(DISTINCT s.race_id) FROM session_weather w JOIN sessions s ON s.id = w.session_id
                       JOIN races r ON r.id = s.race_id WHERE r.season_year = :s AND s.session_type = 'R'""") \
            if opt["session_weather"] else "-"
        no_era = q("SELECT COUNT(*) FROM races WHERE season_year = :s AND regulation_era IS NULL")
        per_race = results / raced if raced else 0
        print(f"  {season:6} {races:5} {raced:5} {quali:5} {results:7} {per_race:8.1f} {sprints:4}/{sprint_weekends:<4} "
              f"{laps_ok:7} {pits:>9} {stints:>6} {weather:>7} {no_era:6}")
        if season < last and raced < races:
            fail(f"{season}: {races - raced} of {races} races have no results")
        if raced and not 18 <= per_race <= 22.5:
            fail(f"{season}: {per_race:.1f} race results per race (expected 20-22)")
        if sprints < sprint_weekends and season < last:
            fail(f"{season}: sprint results for {sprints} of {sprint_weekends} sprint weekends")
        if raced and laps_ok < raced:
            print(f"  note {season}: {raced - laps_ok} raced races with laps for fewer than 90% of starters")
        if no_era:
            fail(f"{season}: {no_era} races without a regulation era")


# --- 2 quality ---------------------------------------------------------------------

def quality(db, last: int) -> None:
    from data_quality_v1 import check_season

    print("\n=== 2 DATA QUALITY (data_quality_v1, identity check off) ===")
    for season in range(FIRST_SEASON, last + 1):
        checks = [c for c in check_season(db, season, identity=False) if c.stage != "scheduled"]
        fails = [c for c in checks if c.fails]
        warns = [c for c in checks if c.warns and not c.fails]
        print(f"  {season}: {len(checks)} races checked, {len(fails)} FAIL, {len(warns)} warn")
        for c in fails:
            fail(f"{season} R{c.round_number} {c.track}: {'; '.join(c.fails)}")


# --- 3 facts -----------------------------------------------------------------------

def facts(db) -> None:
    from championship_model_v1 import load_weekends, standings

    print("\n=== 3 KNOWN FACTS: champions from the stored points ===")
    weekends = load_weekends(db, start_year=FIRST_SEASON)
    dn = {int(i): n for i, n in db.execute(text("SELECT id, name FROM drivers"))}
    tn = {int(i): n for i, n in db.execute(text("SELECT id, name FROM teams"))}
    for season, (driver, team) in KNOWN_CHAMPIONS.items():
        d_pts, t_pts = standings([w for w in weekends if w.season == season], season)
        if not d_pts:
            fail(f"{season}: no points stored")
            continue
        d, t = max(d_pts, key=d_pts.get), max(t_pts, key=t_pts.get)
        ok = driver in dn.get(d, "").lower() and team in tn.get(t, "").lower()
        print(f"  {season}: {dn.get(d)} {d_pts[d]:.0f} pts, {tn.get(t)} {t_pts[t]:.0f} pts  {'ok' if ok else ''}")
        if not ok:
            fail(f"{season}: stored points give {dn.get(d)} / {tn.get(t)}, real champions {driver} / {team}")


# --- 4 api -------------------------------------------------------------------------

def api(db) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    print("\n=== 4 API: every endpoint on this database ===")
    ids = db.execute(text("""
        SELECT r.id AS race_id, r.season_year, r.round_number, r.track_id, r.race_date, re.driver_id, re.team_id
        FROM races r JOIN sessions s ON s.race_id = r.id AND s.session_type = 'R'
        JOIN race_results rr ON rr.session_id = s.id AND rr.finishing_position = 1
        JOIN race_entries re ON re.id = rr.race_entry_id
        ORDER BY r.race_date DESC LIMIT 1""")).mappings().first()
    other = db.execute(text("""SELECT re.driver_id FROM race_entries re WHERE re.race_id = :r
                               AND re.driver_id <> :d AND re.role = 'race_driver' LIMIT 1"""),
                       {"r": ids["race_id"], "d": ids["driver_id"]}).scalar()
    r, d, t, s = ids["race_id"], ids["driver_id"], ids["team_id"], ids["season_year"]
    calls = [
        ("GET", "/health"), ("GET", "/about"),
        ("GET", f"/races?season={s}"), ("GET", f"/races/{r}"), ("GET", f"/races/{r}/qualifying"),
        ("GET", f"/races/{r}/strategy"), ("GET", f"/races/{r}/position-battle"),
        ("GET", f"/races/{r}/practice/FP1"), ("GET", f"/races/{r}/forecast"),
        ("POST", f"/races/{r}/what-if"), ("GET", f"/races/{r}/winner-prediction"),
        ("GET", "/races/next/winner-prediction"),
        ("GET", f"/drivers/{d}/history"), ("GET", f"/drivers/{d}/season/{s}"), ("GET", f"/drivers/{d}/profile"),
        ("GET", "/drivers/wet-weather-ranking"), ("GET", f"/compare/{d}/{other}"),
        ("GET", f"/teams/{t}/season/{s}"), ("GET", f"/tracks/{ids['track_id']}/stats"),
        ("GET", f"/tracks/{ids['track_id']}/overtaking-index"), ("GET", "/records/all-time"),
        ("GET", f"/strategy/predict?track_id={ids['track_id']}&race_date={ids['race_date']}"),
        ("GET", f"/championship/{s}"), ("GET", f"/championship/{s}/history"),
        ("GET", f"/predict-and-compete/{s}/{ids['round_number']}"),
    ]
    client = TestClient(app)
    for method, path in calls:
        started = time.perf_counter()
        try:
            resp = client.post(path, json={"safety_car_laps": [10]}) if method == "POST" else client.get(path)
            code = resp.status_code
        except Exception as exc:                         # an exception is a 500 the client would see
            code, resp = f"error {type(exc).__name__}: {exc}", None
        ms = (time.perf_counter() - started) * 1000
        verdict = "ok" if code == 200 else ("missing data" if code == 404 else "FAIL")
        print(f"  {method:4} {path:60} {code!s:>4}  {ms:7.0f} ms  {verdict}")
        if verdict == "FAIL":
            fail(f"API {method} {path} -> {code}: {resp.text[:200] if resp is not None else ''}")


# --- 5 models ----------------------------------------------------------------------

def models(db, season: int) -> None:
    import winner_prediction_v1 as wp
    from championship_model_v1 import load_weekends, project_season

    print(f"\n=== 5 MODELS ===")
    weekends = load_weekends(db, start_year=FIRST_SEASON, end_year=season)
    dn = {int(i): n for i, n in db.execute(text("SELECT id, name FROM drivers"))}
    tn = {int(i): n for i, n in db.execute(text("SELECT id, name FROM teams"))}
    try:
        sp = project_season(weekends, season)
        p = sp.projection
        print(f"  {season} titles after round {sp.after_round}, {p.remaining_weekends} weekends left:")
        for label, probs, pts, names, exp in (("drivers", p.p_driver_title, sp.driver_points, dn, p.expected_driver_points),
                                              ("constructors", p.p_team_title, sp.team_points, tn, p.expected_team_points)):
            top = sorted(probs, key=lambda k: -probs[k])[:4]
            print(f"    {label:12} " + ", ".join(f"{names.get(k)} {probs[k]:.1%} ({pts.get(k, 0):.0f} -> {exp[k]:.0f} pts)"
                                                  for k in top))
            if abs(sum(probs.values()) - 1) > 0.01:
                fail(f"{label} title chances add up to {sum(probs.values()):.3f}")
    except ValueError as exc:
        fail(f"title projection: {exc}")
    print()
    wp.print_calendar(db)
    print()
    try:
        wp.print_prediction(wp.predict(db, wp.next_race_id(db)))
    except wp.PredictionError as exc:
        fail(f"next race winner prediction: {exc}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=date.today().year)
    parser.add_argument("--skip-api", action="store_true")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        last = db.execute(text("SELECT MAX(season_year) FROM races")).scalar()
        coverage(db, last)
        quality(db, last)
        facts(db)
        if not args.skip_api:
            api(db)
        models(db, args.season)
    print(f"\n=== {len(problems)} FAIL(S) ===")
    for p in problems:
        print(f"  {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
