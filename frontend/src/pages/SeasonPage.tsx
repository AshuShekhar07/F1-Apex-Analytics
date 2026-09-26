import { Link, useNavigate, useParams } from 'react-router-dom'

import { api } from '../api'
import { DriverName } from '../components/DriverName'
import { ErrorBox, Loading } from '../components/Status'
import { useAsync } from '../hooks'

export const SEASONS = [2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018]

function isPast(iso: string) {
  return new Date(`${iso}T23:59:59`) < new Date()
}

function formatDate(iso: string) {
  return new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

export function SeasonPage() {
  const year = Number(useParams().year ?? SEASONS[0])
  const navigate = useNavigate()
  const { data: races, error, loading } = useAsync(() => api.races(year), [year])

  return (
    <main>
      <div className="page-head">
        <h1>{year} season</h1>
        <label className="season-select">
          Season{' '}
          <select value={year} onChange={(e) => navigate(`/season/${e.target.value}`)}>
            {SEASONS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
      </div>
      {loading && <Loading what="races" />}
      {error && <ErrorBox error={error} />}
      {races && races.length === 0 && <p className="muted">No races stored for {year}.</p>}
      <div className="race-grid">
        {races?.map((r) => (
          <Link key={r.id} to={`/race/${r.id}`} className="race-card">
            <div className="race-card-top">
              <span className="round">R{r.round_number}</span>
              <span className="muted small">{formatDate(r.race_date)}</span>
            </div>
            <h3>{r.track_name}</h3>
            <div className="muted small">{r.country}{r.weekend_format === 'sprint' ? ' · Sprint weekend' : ''}</div>
            <div className="race-card-bottom">
              {r.winner_name ? (
                <>
                  <span className="label">Winner</span>
                  <DriverName name={r.winner_name} color={r.winner_color_hex} />
                </>
              ) : r.predicted_winner_name ? (
                <>
                  <span className="label forecast">Forecast</span>
                  <DriverName name={r.predicted_winner_name} color={r.predicted_winner_color_hex} />
                </>
              ) : isPast(r.race_date) ? (
                <span className="small pending">Race run: results not loaded yet</span>
              ) : (
                <span className="muted small">Upcoming: forecast after qualifying</span>
              )}
            </div>
          </Link>
        ))}
      </div>
    </main>
  )
}
