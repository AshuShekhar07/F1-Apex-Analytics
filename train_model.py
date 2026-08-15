import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import mean_absolute_error
import joblib

FEATURE_COLUMNS = [
    'quali_position',
    'form_avg_finish', 'form_std_finish',
    'team_pace_recent', 'team_reliability_recent',
    'track_history_avg_finish', 'track_history_avg_quali',
    'teammate_relative_skill',
    'weather_sensitivity',
    'tire_degradation_rate',
    'safety_car_periods', 'vsc_periods',
    'difficulty_rating',
    'post_2022_era',
]

LEAKAGE_COLUMNS = [
    'finishing_position', 'points', 'status', 'gap_to_winner_seconds',
    'starting_grid_position', 'grid_penalty', 'racecraft_delta',
]


def load_and_prepare():
    df = pd.read_csv('features.csv')
    df = df[df['finishing_position'].notna()].copy()
    print(f"Training on {len(df)} rows with known finishing position.")

    df['circuit_type_code'] = df['circuit_type'].astype('category').cat.codes
    df['circuit_type_code'] = df['circuit_type_code'].replace(-1, np.nan)

    features = FEATURE_COLUMNS + ['circuit_type_code']
    return df, features


def time_based_split(df):
    train = df[df['season_year'] <= 2025]
    valid = df[df['season_year'] == 2026]
    print(f"Train: {len(train)} rows (2018-2025). Validate: {len(valid)} rows (2026).")
    return train, valid


def train_model(train_df, valid_df, features):
    X_train = train_df[features]
    y_train = train_df['finishing_position']
    X_valid = valid_df[features]
    y_valid = valid_df['finishing_position']

    model = xgb.XGBRegressor(
        n_estimators=300,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        missing=np.nan,
        random_state=42,
        eval_metric='mae',
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_valid, y_valid)],
        verbose=False,
    )

    preds = model.predict(X_valid)
    mae = mean_absolute_error(y_valid, preds)
    print(f"\nValidation MAE: {mae:.3f} finishing positions")

    residuals = y_valid.values - preds
    residual_std = residuals.std()
    print(f"Residual std dev: {residual_std:.3f}")
    print("(this is the noise level we'll inject during Monte Carlo simulation)")

    return model, residual_std


def show_feature_importance(model, features):
    importance = model.feature_importances_
    ranked = sorted(zip(features, importance), key=lambda x: -x[1])
    print("\nFeature importance:")
    for name, score in ranked:
        print(f"  {name}: {score:.4f}")


def main():
    df, features = load_and_prepare()
    train_df, valid_df = time_based_split(df)

    if len(valid_df) < 20:
        print("\n[WARNING] Very few 2026 rows to validate on -- results may be noisy.")

    model, residual_std = train_model(train_df, valid_df, features)
    show_feature_importance(model, features)

    joblib.dump({
        'model': model,
        'features': features,
        'residual_std': residual_std,
    }, 'finishing_position_model.pkl')

    print("\nSaved model to finishing_position_model.pkl")


if __name__ == "__main__":
    main()
