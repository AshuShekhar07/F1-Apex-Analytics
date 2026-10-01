"""Show a race forecast in the terminal: predicted order, chances, strategy, notes, result.

    python show_forecast.py --race 185
    python show_forecast.py --season 2026 --round 12
    python show_forecast.py --race 185 --sc 20-23                      # what-if: Safety Car laps 20-23
    python show_forecast.py --race 185 --strategy "Norris:MEDIUM-HARD@18" --vsc 40
    python show_forecast.py --race 186 --compute                       # build the forecast if missing

Reads the forecast stored by race_forecast_v1 (so it is exactly what the API serves);
when the race has been run, the actual finish is shown next to each prediction.
Colours are switched off automatically when the output is not a terminal (or --no-color).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any, Sequence

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

COMPOUND_RGB = {"SOFT": (225, 6, 0), "MEDIUM": (245, 196, 0), "HARD": (232, 232, 232),
                "INTERMEDIATE": (47, 168, 79), "WET": (30, 111, 217)}


# --- formatting helpers (pure) -----------------------------------------------------

@dataclass
class Style:
    color: bool

    def rgb(self, s: str, rgb: tuple[int, int, int] | None) -> str:
        if not self.color or rgb is None:
            return s
        return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{s}\x1b[0m"

    def bg(self, s: str, rgb: tuple[int, int, int]) -> str:
        if not self.color:
            return s
        return f"\x1b[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m\x1b[38;2;17;17;17m{s}\x1b[0m"

    def bold(self, s: str) -> str:
        return f"\x1b[1m{s}\x1b[0m" if self.color else s

    def dim(self, s: str) -> str:
        return f"\x1b[2m{s}\x1b[0m" if self.color else s

    def good(self, s: str) -> str:
        return self.rgb(s, (62, 207, 142))

    def bad(self, s: str) -> str:
        return self.rgb(s, (255, 107, 107))


def hex_rgb(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    h = value.lstrip("#")
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def pct(p: float) -> str:
    return "<1%" if 0 < p < 0.005 else f"{p:.0%}"


def prob_bar(p: float, width: int = 10) -> str:
    filled = round(max(0.0, min(1.0, p)) * width)
    return "█" * filled + "·" * (width - filled)


def stint_bar(sequence: Sequence[str], stop_laps: Sequence[int], total_laps: int, style: Style,
              width: int = 40) -> str:
    """Coloured bar, one segment per stint, widths proportional to laps."""
    bounds = [0, *stop_laps, total_laps]
    out, used = [], 0
    for i, compound in enumerate(sequence):
        laps = bounds[i + 1] - bounds[i]
        cells = width - used if i == len(sequence) - 1 else max(1, round(width * laps / total_laps))
        used += cells
        label = compound[0] if compound != "INTERMEDIATE" else "I"
        text_ = (label + " " * cells)[:cells] if style.color else (label * cells)
        out.append(style.bg(text_, COMPOUND_RGB.get(compound, (120, 120, 120))) if style.color else text_)
    return "".join(out)


def parse_laps(value: str | None, total_laps: int) -> tuple[int, ...]:
    """"20-23,40" -> (20, 21, 22, 23, 40)"""
    if not value:
        return ()
    laps: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        if not 1 <= lo <= hi <= total_laps:
            raise ValueError(f"{part!r} is not a lap range within 1-{total_laps}")
        laps.update(range(lo, hi + 1))
    return tuple(sorted(laps))


def parse_strategy(value: str, drivers: Sequence[dict]) -> tuple[int, dict]:
    """"Norris:MEDIUM-HARD@18" or "24:SOFT-MEDIUM-HARD@15,38" -> (driver_id, plan)."""
    who, _, plan = value.partition(":")
    compounds, _, stops = plan.partition("@")
    if not who or not compounds or not stops:
        raise ValueError(f"strategy {value!r}: use DRIVER:COMPOUND-COMPOUND@STOP[,STOP]")
    if who.isdigit():
        matches = [d for d in drivers if d["driver_id"] == int(who)]
    else:
        key = who.strip().lower()
        name = lambda d: (d.get("driver_name") or "").lower()
        matches = ([d for d in drivers if name(d) == key]                         # full name
                   or [d for d in drivers if name(d).split()[-1:] == [key]]       # surname
                   or [d for d in drivers if key in name(d)])                     # part of the name
    if len(matches) != 1:
        raise ValueError(f"strategy {value!r}: driver {who!r} matches {len(matches)} drivers")
    sequence = [c.strip().upper() for c in compounds.split("-")]
    return matches[0]["driver_id"], {"sequence": sequence, "stop_laps": [int(s) for s in stops.split(",")]}


def result_summary(drivers: Sequence[dict]) -> dict | None:
    finished = [d for d in drivers if d.get("finish") is not None]
    if not finished:
        return None
    by_pred = sorted(drivers, key=lambda d: d["predicted_position"])
    top = lambda key, n: {d["driver_id"] for d in drivers if d.get(key) is not None and d[key] <= n}
    winner = next((d for d in finished if d["finish"] == 1), None)
    return {
        "winner": winner,
        "winner_predicted": winner["predicted_position"] if winner else None,
        "winner_hit": bool(winner and by_pred[0]["driver_id"] == winner["driver_id"]),
        "podium_overlap": len(top("predicted_position", 3) & top("finish", 3)),
        "top10_overlap": len(top("predicted_position", 10) & top("finish", 10)),
        "mae": sum(abs(d["finish"] - d["predicted_position"]) for d in finished) / len(finished),
    }


# --- database ------------------------------------------------------------------------

def load(db: Any, race_id: int) -> dict | None:
    from race_forecast_v1 import MODEL_VERSION

    race = db.execute(text("""
        SELECT r.id, r.season_year, r.round_number, r.race_date, t.name AS track, t.country
        FROM races r JOIN tracks t ON t.id = r.track_id WHERE r.id = :r
    """), {"r": race_id}).mappings().first()
    if race is None:
        return None
    forecast = db.execute(text("""
        SELECT id, as_of_date, grid_source, computed_at, inputs, validation
        FROM race_forecasts WHERE race_id = :r AND model_version = :v
    """), {"r": race_id, "v": MODEL_VERSION}).mappings().first()
    drivers = []
    if forecast:
        drivers = [dict(d) for d in db.execute(text("""
            SELECT fd.driver_id, d.name AS driver_name, tm.name AS team_name, tm.color_hex,
                   fd.grid, fd.predicted_position, fd.p_win::float AS p_win, fd.p_podium::float AS p_podium,
                   rr.finishing_position AS finish, rr.status
            FROM race_forecast_drivers fd
            JOIN drivers d ON d.id = fd.driver_id
            LEFT JOIN race_entries re ON re.driver_id = fd.driver_id AND re.race_id = :r
            LEFT JOIN teams tm ON tm.id = re.team_id
            LEFT JOIN sessions s ON s.race_id = :r AND s.session_type = 'R'
            LEFT JOIN race_results rr ON rr.session_id = s.id AND rr.race_entry_id = re.id
            WHERE fd.forecast_id = :f
            ORDER BY fd.predicted_position, fd.grid
        """), {"f": forecast["id"], "r": race_id}).mappings()]
    return {"race": dict(race), "forecast": dict(forecast) if forecast else None, "drivers": drivers}


# --- rendering -----------------------------------------------------------------------

def render(data: dict, style: Style, what_if: list[dict] | None = None, scenario_label: str = "",
           sims: int = 1000) -> str:
    from race_status import is_classified

    race, forecast, drivers = data["race"], data["forecast"], data["drivers"]
    inputs = forecast["inputs"]
    total_laps = int(inputs.get("total_laps") or 0)
    lines = [
        style.bold(f"{race['season_year']} Round {race['round_number']} · {race['track']}"
                   + (f", {race['country']}" if race.get("country") else "")),
        style.dim(f"{race['race_date']:%d %b %Y} · {total_laps} laps · uses only races before "
                  f"{forecast['as_of_date']} · grid from {forecast['grid_source'].replace('_', ' ')}"),
        "",
    ]
    conf = inputs.get("confidence")
    if conf:
        n = conf["measured_on_races"]
        text_ = (f"Confidence: {conf['level'].upper()} ({conf['reason']}) · in {n} "
                 f"earlier race{'s' if n != 1 else ''} like this the forecast was off by {conf['expected_error_places']:.1f} places "
                 f"per driver on average")
        if conf["level"] != "normal":
            text_ += f" (dry-qualifying races: {conf['dry_expected_error_places']:.1f})"
        lines += [style.bad(text_) if conf["level"] == "low" else style.dim(text_),
                  style.dim(f"Range: {conf['coverage']:.0%} of drivers finished inside it in earlier races"), ""]
    ranges = {int(d["driver_id"]): d["range"] for d in inputs.get("drivers", []) if d.get("range")}
    finished = any(d.get("finish") is not None for d in drivers)
    header = (f"{'Pred':>4}  {'Driver':<22} {'Team':<18} {'Grid':>4}  {'Win':<15} {'Podium':<15}"
              + (f" {'Range':<8}" if ranges else ""))
    lines.append(style.bold(header + ("  Actual" if finished else "")))
    for d in drivers:
        name = style.rgb("▌", hex_rgb(d.get("color_hex"))) + f" {d['driver_name'][:20]:<20}"
        row = (f"{'P' + str(d['predicted_position']):>4}  {name} {(d.get('team_name') or '')[:18]:<18} "
               f"{d['grid']:>4}  {prob_bar(d['p_win'])} {pct(d['p_win']):>4} {prob_bar(d['p_podium'])} "
               f"{pct(d['p_podium']):>4}")
        if ranges:
            lo, hi = ranges.get(int(d["driver_id"]), (None, None))
            cell = "-" if lo is None else f"P{lo}" if lo == hi else f"P{lo}-{hi}"
            row += f" {cell:<8}"
        if finished:
            if d.get("finish") is None:
                row += "  -"
            else:
                diff = d["finish"] - d["predicted_position"]
                cell = f"P{d['finish']}" + ("" if is_classified(d.get("status")) else f" ({d.get('status')})")
                mark = "" if diff == 0 else f" {'+' if diff > 0 else ''}{diff}"
                row += "  " + cell + (style.bad(mark) if abs(diff) >= 4 else style.dim(mark))
        lines.append(row)

    summary = result_summary(drivers)
    if summary:
        w = summary["winner"]
        lines += ["", style.bold("Result vs forecast: ")
                  + (style.good("winner called") if summary["winner_hit"] else
                     style.bad(f"winner {w['driver_name']} was predicted P{summary['winner_predicted']}") if w else "no winner")
                  + f" · podium {summary['podium_overlap']}/3 · top 10 {summary['top10_overlap']}/10"
                  + f" · average error {summary['mae']:.1f} places"]

    options = inputs.get("strategy_options", [])
    total_weight = sum(float(o.get("weight", 0)) for o in options) or 1.0     # stored weights are counts
    if options and total_laps:
        lines += ["", style.bold("Tyre strategy (most common in this era, share of cars)")]
        for o in options:
            stops = ", ".join(map(str, o.get("stop_laps", [])))
            lines.append(f"  {stint_bar(o['sequence'], o.get('stop_laps', []), total_laps, style)}  "
                         f"{' → '.join(o['sequence']):<24} stop lap {stops:<8} {pct(float(o.get('weight', 0)) / total_weight):>4}")
    extras = inputs.get("strategy_extras", {})
    predicted = extras.get("predicted")
    if predicted and total_laps:
        stops = ", ".join(map(str, predicted.get("stop_laps", [])))
        lines.append(style.bold("  Predicted for this race: ")
                     + f"{' → '.join(predicted['sequence'])}, stop lap {stops}"
                     + style.dim(f"  ({predicted.get('source', '')})"))
    weather = extras.get("weather")
    if weather:
        lines.append(style.dim(f"  weather: rain expected {weather.get('rain_expected')}, "
                               f"wet-strategy trigger {weather.get('wet_strategy_trigger')}"))

    v = (forecast.get("validation") or {}).get("predicted_position", {})
    if v:
        lines += ["", style.dim(f"How accurate is this? Tested walk-forward on past races: off by "
                                f"{v.get('mean_abs_error_places', float('nan')):.2f} places on average "
                                f"(grid order alone {v.get('grid_only_mean_abs_error_places', float('nan')):.2f}); "
                                f"finishers off by 10+ places {v.get('finishers_off_by_10_or_more', 0):.1%}.")]
    notes = inputs.get("data_notes", [])
    if notes:
        lines += ["", style.bold(f"Data notes ({len(notes)})")] + [style.dim(f"  • {n}") for n in notes]

    if what_if is not None:
        lines += ["", style.bold(f"What if: {scenario_label}"),
                  style.dim(f"  {sims:,} simulated races with and without the change; the difference is the useful part"),
                  style.bold(f"  {'Driver':<22} {'Grid':>4}  {'Typical finish':<16} {'Avg change':>10}  "
                             f"{'Win':<12} {'Podium':<12}")]
        for r in sorted(what_if, key=lambda r: r["scenario"]["expected_position"]):
            b, s = r["baseline"], r["scenario"]
            delta = r["expected_position_change"]
            plain = (f"▲ {abs(delta):.1f}" if delta <= -0.05 else f"▼ {delta:.1f}" if delta >= 0.05 else "±0.0")
            plain = f"{plain:>10}"                     # pad before colouring so columns line up
            change = style.good(plain) if delta <= -0.05 else style.bad(plain) if delta >= 0.05 else style.dim(plain)
            lines.append(f"  {(r.get('driver_name') or str(r['driver_id']))[:22]:<22} {r['grid']:>4}  "
                         f"{'P' + str(b['median_position']) + ' → P' + str(s['median_position']):<16} {change}  "
                         f"{pct(b['p_win']) + ' → ' + pct(s['p_win']):<12} {pct(b['p_podium']) + ' → ' + pct(s['p_podium']):<12}")
    return "\n".join(lines)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--race", type=int, help="race id")
    parser.add_argument("--season", type=int)
    parser.add_argument("--round", type=int)
    parser.add_argument("--compute", action="store_true", help="compute and store the forecast if missing")
    parser.add_argument("--sc", help="what-if: Safety Car laps, e.g. 20-23")
    parser.add_argument("--vsc", help="what-if: Virtual Safety Car laps, e.g. 35,36")
    parser.add_argument("--strategy", action="append", default=[],
                        help='what-if: "DRIVER:COMPOUND-COMPOUND@STOP[,STOP]" (name or id), repeatable')
    parser.add_argument("--sims", type=int, default=1000)
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()
    style = Style(color=sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ)

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as db:
        race_id = args.race
        if race_id is None:
            if not (args.season and args.round):
                parser.error("give --race ID, or --season YEAR --round N")
            race_id = db.execute(text("SELECT id FROM races WHERE season_year = :s AND round_number = :r"),
                                 {"s": args.season, "r": args.round}).scalar()
            if race_id is None:
                print(f"No race {args.season} round {args.round} in the database.")
                return 1
        data = load(db, race_id)
    if data is None:
        print(f"Race {race_id} not found.")
        return 1
    if data["forecast"] is None:
        if not args.compute:
            print(f"No forecast stored for race {race_id}. Run again with --compute "
                  f"(needs qualifying results), or: python race_forecast_v1.py --race-id {race_id}")
            return 1
        from race_forecast_v1 import ForecastError, compute_forecast, store_forecast
        try:
            with engine.begin() as db:
                store_forecast(db, compute_forecast(db, race_id))
        except ForecastError as exc:
            print(f"Cannot forecast race {race_id} yet: {exc}")
            return 1
        with engine.connect() as db:
            data = load(db, race_id)

    what_if, label = None, ""
    if args.sc or args.vsc or args.strategy:
        from race_forecast_v1 import Scenario, run_what_if

        inputs = data["forecast"]["inputs"]
        total = int(inputs["total_laps"])
        try:
            strategies = tuple(parse_strategy(s, data["drivers"]) for s in args.strategy)
            scenario = Scenario(parse_laps(args.sc, total), parse_laps(args.vsc, total), strategies)
            what_if = run_what_if(inputs, scenario, sims=args.sims)
        except ValueError as exc:
            print(f"what-if: {exc}")
            return 1
        names = {d["driver_id"]: d["driver_name"] for d in data["drivers"]}
        what_if = [{**r, "driver_name": names.get(r["driver_id"])} for r in what_if]
        parts = ([f"Safety Car laps {args.sc}"] if args.sc else []) + ([f"VSC laps {args.vsc}"] if args.vsc else []) \
            + [f"{names[d]} {' → '.join(p['sequence'])} stop {','.join(map(str, p['stop_laps']))}" for d, p in strategies]
        label = "; ".join(parts)
    print(render(data, style, what_if, label, args.sims))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
