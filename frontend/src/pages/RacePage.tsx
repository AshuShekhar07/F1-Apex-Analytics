import { Link, useParams } from 'react-router-dom'

import { api } from '../api'
import { ForecastTable, ResultsTable } from '../components/ForecastTable'
import { DataNotes, ErrorBox, Loading } from '../components/Status'
import { ActualStrategies, PredictedStrategy } from '../components/StrategySection'
import { WhatIfPanel } from '../components/WhatIfPanel'
import { WinnerPredictionPanel } from '../components/WinnerPredictionPanel'
import { useAsync } from '../hooks'

export function RacePage() {
  const raceId = Number(useParams().id)
  const race = useAsync(() => api.race(raceId), [raceId])
  const forecast = useAsync(() => api.forecast(raceId), [raceId])
  const actual = useAsync(() => api.actualStrategy(raceId), [raceId])
  const winner = useAsync(() => api.winnerPrediction(raceId), [raceId])
  const r = race.data
  const strategy = useAsync(
    () => (r ? api.strategyPrediction(r.track_id, r.race_date) : Promise.resolve(null)),
    [r?.track_id, r?.race_date],
  )

  if (race.loading) return <main><Loading what="race" /></main>
  if (race.error || !r) return <main><ErrorBox error={race.error ?? new Error('Race not found')} /></main>

  const f = forecast.data
  const finished = r.results.length > 0
  const totalLaps = f?.total_laps ?? strategy.data?.total_laps ?? 0
  const actualDrivers = actual.data?.results ?? []

  return (
    <main>
      <Link to={`/season/${r.season_year}`} className="back">← {r.season_year} season</Link>
      <div className="page-head">
        <div>
          <h1>{r.track_name}</h1>
          <div className="muted">
            {r.season_year} · Round {r.round_number} · {new Date(`${r.race_date}T12:00:00`).toLocaleDateString(undefined, { dateStyle: 'long' })}
            {r.country && ` · ${r.country}`}
            {totalLaps > 0 && ` · ${totalLaps} laps`}
          </div>
        </div>
        <span className={`badge ${finished ? 'done' : 'upcoming'}`}>{finished ? 'Finished' : 'Upcoming'}</span>
      </div>

      <section>
        <h2>{finished ? 'Who we expected to win' : 'Who will win?'}</h2>
        {winner.loading && <Loading what="winner prediction" />}
        {winner.error && <ErrorBox error={winner.error} />}
        {winner.data && <WinnerPredictionPanel prediction={winner.data} />}
        {!winner.loading && !winner.error && !winner.data && (
          <p className="muted">Not enough earlier races to predict a winner here.</p>
        )}
      </section>

      <section>
        <h2>{finished ? 'Forecast vs result' : 'Forecast'}</h2>
        {forecast.loading && <Loading what="forecast" />}
        {forecast.error && <ErrorBox error={forecast.error} />}
        {f && (
          <>
            <p className="muted small">
              Uses only races before {f.as_of_date}. Grid from {f.grid_source === 'qualifying' ? 'qualifying' : 'the official starting grid'}.
            </p>
            <ForecastTable forecast={f} results={r.results} />
            <DataNotes notes={f.data_notes} />
          </>
        )}
        {!forecast.loading && !f && (
          <>
            <p className="muted">
              No forecast for this race yet. Forecasts are computed once qualifying is known
              (<code>python race_forecast_v1.py --race-id {r.id}</code>).
            </p>
            {finished && <ResultsTable results={r.results} />}
          </>
        )}
      </section>

      <section>
        <h2>Tyre strategy</h2>
        {strategy.loading && <Loading what="strategy" />}
        {strategy.error && <ErrorBox error={strategy.error} />}
        {strategy.data && <PredictedStrategy prediction={strategy.data} />}
        {!strategy.loading && !strategy.data && !strategy.error && (
          <p className="muted">Not enough earlier races to predict a strategy here.</p>
        )}
        {finished && actualDrivers.length > 0 && (
          <details className="actual-strategies">
            <summary>What each driver actually ran</summary>
            <p className="muted small">From compound changes; two stints on the same compound show as one.</p>
            <ActualStrategies drivers={actualDrivers} totalLaps={totalLaps} />
          </details>
        )}
      </section>

      {f && (
        <section>
          <h2>What if…?</h2>
          <WhatIfPanel raceId={r.id} forecast={f} />
        </section>
      )}
    </main>
  )
}
