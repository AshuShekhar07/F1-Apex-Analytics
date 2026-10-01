# Deploying Apex21

Everything runs as three containers from one image:

| service     | what it does                                                         |
|-------------|----------------------------------------------------------------------|
| `db`        | PostgreSQL 16 with all race data, forecasts and the live record      |
| `api`       | FastAPI on port 8000 (`/health`, `/about`, forecasts, strategy, ...)  |
| `scheduler` | `run_pipeline.py --loop`: loads new races and forecasts them unattended |

## 1. Pick a server

Any Linux machine with Docker works. The pipeline loads F1 timing data with pandas, so give it
at least **2 vCPU and 4 GB RAM** and ~20 GB of disk (database + FastF1 cache). Options:

* **One small VPS** (any provider) running `docker compose` -- simplest and cheapest.
* **Managed**: a hosted PostgreSQL plus a container host for `api` and `scheduler`. Set
  `DATABASE_URL` to the hosted database and skip the `db` service.

The server must be able to reach `livetiming.formula1.com`, `api.jolpi.ca`, `api.openf1.org`,
`raw.githubusercontent.com` (FastF1 schedule) and `archive-api.open-meteo.com` (weather).

## 2. Configure

```bash
git clone https://github.com/AshuShekhar07/F1-Apex-Analytics.git && cd F1-Apex-Analytics
cp .env.example .env
```

In `.env` set at least:

```
POSTGRES_PASSWORD=<a long random password>
CORS_ORIGINS=https://<your-frontend-domain>
```

`APP_ENV=production` is set by `docker-compose.yml`. Team logos and driver photos stay off
(`SERVE_BRAND_ASSETS=false`): they belong to Formula 1, the teams or photographers.

## 3. Move your existing database (keeps 2018-2026 and every forecast)

On your laptop:

```bash
ops/db_dump.sh                       # -> apex21_YYYYMMDD_HHMM.dump
scp apex21_*.dump you@server:~/F1-Apex-Analytics/
```

On the server:

```bash
docker compose up -d db
docker compose cp apex21_*.dump db:/tmp/apex21.dump
docker compose exec db pg_restore --clean --if-exists --no-owner -U apex21 -d apex21 /tmp/apex21.dump
```

(Or `ops/db_restore.sh FILE.dump postgresql://...` against a hosted database.)

## 4. Start

```bash
docker compose up -d --build
curl http://localhost:8000/health
```

`/health` says `ok` once a pipeline run has succeeded; `degraded` lists the problem (a failed
run, or no run for a week); HTTP 503 means the database is unreachable. Point an uptime
monitor at it.

## 5. Day to day

* The scheduler checks every hour and runs the update **every 2 h on race weekends, daily
  otherwise**. Runs never overlap (database lock). History: `pipeline_runs` table, logs in the
  `pipelinelogs` volume (`docker compose exec scheduler ls logs`).
* Run an update by hand: `docker compose exec scheduler python run_pipeline.py`
* Weekly report: `docker compose exec api python apex_report.py --no-wait`
* Back up: `docker compose exec db pg_dump -Fc -U apex21 apex21 > backup_$(date +%F).dump`
* Upgrade: `git pull && docker compose up -d --build`

## 6. Put HTTPS in front

Run a reverse proxy (Caddy, nginx, or the host's load balancer) on 443 forwarding to port
8000; the API trusts `X-Forwarded-For` for rate limiting (`--proxy-headers`). Don't expose
PostgreSQL publicly (compose does not publish port 5432).

## 7. Checks on every push (GitHub Actions)

`.github/workflows/tests.yml` runs the full test suite against a throwaway PostgreSQL and
builds the Docker image. A red check means do not deploy that commit.

## Public demo notes

Apex21 is an independent fan project. `/about` carries the disclaimer (not affiliated with
Formula 1, the FIA or any team) and how the predictions are tested; keep it visible on the
site. FastF1 / OpenF1 data comes from unofficial feeds for non-commercial use -- check their
terms before any commercial use.
