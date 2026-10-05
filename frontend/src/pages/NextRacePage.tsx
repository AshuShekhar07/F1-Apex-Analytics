import { Link } from 'react-router-dom'

import { api } from '../api'
import { ErrorBox, Loading } from '../components/Status'
import { WinnerPredictionPanel } from '../components/WinnerPredictionPanel'
import { useAsync } from '../hooks'

export function NextRacePage() {
  const prediction = useAsync(() => api.nextWinnerPrediction(), [])
  const p = prediction.data
  return (
    <main>
      <div className="page-head">
        <div>
          <h1>{p ? `Next race: ${p.track}` : 'Next race'}</h1>
          {p && (
            <div className="muted">
              {p.season} · Round {p.round} · {new Date(`${p.race_date}T12:00:00`).toLocaleDateString(undefined, { dateStyle: 'long' })}
            </div>
          )}
        </div>
        {p && <Link to={`/race/${p.race_id}`} className="back">Race page →</Link>}
      </div>
      <section>
        <h2>Who will win?</h2>
        {prediction.loading && <Loading what="prediction" />}
        {prediction.error && <ErrorBox error={prediction.error} />}
        {!prediction.loading && !prediction.error && !p && <p className="muted">No upcoming race in the calendar.</p>}
        {p && <WinnerPredictionPanel prediction={p} />}
      </section>
    </main>
  )
}
