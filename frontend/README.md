# Apex21 frontend

React + TypeScript + Vite. Pages:

- `/season/:year` – every race of a season with its winner, or the forecast winner if not run yet
- `/race/:id` – forecast (vs the actual result once run), predicted tyre strategy with its tested
  accuracy, what each driver actually ran, and the what-if simulator (Safety Car / VSC laps, one
  driver's strategy)

## Run (two terminals)

```bash
# terminal 1 – API (repo root, venv active)
uvicorn app.main:app --reload

# terminal 2 – frontend
cd frontend
npm install        # first time only
npm run dev        # http://localhost:5173
```

The dev server forwards `/api/*` to `http://localhost:8000` (set `APEX21_API_URL` to change it).

## Checks

```bash
npm run typecheck
npm test           # node --test on the pure helpers in src/format.ts
npm run build
```
