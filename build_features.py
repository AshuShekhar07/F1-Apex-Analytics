"""
Apex21 feature engineering pipeline.
Builds one row per (driver, race) with all features for the win-probability model.
"""

import os
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv('DATABASE_URL'))

SHORT_WINDOW = 3
FORM_WINDOW = 5
TRACK_HISTORY_YEARS = 5


def load_base_data():
    query = text("""
        SELECT
            re.id AS race_entry_id,
            re.race_id,
            re.driver_id,
            re.team_id,
            d.name AS driver_name,
            tm.name AS team_name,
            r.season_year,
            r.round_number,
            r.race_date,
            r.track_id,
            r.weekend_format,
            r.safety_car_periods,
            r.vsc_periods,
            qr.final_position AS quali_position,
            qr.q1_time, qr.q2_time, qr.q3_time,
            rr.finishing_position,
            rr.starting_grid_position,
            rr.points,
            rr.status,
            rr.gap_to_winner_seconds
        FROM race_entries re
        JOIN drivers d ON d.id = re.driver_id
        JOIN teams tm ON tm.id = re.team_id
        JOIN races r ON r.id = re.race_id
        LEFT JOIN sessions qs ON qs.race_id = r.id AND qs.session_type = 'Q'
        LEFT JOIN qualifying_results qr ON qr.session_id = qs.id AND qr.race_entry_id = re.id
        LEFT JOIN sessions rs ON rs.race_id = r.id AND rs.session_type = 'R'
        LEFT JOIN race_results rr ON rr.session_id = rs.id AND rr.race_entry_id = re.id
        WHERE re.role = 'race_driver'
        ORDER BY r.season_year, r.round_number, re.driver_id
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    return df


def load_tire_degradation():
    query = text("""
        SELECT
            l.race_entry_id,
            l.lap_number,
            l.lap_time,
            l.tire_compound
        FROM laps l
        JOIN sessions s ON s.id = l.session_id
        WHERE s.session_type = 'R' AND l.lap_time IS NOT NULL AND l.is_valid = true
        ORDER BY l.race_entry_id, l.tire_compound, l.lap_number
    """)
    with engine.connect() as conn:
        laps = pd.read_sql(query, conn)

    if laps.empty:
        return pd.DataFrame(columns=['race_entry_id', 'tire_degradation_rate'])

    def stint_slope(group):
        group = group.reset_index(drop=True)
        if len(group) < 4:
            return np.nan
        x = np.arange(len(group))
        y = group['lap_time'].astype(float).values
        lo, hi = np.percentile(y, [5, 95])
        mask = (y >= lo) & (y <= hi)
        if mask.sum() < 4:
            return np.nan
        slope = np.polyfit(x[mask], y[mask], 1)[0]
        return slope

    degradation = (
        laps.groupby(['race_entry_id', 'tire_compound'])
        .apply(stint_slope)
        .reset_index(name='slope')
    )
    per_entry = degradation.groupby('race_entry_id')['slope'].mean().reset_index()
    per_entry.columns = ['race_entry_id', 'tire_degradation_rate']
    return per_entry


def add_grid_penalty_and_racecraft(df):
    df['grid_penalty'] = df['starting_grid_position'] - df['quali_position']
    df['racecraft_delta'] = df['starting_grid_position'] - df['finishing_position']
    return df


def add_regulation_era(df):
    df['post_2022_era'] = (df['season_year'] >= 2022).astype(int)
    return df


def add_rolling_form(df):
    df = df.sort_values(['driver_id', 'season_year', 'round_number'])

    def rolling_features(group):
        group = group.copy()
        group['form_avg_finish'] = np.nan
        group['form_std_finish'] = np.nan
        for team_id, team_group in group.groupby('team_id'):
            idx = team_group.index
            roll = team_group['finishing_position'].shift(1).rolling(FORM_WINDOW, min_periods=1)
            group.loc[idx, 'form_avg_finish'] = roll.mean()
            group.loc[idx, 'form_std_finish'] = roll.std()
        return group

    df = df.groupby('driver_id', group_keys=False).apply(rolling_features)
    return df


def add_team_pace_and_reliability(df):
    team_race = (
        df.groupby(['team_id', 'season_year', 'round_number'])
        .agg(team_avg_finish=('finishing_position', 'mean'),
             team_dnf_rate=('status', lambda s: (s != 'Finished').mean()))
        .reset_index()
        .sort_values(['team_id', 'season_year', 'round_number'])
    )

    def rolling_team(group):
        group = group.copy()
        group['team_pace_recent'] = (
            group['team_avg_finish'].shift(1).rolling(SHORT_WINDOW, min_periods=1).mean()
        )
        group['team_reliability_recent'] = (
            group['team_dnf_rate'].shift(1).rolling(SHORT_WINDOW, min_periods=1).mean()
        )
        return group

    team_race = team_race.groupby('team_id', group_keys=False).apply(rolling_team)
    df = df.merge(
        team_race[['team_id', 'season_year', 'round_number', 'team_pace_recent', 'team_reliability_recent']],
        on=['team_id', 'season_year', 'round_number'], how='left'
    )
    return df


def add_track_history(df):
    df = df.sort_values(['driver_id', 'track_id', 'season_year', 'round_number'])

    def track_hist(group):
        group = group.copy()
        group['track_history_avg_finish'] = (
            group['finishing_position'].shift(1).expanding(min_periods=1).mean()
        )
        group['track_history_avg_quali'] = (
            group['quali_position'].shift(1).expanding(min_periods=1).mean()
        )
        return group

    df = df.groupby(['driver_id', 'track_id'], group_keys=False).apply(track_hist)
    return df


def add_teammate_relative_skill(df):
    df = df.sort_values(['race_id', 'team_id'])

    def per_race_team(group):
        group = group.copy()
        if len(group) != 2:
            group['teammate_quali_gap'] = np.nan
            return group
        a, b = group.index
        pos_a, pos_b = group.loc[a, 'quali_position'], group.loc[b, 'quali_position']
        if pd.isna(pos_a) or pd.isna(pos_b):
            group['teammate_quali_gap'] = np.nan
        else:
            group.loc[a, 'teammate_quali_gap'] = pos_a - pos_b
            group.loc[b, 'teammate_quali_gap'] = pos_b - pos_a
        return group

    df = df.groupby(['race_id', 'team_id'], group_keys=False).apply(per_race_team)

    df = df.sort_values(['driver_id', 'season_year', 'round_number'])
    df['teammate_relative_skill'] = (
        df.groupby('driver_id')['teammate_quali_gap']
        .transform(lambda s: s.shift(1).rolling(FORM_WINDOW, min_periods=1).mean())
    )
    return df


def add_weather_sensitivity(df):
    query = text("""
        SELECT s.race_id, sw.rainfall
        FROM session_weather sw
        JOIN sessions s ON s.id = sw.session_id
        WHERE s.session_type = 'R'
    """)
    with engine.connect() as conn:
        weather = pd.read_sql(query, conn)

    if weather.empty:
        df['weather_sensitivity'] = np.nan
        return df

    df = df.merge(weather, on='race_id', how='left')
    df = df.sort_values(['driver_id', 'season_year', 'round_number'])

    def wet_dry_delta(group):
        group = group.copy()
        wet_avg = group.loc[group['rainfall'] == True, 'finishing_position'].mean()
        dry_avg = group.loc[group['rainfall'] == False, 'finishing_position'].mean()
        if pd.isna(wet_avg) or pd.isna(dry_avg):
            group['weather_sensitivity'] = np.nan
        else:
            group['weather_sensitivity'] = wet_avg - dry_avg
        return group

    df = df.groupby('driver_id', group_keys=False).apply(wet_dry_delta)
    return df


def add_track_type_and_sc(df):
    query = text("""
        SELECT id AS track_id, circuit_type, difficulty_rating
        FROM tracks
    """)
    with engine.connect() as conn:
        tracks = pd.read_sql(query, conn)
    df = df.merge(tracks, on='track_id', how='left')
    return df


def main():
    print("Loading base race-entry data...")
    df = load_base_data()
    print(f"  {len(df)} driver-race rows loaded.")

    print("Adding grid penalty + racecraft...")
    df = add_grid_penalty_and_racecraft(df)

    print("Adding regulation era flag...")
    df = add_regulation_era(df)

    print("Adding rolling season form (team-switch aware)...")
    df = add_rolling_form(df)

    print("Adding team pace + reliability (short-window recency-weighted)...")
    df = add_team_pace_and_reliability(df)

    print("Adding track-specific history...")
    df = add_track_history(df)

    print("Adding teammate-relative skill...")
    df = add_teammate_relative_skill(df)

    print("Adding weather sensitivity (nullable if not yet backfilled)...")
    df = add_weather_sensitivity(df)

    print("Adding track type + safety car frequency...")
    df = add_track_type_and_sc(df)

    print("Adding tire degradation rate...")
    tire_deg = load_tire_degradation()
    df = df.merge(tire_deg, on='race_entry_id', how='left')

    df.to_csv('features.csv', index=False)
    print(f"\\nDone. Wrote {len(df)} rows x {len(df.columns)} columns to features.csv")
    print("\\nColumns:", list(df.columns))


if __name__ == "__main__":
    main()
