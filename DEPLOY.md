# Deploying Apex21

Everything runs as four containers:

| service     | what it does                                                         |
|-------------|----------------------------------------------------------------------|
| `db`        | PostgreSQL 16 with all race data, forecasts and the live record      |
| `api`       | FastAPI on port 8000 (`/health`, `/about`, forecasts, strategy, ...)  |
| `scheduler` | `run_pipeline.py --loop`: loads new races and forecasts them unattended |
| `web`       | the website (nginx) on port 80; it forwards `/api/...` to the `api` container |

The website and its data share one address, so no CORS setup is needed.

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
```

Optional: `WEB_PORT` (website, default 80), `API_PORT` (the API on its own, default 8000),
`CORS_ORIGINS` (only if a site on another domain calls the API directly).

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
curl http://localhost:8000/health          # the API
curl -I http://localhost/                  # the website (WEB_PORT)
docker compose exec scheduler python run_pipeline.py   # load anything newer than your dump now
docker compose exec api python api_check_v1.py --deep  # every API route against the real data
```

Then open `http://<server>/` in a browser.

`/health` says `ok` once a pipeline run has succeeded; `degraded` lists the problem (a failed
run, or no run for a week); HTTP 503 means the database is unreachable. Point an uptime
monitor at it.

## 5. Day to day

* The scheduler checks every 10 minutes. When a session of the race weekend has finished and its
  data should be out (FastF1's calendar: start + about 1.5 h, the race 3 h) but the database
  does not have it complete (laps or results; a race or sprint with every official point; a
  qualifying with lap times), it runs the update **every 30 minutes**, for up to 12 hours. Otherwise
  it runs **every 2 h on race weekends, daily otherwise**. Runs never overlap (database lock).
  History: `pipeline_runs` table, logs in the `pipelinelogs` volume
  (`docker compose exec scheduler ls logs`).
* Run an update by hand: `docker compose exec scheduler python run_pipeline.py`
* Weekly report: `docker compose exec api python apex_report.py --no-wait`
* Back up: `docker compose exec db pg_dump -Fc -U apex21 apex21 > backup_$(date +%F).dump`
* Upgrade: `git pull && docker compose up -d --build`
  Visitors need not do anything: while the API restarts, pages keep retrying for about a minute (a
  "reconnecting" bar) and fill in by themselves; pages left open switch to the new version at the next
  page change (at once in a background tab), using the build id in `/version.json`.
* Car photos (Liveries page): on a machine with internet, `python fetch_livery_photos_v1.py`, then commit
  `frontend/public/livery-photos/` and rebuild. It keeps only freely licensed Wikimedia Commons photos and
  stores each one's author and licence, which the page shows. Without it the page draws each car in its colours.

## 6. Put HTTPS in front

Run a reverse proxy (Caddy, nginx, or the host's load balancer) on 443 forwarding to the
website port (`WEB_PORT`, 80); the website passes `/api` on to the API. The API rate-limits
per visitor using `X-Forwarded-For` (600 requests a minute; `RATE_LIMIT_PER_MINUTE`). Don't
expose PostgreSQL publicly (compose does not publish port 5432); the API port only needs to
be open if something other than the website calls it.

## 7. Checks on every push (GitHub Actions)

`.github/workflows/tests.yml` runs the full test suite against a throwaway PostgreSQL and
builds the Docker image. A red check means do not deploy that commit.

## Free hosting without a card

No server of your own: each part goes to a free plan that needs no credit card. The site works
the same; the costs are a slow first visit after the API has been idle and a small CPU.

| part     | free host (no card)                    | limits that matter                                              |
|----------|----------------------------------------|-----------------------------------------------------------------|
| database | Neon                                   | 1 GB per project; sleeps after 5 min idle (wakes in about a second) |
| API      | Render web service (Docker, `render.yaml`) | 512 MB, 0.1 CPU, one worker; sleeps after 15 min idle (about 1 min to wake) |
| website  | Cloudflare Pages                       | static files, no practical limit                                 |
| pipeline | GitHub Actions (`.github/workflows/pipeline.yml`) | free on a public repo; schedules run from the default branch only |

**0. Check the database fits (1 GB).** On your computer:
`psql "$DATABASE_URL" -c "SELECT pg_size_pretty(pg_database_size(current_database()));"`

**1. Database (Neon).** Sign up at neon.tech, create a project (Postgres 16), and copy its
connection string with connection pooling switched **off** (the pipeline holds an advisory lock).
Then, on your computer: `ops/db_dump.sh`, then `ops/db_restore.sh apex21_*.dump "<neon string>"`.

**2. API (Render).** Sign up at render.com with GitHub. New → Blueprint → this repository (it reads
`render.yaml`), or New → Web Service → Docker, branch `feature/frontend`, plan Free. Set
`DATABASE_URL` to the Neon string; leave `CORS_ORIGINS` for step 4. When it is live,
`https://<service>.onrender.com/health` answers `ok` (or `degraded` with the reason).

**3. Website (Cloudflare Pages).** Workers & Pages → Create → Pages → connect the repository.
Branch `feature/frontend`, root directory `frontend`, build command `npm run build`, output
directory `dist`, environment variables `VITE_API_BASE=https://<service>.onrender.com` and
`NODE_VERSION=22`. The site gets an address like `https://apex21.pages.dev`. Pages serves the app for
any path (no `404.html`), and `frontend/public/_headers` sets the same caching as `nginx.conf`.

**4. Let the website call the API.** In Render, set `CORS_ORIGINS=https://apex21.pages.dev` (your
Pages address, plus any custom domain, comma-separated). Render restarts the API.

**5. Pipeline (GitHub Actions).** Repository → Settings → Secrets and variables → Actions → New
secret `DATABASE_URL` = the Neon string. `pipeline.yml` must be on the default branch (`main`) to
run on its schedule; once it is, Actions → pipeline → Run workflow (tick "Load now") tests it.
GitHub turns schedules off after 60 days without any repository activity; re-enable them in the
Actions tab.

**6. Optional: no slow first visit.** A free uptime monitor (e.g. UptimeRobot) calling
`https://<service>.onrender.com/` every 5 minutes keeps the API awake. That address does not
touch the database, so Neon can still sleep. One always-on service fits Render's 750 free hours
a month.

## Public demo notes

Apex21 is an independent fan project. `/about` carries the disclaimer (not affiliated with
Formula 1, the FIA or any team) and how the predictions are tested; keep it visible on the
site. FastF1 / OpenF1 data comes from unofficial feeds for non-commercial use -- check their
terms before any commercial use.
