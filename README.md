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
| `strategy_production_v6.py` | Strategy model currently served by `/strategy/predict` |
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
uvicorn app.main:app --reload          # API on :8000, docs at /docs (run from repo root)
cd circuit-viz-app && npm install && npm run dev   # 3D circuit visualizer on :5173
```

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

**Blocked:** publishing new season predictions (`simulate_season.py`) is
fail-closed until a validated pre-qualifying finishing-position model exists.

**Research only:** strategy simulator v1 (not exposed by the API), tyre
degradation models, empirical strategy prior, traffic/defending effect.

**Known open questions (not yet changed):**
- The overtaking index averages |grid − finish| over every car with a finishing
  position, so retirements count as positions lost.
- "Track record" / `delta_to_track_record` is the fastest valid race lap in the
  database since 2018, not the official lap record, and ignores layout changes.
- `/strategy/predict` serves `strategy_production_v6`, not the v1 simulator.
