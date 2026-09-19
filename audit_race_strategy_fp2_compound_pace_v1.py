"""Audit FP2 long-run compound pace estimates by regulation era."""
from __future__ import annotations
import argparse, os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from race_strategy_fp2_compound_calibration_v1 import calibrate_fp2_compound_pace
from race_strategy_fp2_compound_db_adapter_v1 import load_fp2_compound_observations

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--start-year",type=int,default=2019)
    p.add_argument("--end-year",type=int,default=2025)
    p.add_argument("--max-lap-distance",type=int,default=15)
    p.add_argument("--min-pairs",type=int,default=1)
    args=p.parse_args()
    load_dotenv(dotenv_path=Path.cwd()/".env")
    url=os.getenv("DATABASE_URL")
    if not url: raise SystemExit("DATABASE_URL is required")
    engine=create_engine(url)
    with engine.connect() as db:
        rows,warnings=load_fp2_compound_observations(db,start_year=2018,end_year=args.end_year)
        for w in warnings: print("WARNING:",w)
        eras=sorted({r.regulation_era for r in rows})
        print("=== FP2 COMPOUND PACE AUDIT (RESEARCH ONLY) ===")
        print("target_year regulation_era observations groups_soft groups_hard pairs_soft pairs_hard soft_delta_ms medium_delta_ms hard_delta_ms")
        for era in eras:
            for year in range(args.start_year,args.end_year+1):
                train=[r for r in rows if r.regulation_era==era and r.season_year<year]
                try:
                    res=calibrate_fp2_compound_pace(train,min_pairs_per_group=args.min_pairs,max_lap_distance=args.max_lap_distance)
                except ValueError as exc:
                    print(year,era,"ERROR",exc); continue
                print(f"{year:4d} {era:28s} {res.observations:11d} {res.group_counts['SOFT']:11d} {res.group_counts['HARD']:10d} {res.matched_pairs['SOFT']:10d} {res.matched_pairs['HARD']:9d} {res.offsets_seconds['SOFT']*1000:12.3f} {0.0:14.3f} {res.offsets_seconds['HARD']*1000:11.3f}")
                for w in res.warnings:
                    if "exceeds" in w: print("  WARNING:",w)
        print("\nNo simulator integration is performed.")
if __name__=="__main__":
    main()
