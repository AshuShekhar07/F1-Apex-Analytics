# Apex21 — F1 Analytics, Prediction & Strategy

Formula 1 analytics platform built on real, sourced data (FastF1, 2018–2026):
race/qualifying/practice analysis, driver and team profiles, comparisons,
records, track intelligence, championship simulation, a 3D circuit visualizer,
and a probabilistic race-strategy simulator under active research.

Principles: no invented statistics, validate against known races, chronological
(walk-forward) validation for models, and research code stays out of production
until it beats simple baselines out of sample.

## Layout

| Path | Purpose |
|---|---|
| `app/` | FastAPI backend (`main.py`, `database.py`, `routers/`, `static/team_logos/`) |
| `schema.sql`, `migration_*.sql` | Database DDL; apply in the order listed in `schema_migrations.py` |
| `backfill_*.py`, `fix_*.py` | FastF1 ingestion and one-off data repairs |
| `race_status.py` | Canonical race-result status taxonomy (classified / DNF / DNS …) |
| `finishing_position_contract.py` | Leakage-safe feature contract for the finishing-position model |
| `strategy_service_v7.py` | Strategy model served by `/strategy/predict` (real stints, track/grid-band precedent, pre-start stage, rain switch) |
| `strategy_production_v6.py` | Legacy strategy model, still served at the deprecated `/strategy/predict-v6` for comparison |
| `race_strategy_*.py`, `audit_*.py` | Strategy simulator v1 and research audits (not production) |
| `circuit-viz-app/` | React + Three.js 3D circuit visualizer and its Python pipeline |
| `tests/` | Unit tests, fixture-DB API contract tests, opt-in real-data tests |

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set DATABASE_URL (and optionally FASTF1_CACHE_DIR)
```

Build an empty database from the repo DDL:

```bash
python schema_migrations.py      # applies every file in SCHEMA_FILES to DATABASE_URL
```

Then populate it with `backfill_history.py` followed by the enrichment backfills.

Check that the live database and the repo DDL agree. Needs no extra
privileges: the repo DDL is applied to a scratch schema inside one transaction
that is rolled back, so nothing in the database changes:

```bash
python audit_schema_drift_v1.py
```

## Run

```bash
./run_site.sh                          # API on :8000 + website on :5173; Ctrl+C stops both
uvicorn app.main:app --reload          # or the API alone: :8000, docs at /docs (run from repo root)
python api_check_v1.py > api_check.txt 2>&1   # site or API not working: one report saying why
cd circuit-viz-app && npm install && npm run dev   # 3D circuit visualizer (also :5173, run it alone)
```

## Strategy data layer (Phase 2, read-only audits)

| Module | What it produces |
|---|---|
| `race_neutralisations_v1.py` | SC / VSC / red-flag windows per race from stored track-status intervals, mapped to the race leader's laps; flags `races.safety_car_periods` counts that disagree |
| `race_strategy_pit_loss_v2.py` | Time lost per stop (in-lap + out-lap vs clean pace), split green / SC / VSC, per track and era with era fallback and a walk-forward date cutoff |
| `pirelli_compounds_v1.py` | Validation of `race_compound_nominations`; season-scoped compound identity (no cross-season C-number equivalence is assumed) |

None of these are wired into the simulator yet; each must pass its real-data
checks (`tests/real_db/test_phase2_data.py`) first.

## Strategy stack (production design)

One tool cannot do three jobs; each layer is validated on its own and ships
only if its race-resampled 90% CI beats a simple baseline. Otherwise the
product shows the baseline.

| Layer | Job | Inputs | Baseline to beat |
|---|---|---|---|
| 1. `race_strategy_precedent_v1.py` | which strategy a car will run | what teams did: track → era, grid band | era's most common strategy |
| 2. `race_strategy_simulator_v2.py` | finishing position, P(win), P(podium) for every car | qualifying pace (`race_pace_from_quali_v1.py`), precedent strategies, retirements, SC/VSC, pit loss v2 | grid position; grid-slot win/podium rates |
| 3. optimiser | *optimal* strategy | needs a validated tyre model | **off** — none exists yet |

`race_strategy_backtest_v2.py` scores layers 1-2 for every starter of every dry
race (walk-forward). The first pole-sitter-only run (optimiser on, no quali
pace) lost to every baseline, which is why the stack is layered.

## Race forecasts (production)

Chosen by the walk-forward backtest (dry races 2024-2025, 717 drivers):

| Shown | Method | Measured accuracy |
|---|---|---|
| predicted position | grid/pace blend (grid + qualifying pace + recent team race form) | 3.01 places MAE (grid alone 3.07) |
| P(win), P(podium) | historical rate for the grid slot | Brier 0.028 / 0.061 |
| strategy | era's most common dry strategy | stop count 70%, sequence 47% |
| what-if | simulator v2 from the stored snapshot | scenario tool, not a forecast |

```bash
python race_forecast_v1.py --season 2026 --missing   # after each qualifying; stores forecasts
```

- `GET /races/{id}/forecast` — stored forecast, strategy alternatives, validation block
- `POST /races/{id}/what-if` — e.g. `{"safety_car_laps": [20, 21, 22], "strategies": {"1": {"sequence": ["MEDIUM", "HARD"], "stop_laps": [18]}}}`

## Tests

Three tiers:

```bash
# 1. Unit tests -- no database needed
pytest

# 2. API contract tests -- a throwaway database is created from the repo DDL,
#    seeded with SYNTHETIC rows (tests/fixture_data.py), and dropped afterwards
APEX21_TEST_DATABASE_URL=postgresql://USER@localhost:5432/postgres pytest

# 3. Real-data tests -- known F1 facts and data invariants against DATABASE_URL
APEX21_REAL_DB_TESTS=1 pytest tests/real_db
```

Tier 3 checks facts such as the 2021 final points (Verstappen 395.5, Hamilton
387.5), the 2024 British GP qualifying top three and pole time, Verstappen's
10-race win streak, and integrity invariants (one winner per race, no duplicate
results, sprint/race sessions kept separate).

## Status

**Production:** races, qualifying, practice, driver profiles/seasons/history,
team seasons, comparisons, records, overtaking index, wet-weather ranking,
Predict & Compete, prediction endpoints, circuit visualizer.

**Championship and race-winner chances** (`championship_model_v1.py`): one strength per
car (per season) and per driver, learned from race finishing orders, simulated
race by race with the real points systems. Walk-forward on 2018-2025
(`championship_backtest_v1.py`):
- race winner: favourite won 57% vs 52% for the grid-slot rates; the race forecast's
  P(win)/P(podium) and its predicted winner (highest P(win)) come from this model;
- drivers' title: calibrated (90%+ calls all came true), chances move ~6.6% a round;
- constructors' title: clearly better than "points leader wins", but 90%+ calls came
  true ~86% of the time (2021 and 2024 surprised it).

Title chances are stored after every round (`championship_projection_v1.py`, run by
`update_latest_races.py`) and served at `/championship/{season}` and
`/championship/{season}/history`. The older `/predictions/...` endpoints read
`season_predictions` from `simulate_season.py`, which stays blocked (fail-closed);
use `/championship` instead.

**Research only:** strategy simulators v1 and v2 (neither exposed by the API; v2
must beat v1 and simple baselines in a walk-forward backtest first), tyre
degradation models, empirical strategy prior, traffic/defending effect.

**Known open questions (not yet changed):**
- The overtaking index averages |grid − finish| over every car with a finishing
  position, so retirements count as positions lost.
- "Track record" / `delta_to_track_record` is the fastest valid race lap in the
  database since 2018, not the official lap record, and ignores layout changes.
- `/strategy/predict` serves `strategy_service_v7` (optional `grid` and `start_compound` query params); the old v6 answer is at `/strategy/predict-v6` (deprecated).

## Frontend

`frontend/` is the web app (React + TypeScript + Vite): season overview, race page with forecast vs
result, tyre strategy and the what-if simulator. See `frontend/README.md` to run it.

## Forecasts in the terminal

```bash
python show_forecast.py --race 185                       # or --season 2026 --round 12
python show_forecast.py --race 185 --sc 20-23            # what-if: Safety Car laps 20-23
python show_forecast.py --race 185 --strategy "Norris:MEDIUM-HARD@18" --vsc 40
python show_forecast.py --race 186 --compute             # build a missing forecast first
```

Shows the stored forecast (predicted order, grid, win/podium chances), the actual result
and error once the race is run, the era's tyre strategies, data notes and tested accuracy.
Model changes are judged with `prediction_scoreboard_v1.py` (see its docstring).

## Keeping the data current

```bash
python update_latest_races.py --season 2026                                     # load, check, forecast
python update_latest_races.py --season 2026 --score scoreboard_baseline_2018.json # + scoreboard gate
python data_quality_v1.py --season 2026                                         # checks only
```

Runs every loader in dependency order (each only fills what is missing), sets the
regulation era for new races, runs the data-quality checks (a FAIL -- e.g. a driver
identity that differs from FastF1 -- blocks that race's forecast), then stores forecasts
for weekends with qualifying and no forecast yet. Resume a failed load with `--from-step N`.
