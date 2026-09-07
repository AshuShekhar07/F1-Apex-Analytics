path = "simulate_season.py"
with open(path) as f:
    content = f.read()

old = """    wdc_results.to_csv('prediction_wdc.csv', index=False)
    ccc_results.to_csv('prediction_constructors.csv', index=False)
    next_race_results.to_csv('prediction_next_race.csv', index=False)
    print("\\nSaved: prediction_wdc.csv, prediction_constructors.csv, prediction_next_race.csv")"""

new = old + """

    print("\\nSaving predictions to season_predictions table...")
    save_predictions_to_db(driver_champ_counts, 'driver_id', driver_team, 'driver_name',
                            'wdc', CURRENT_SEASON, latest_round)
    save_predictions_to_db(team_champ_counts, 'team_id', team_lookup, 'team_name',
                            'constructors', CURRENT_SEASON, latest_round)
    save_predictions_to_db(next_race_counts, 'driver_id', driver_team, 'driver_name',
                            'next_race', CURRENT_SEASON, latest_round)"""

assert old in content, "CSV-save block not found -- file may have changed"
content = content.replace(old, new)

with open(path, "w") as f:
    f.write(content)
print("main() patched")
