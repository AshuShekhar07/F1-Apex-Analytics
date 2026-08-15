import pandas as pd
import numpy as np
import joblib

N_SIMULATIONS = 10000
POINTS_TABLE = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}
CURRENT_SEASON = 2026


def load_everything():
    bundle = joblib.load('finishing_position_model.pkl')
    model = bundle['model']
    features = bundle['features']
    residual_std = bundle['residual_std']

    df = pd.read_csv('features.csv')
    df['circuit_type_code'] = df['circuit_type'].astype('category').cat.codes
    df['circuit_type_code'] = df['circuit_type_code'].replace(-1, np.nan)

    return model, features, residual_std, df


def get_current_standings(df):
    completed = df[(df['season_year'] == CURRENT_SEASON) & df['finishing_position'].notna()]

    driver_points = completed.groupby(['driver_id', 'driver_name'])['points'].sum().reset_index()
    driver_points.columns = ['driver_id', 'driver_name', 'current_points']

    team_points = completed.groupby(['team_id', 'team_name'])['points'].sum().reset_index()
    team_points.columns = ['team_id', 'team_name', 'current_points']

    latest_round = completed['round_number'].max()
    return driver_points, team_points, latest_round


def get_remaining_races(df, latest_round):
    import os
    from sqlalchemy import create_engine, text
    from dotenv import load_dotenv
    load_dotenv()
    engine = create_engine(os.getenv('DATABASE_URL'))

    with engine.connect() as conn:
        schedule = pd.read_sql(text("""
            SELECT round_number, track_id FROM races
            WHERE season_year = :season AND round_number > :latest
            ORDER BY round_number
        """), conn, params={"season": CURRENT_SEASON, "latest": int(latest_round)})

    if schedule.empty:
        return pd.DataFrame()

    driver_team = get_driver_team_map(df)
    static_feature_cols = [
        'form_avg_finish', 'form_std_finish', 'team_pace_recent',
        'team_reliability_recent', 'teammate_relative_skill',
        'weather_sensitivity', 'tire_degradation_rate',
        'safety_car_periods', 'vsc_periods',
        'circuit_type_code', 'difficulty_rating', 'post_2022_era',
    ]
    latest_current = df[df['season_year'] == CURRENT_SEASON].sort_values('round_number')
    latest_features = latest_current.groupby('driver_id').tail(1).set_index('driver_id')

    rows = []
    for _, race in schedule.iterrows():
        for _, drv in driver_team.iterrows():
            driver_id = drv['driver_id']
            base = latest_features.loc[driver_id] if driver_id in latest_features.index else None

            row = {
                'driver_id': driver_id, 'driver_name': drv['driver_name'],
                'team_id': drv['team_id'], 'team_name': drv['team_name'],
                'season_year': CURRENT_SEASON, 'round_number': race['round_number'],
                'track_id': race['track_id'], 'quali_position': np.nan,
                'finishing_position': np.nan,
            }
            for col in static_feature_cols:
                row[col] = base[col] if base is not None and col in base else np.nan

            track_hist = df[(df['driver_id'] == driver_id) & (df['track_id'] == race['track_id'])]
            row['track_history_avg_finish'] = track_hist['finishing_position'].mean()
            row['track_history_avg_quali'] = track_hist['quali_position'].mean()

            rows.append(row)

    return pd.DataFrame(rows)


def build_quali_proxy(df):
    known = df[df['quali_position'].notna()].sort_values(['driver_id', 'season_year', 'round_number'])
    proxy = (
        known.groupby('driver_id')['quali_position']
        .apply(lambda s: s.tail(3).mean())
        .reset_index()
    )
    proxy.columns = ['driver_id', 'quali_proxy']
    return proxy


def get_driver_team_map(df, min_races=5):
    current = df[df['season_year'] == CURRENT_SEASON].sort_values('round_number')

    race_counts = current[current['finishing_position'].notna()].groupby('driver_id').size()
    regular_drivers = race_counts[race_counts >= min_races].index

    current = current[current['driver_id'].isin(regular_drivers)]
    current = current[current['driver_name'].notna() & (current['driver_name'] != 'None None')]

    latest = current.groupby('driver_id').tail(1)[['driver_id', 'driver_name', 'team_id', 'team_name']]
    return latest


def simulate_one_race(race_rows, model, features, residual_std, quali_proxy):
    race_rows = race_rows.merge(quali_proxy, on='driver_id', how='left')
    race_rows['quali_position'] = race_rows['quali_position'].fillna(race_rows['quali_proxy'])

    X = race_rows[features].copy()
    preds = model.predict(X)
    noise = np.random.normal(0, residual_std, size=len(preds))
    simulated_score = preds + noise

    dnf_prob = race_rows['team_reliability_recent'].fillna(0.05).clip(0, 0.5)
    dnf_roll = np.random.random(len(race_rows))
    is_dnf = dnf_roll < dnf_prob

    race_rows = race_rows.copy()
    race_rows['simulated_score'] = simulated_score
    race_rows['is_dnf'] = is_dnf

    finishers = race_rows[~race_rows['is_dnf']].sort_values('simulated_score')
    finishers = finishers.reset_index(drop=True)
    finishers['simulated_position'] = finishers.index + 1
    finishers['simulated_points'] = finishers['simulated_position'].map(POINTS_TABLE).fillna(0)

    dnfs = race_rows[race_rows['is_dnf']].copy()
    dnfs['simulated_position'] = np.nan
    dnfs['simulated_points'] = 0

    return pd.concat([finishers, dnfs], ignore_index=True)


def run_simulation(df, model, features, residual_std):
    driver_points, team_points, latest_round = get_current_standings(df)
    remaining = get_remaining_races(df, latest_round)
    quali_proxy = build_quali_proxy(df)
    driver_team = get_driver_team_map(df)

    if remaining.empty:
        print("No remaining races found for the current season -- season may be complete.")
        return None, None, None

    remaining_rounds = sorted(remaining['round_number'].unique())
    print(f"Simulating {len(remaining_rounds)} remaining races, {N_SIMULATIONS} times each...")

    driver_champion_counts = {}
    team_champion_counts = {}
    next_race_win_counts = {}
    next_round = remaining_rounds[0]

    for sim in range(N_SIMULATIONS):
        sim_driver_points = dict(zip(driver_points['driver_id'], driver_points['current_points']))
        sim_team_points = dict(zip(team_points['team_id'], team_points['current_points']))

        for rnd in remaining_rounds:
            race_rows = remaining[remaining['round_number'] == rnd]
            result = simulate_one_race(race_rows, model, features, residual_std, quali_proxy)

            for _, row in result.iterrows():
                sim_driver_points[row['driver_id']] = sim_driver_points.get(row['driver_id'], 0) + row['simulated_points']
                sim_team_points[row['team_id']] = sim_team_points.get(row['team_id'], 0) + row['simulated_points']

            if rnd == next_round:
                winner_row = result[result['simulated_position'] == 1]
                if len(winner_row) > 0:
                    winner_id = winner_row.iloc[0]['driver_id']
                    next_race_win_counts[winner_id] = next_race_win_counts.get(winner_id, 0) + 1

        driver_champ = max(sim_driver_points, key=sim_driver_points.get)
        driver_champion_counts[driver_champ] = driver_champion_counts.get(driver_champ, 0) + 1

        team_champ = max(sim_team_points, key=sim_team_points.get)
        team_champion_counts[team_champ] = team_champion_counts.get(team_champ, 0) + 1

        if (sim + 1) % 2000 == 0:
            print(f"  {sim + 1}/{N_SIMULATIONS} simulations done...")

    return driver_champion_counts, team_champion_counts, next_race_win_counts


def format_results(counts, id_col, name_lookup_df, name_col):
    total = sum(counts.values())
    rows = []
    for entity_id, count in counts.items():
        name_row = name_lookup_df[name_lookup_df[id_col] == entity_id]
        name = name_row[name_col].iloc[0] if len(name_row) > 0 else f"Unknown ({entity_id})"
        rows.append({'name': name, 'probability_pct': round(100 * count / total, 1)})
    result_df = pd.DataFrame(rows).sort_values('probability_pct', ascending=False).reset_index(drop=True)
    return result_df


def main():
    print("Loading model and data...")
    model, features, residual_std, df = load_everything()

    driver_champ_counts, team_champ_counts, next_race_counts = run_simulation(
        df, model, features, residual_std
    )

    if driver_champ_counts is None:
        return

    driver_points, team_points, _ = get_current_standings(df)
    driver_team = get_driver_team_map(df)

    print("\n" + "=" * 60)
    print("WORLD DRIVERS' CHAMPIONSHIP -- win probability")
    print("=" * 60)
    wdc_results = format_results(driver_champ_counts, 'driver_id', driver_team, 'driver_name')
    print(wdc_results.to_string(index=False))

    print("\n" + "=" * 60)
    print("CONSTRUCTORS' CHAMPIONSHIP -- win probability")
    print("=" * 60)
    team_lookup = driver_team[['team_id', 'team_name']].drop_duplicates()
    ccc_results = format_results(team_champ_counts, 'team_id', team_lookup, 'team_name')
    print(ccc_results.to_string(index=False))

    print("\n" + "=" * 60)
    print("NEXT RACE -- win probability")
    print("=" * 60)
    next_race_results = format_results(next_race_counts, 'driver_id', driver_team, 'driver_name')
    print(next_race_results.to_string(index=False))

    wdc_results.to_csv('prediction_wdc.csv', index=False)
    ccc_results.to_csv('prediction_constructors.csv', index=False)
    next_race_results.to_csv('prediction_next_race.csv', index=False)
    print("\nSaved: prediction_wdc.csv, prediction_constructors.csv, prediction_next_race.csv")


if __name__ == "__main__":
    main()
