"""One command, one short report: is the data complete and is the model still good?

Waits for a running update_latest_races job to finish, then prints only what matters:

  1. latest update   the data-quality + forecast summary from update_2026.log
  2. data quality    every season 2018-now, FAIL lines only
  3. new forecasts   for each raced weekend of the current season: winner called?,
                     podium overlap, average error (from the stored forecast vs result)
  4. scoreboard      headline for production vs grid, each candidate vs production,
                     and the regression gate verdict against the saved baseline

    python apex_report.py                       # prints the report
    python apex_report.py --no-wait             # don't wait for a running update job
    python apex_report.py --save                # also re-save the scoreboard baseline if it PASSES
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
from contextlib import redirect_stdout
from datetime import date

from dotenv import load_dotenv

BASELINE = "scoreboard_baseline_2018.json"
LOG = "update_{season}.log"


def running_update() -> bool:
    out = subprocess.run(["pgrep", "-f", "update_latest_races.py"], capture_output=True, text=True)
    return bool(out.stdout.strip())


def wait_for_update(poll: int = 30) -> None:
    if not running_update():
        return
    print("waiting for the running update_latest_races job to finish", end="", flush=True)
    while running_update():
        time.sleep(poll)
        print(".", end="", flush=True)
    print(" done\n", flush=True)


def update_summary(season: int) -> list[str]:
    path = LOG.format(season=season)
    if not os.path.exists(path):
        return [f"  (no {path}; run update_latest_races.py --season {season} first)"]
    text_ = open(path, encoding="utf-8", errors="replace").read()
    steps = re.findall(r"^\[(\d+)/(\d+)\] (done in \d+s|FAILED.*)$", text_, flags=re.M)
    lines = []
    if steps:
        n, total, status = steps[-1]
        lines.append(f"  loaders: step {n}/{total} {status}")
    stopped = re.search(r"^Stopped\..*$", text_, flags=re.M)
    if stopped:
        lines.append("  " + stopped.group(0))
    block = re.search(r"=== DATA QUALITY.*?(?==== SCOREBOARD|\Z)", text_, flags=re.S)
    lines += ["  " + l for l in (block.group(0).strip().splitlines() if block else ["(no summary yet)"])]
    return lines


def quality_fails(first: int, last: int) -> list[str]:
    from sqlalchemy import create_engine

    from data_quality_v1 import check_season

    engine = create_engine(os.environ["DATABASE_URL"])
    out = []
    with engine.connect() as db:
        for season in range(first, last + 1):
            checks = check_season(db, season, identity=False)
            fails = [c for c in checks if c.fails]
            out.append(f"  {season}: {'ok' if not fails else f'{len(fails)} weekend(s) FAIL'}")
            for c in fails:
                out += [f"      R{c.round_number} {c.track}: {f}" for f in c.fails]
    return out


def season_forecasts(season: int) -> list[str]:
    from sqlalchemy import create_engine, text

    from show_forecast import load, result_summary

    engine = create_engine(os.environ["DATABASE_URL"])
    out = []
    with engine.connect() as db:
        races = db.execute(text("SELECT id, round_number FROM races WHERE season_year = :s ORDER BY round_number"),
                           {"s": season}).all()
        for race_id, rnd in races:
            data = load(db, race_id)
            if not data or not data["forecast"]:
                continue
            s = result_summary(data["drivers"])
            track = data["race"]["track"]
            if s is None:
                leader = data["drivers"][0]["driver_name"] if data["drivers"] else "?"
                out.append(f"  R{rnd:<2} {track:<20} upcoming   predicted winner {leader}")
            else:
                out.append(f"  R{rnd:<2} {track:<20} winner {'called   ' if s['winner_hit'] else 'missed   '}"
                           f"podium {s['podium_overlap']}/3  top10 {s['top10_overlap']}/10  "
                           f"avg error {s['mae']:.1f}")
    return out or ["  no stored forecasts this season"]


def scoreboard(save: bool) -> list[str]:
    """Run the scoreboard quietly and keep the lines that decide anything."""
    import prediction_scoreboard_v1 as sb

    argv = ["prediction_scoreboard_v1.py", "--stored"]
    if os.path.exists(BASELINE):
        argv += ["--compare", BASELINE]
    buf = io.StringIO()
    old = sys.argv
    sys.argv = argv
    try:
        with redirect_stdout(buf):
            code = sb.main()
    finally:
        sys.argv = old
    full = buf.getvalue()
    keep = []
    headline = re.search(r"=== HEADLINE.*?races / drivers[^\n]*", full, flags=re.S)
    if headline:
        wanted = ("metric", "winner_hit", "podium_overlap", "mae ", "mae_finishers", "bad_miss_finishers", "races /")
        keep += ["  " + l for l in headline.group(0).splitlines() if l.startswith(wanted)]
    cand = re.search(r"=== CANDIDATES vs PRODUCTION BLEND.*?(?=\n\n)", full, flags=re.S)
    if cand:
        verdicts = [l for l in cand.group(0).splitlines()[1:] if "BETTER" in l or "WORSE" in l]
        names = sorted({l.split()[0] for l in cand.group(0).splitlines()[1:] if l.strip() and not l.startswith("  ")})
        for n in names:
            mine = [l for l in verdicts if l.startswith(n)]
            better = [l.split()[1] for l in mine if l.rstrip().endswith("BETTER")]
            worse = [l.split()[1] for l in mine if l.rstrip().endswith("WORSE")]
            verdict = "PROMOTE" if better and not worse else "keep off"
            keep.append(f"  candidate {n}: better on {better or 'nothing'}, worse on {worse or 'nothing'} -> {verdict}")
    groups = re.search(r"=== HARD-TO-PASS TRACKS AND WET RACES.*?(?=\n\n|\Z)", full, flags=re.S)
    if groups:
        keep += ["  " + l.strip("= ") if l.startswith("===") else "  " + l for l in groups.group(0).splitlines()]
    for pattern in (r"  in wet qualifying:[^\n]*", r"=== CAR PACE RULE[^\n]*"):
        m = re.search(pattern, full)
        if m:
            keep.append("  " + m.group(0).strip("= "))
    gate = re.search(r"  (PASS|FAIL[^\n]*)\s*$", full)
    if gate:
        keep.append(f"  regression gate vs {BASELINE}: {gate.group(1)}")
        if save and gate.group(1) == "PASS":
            summary = json.loads(json.dumps(sb.LAST_SUMMARY)) if hasattr(sb, "LAST_SUMMARY") else None
            if summary:
                with open(BASELINE, "w") as f:
                    json.dump(summary, f, indent=2)
                keep.append(f"  baseline re-saved to {BASELINE}")
    elif not os.path.exists(BASELINE):
        keep.append(f"  (no {BASELINE} yet: run prediction_scoreboard_v1.py --stored --save {BASELINE})")
    if code not in (0, None) and not gate:
        keep.append(f"  scoreboard exited with {code}")
    return keep


def main() -> int:
    load_dotenv()
    import logging
    logging.getLogger("fastf1").setLevel(logging.WARNING)   # keep the report short
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=date.today().year)
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--save", action="store_true", help="re-save the scoreboard baseline if the gate passes")
    args = parser.parse_args()
    if not args.no_wait:
        wait_for_update()

    sections = [
        (f"1. LATEST UPDATE ({args.season})", lambda: update_summary(args.season)),
        (f"2. DATA QUALITY 2018-{args.season} (failures only)", lambda: quality_fails(2018, args.season)),
        (f"3. {args.season} FORECASTS vs RESULTS", lambda: season_forecasts(args.season)),
        ("4. SCOREBOARD", lambda: scoreboard(args.save)),
    ]
    print(f"APEX21 REPORT  {date.today()}")
    for title, fn in sections:
        print(f"\n{title}")
        try:
            print("\n".join(fn()))
        except Exception as exc:   # one broken section must not hide the others
            print(f"  ERROR {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
