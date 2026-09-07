"""
Finds the real distance-along-lap (in meters) where Sector 1 ends and
Sector 2 ends, for a given circuit, by averaging across several clean
laps. Sector boundaries are fixed FIA marshalling points, so this
should converge to a stable answer once averaged.

Usage:
    python get_sector_boundaries.py --year 2024 --gp "Silverstone" --session R
"""
import argparse
import fastf1
import numpy as np

fastf1.Cache.enable_cache("fastf1_cache")  # reuse your existing Apex21 cache dir if you point this at it

def sector_boundary_distances(lap):
    """
    Returns (sector1_end_m, sector2_end_m, lap_length_m) for one lap,
    or None if telemetry / sector times are incomplete for this lap.
    """
    if pd_isna(lap["Sector1Time"]) or pd_isna(lap["Sector2Time"]):
        return None

    tel = lap.get_car_data().add_distance()
    if tel.empty:
        return None

    # tel["Time"] is already relative to this lap's start (starts near 0),
    # so the sector boundary timestamps must be lap-relative too --
    # do NOT add LapStartTime (that's absolute session time and will
    # push us completely outside the telemetry's time window).
    s1_end_time = lap["Sector1Time"]
    s2_end_time = lap["Sector1Time"] + lap["Sector2Time"]

    s1_dist = np.interp(
        s1_end_time.total_seconds(),
        tel["Time"].dt.total_seconds(),
        tel["Distance"],
    )
    s2_dist = np.interp(
        s2_end_time.total_seconds(),
        tel["Time"].dt.total_seconds(),
        tel["Distance"],
    )
    lap_length = tel["Distance"].iloc[-1]
    return s1_dist, s2_dist, lap_length


def pd_isna(x):
    import pandas as pd
    return pd.isna(x)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--gp", type=str, required=True)
    parser.add_argument("--session", type=str, default="R")
    parser.add_argument("--max-laps", type=int, default=25, help="cap how many laps to sample")
    args = parser.parse_args()

    session = fastf1.get_session(args.year, args.gp, args.session)
    session.load(laps=True, telemetry=True, weather=False)

    laps = session.laps.pick_accurate()  # drops in/out laps, SC laps, etc.
    laps = laps.head(args.max_laps)

    s1_results, s2_results, lengths = [], [], []
    for _, lap in laps.iterlaps():
        result = sector_boundary_distances(lap)
        if result is None:
            continue
        s1, s2, length = result
        s1_results.append(s1)
        s2_results.append(s2)
        lengths.append(length)

    if not s1_results:
        print("No usable laps found -- try a different session or raise --max-laps.")
        return

    print(f"Sampled {len(s1_results)} clean laps")
    print(f"Sector 1 end distance:  mean={np.mean(s1_results):.1f} m   std={np.std(s1_results):.2f} m")
    print(f"Sector 2 end distance:  mean={np.mean(s2_results):.1f} m   std={np.std(s2_results):.2f} m")
    print(f"Lap length (telemetry): mean={np.mean(lengths):.1f} m")

    import json
    out = {
        "year": args.year,
        "gp": args.gp,
        "session": args.session,
        "num_laps_sampled": len(s1_results),
        "sector1_end_m": round(float(np.mean(s1_results)), 1),
        "sector2_end_m": round(float(np.mean(s2_results)), 1),
        "lap_length_telemetry_m": round(float(np.mean(lengths)), 1),
    }
    fname = f"sector_boundaries_{args.gp.lower().replace(' ', '_')}_{args.year}.json"
    with open(fname, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Saved -> {fname}")


if __name__ == "__main__":
    main()
