import { useNavigate, useParams } from 'react-router-dom'

import { api } from '../api'
import { ConfidenceBadge, ConfidenceBasis } from '../components/Confidence'
import { DriverName } from '../components/DriverName'
import { ErrorBox, Loading } from '../components/Status'
import { TitleChart } from '../components/TitleChart'
import { pct, roundLabel, topTitleSeries } from '../format'
import { useAsync } from '../hooks'
import type { Championship, ChampionshipHistory, TitleEntry } from '../types'
import { SEASONS } from './SeasonPage'

const KINDS = [
  { key: 'drivers', title: "Drivers' title" },
  { key: 'constructors', title: "Constructors' title" },
] as const

function entryName(e: TitleEntry) {
  return e.driver_name ?? e.team_name ?? 'Unknown'
}

function TitleSection({ kind, title, champ, history }: {
  kind: 'drivers' | 'constructors'
  title: string
  champ: Championship
  history: ChampionshipHistory | null | undefined
}) {
  const entries = champ[kind].filter((e) => e.p_title >= 0.0005 || e.points_now > 0).slice(0, 8)
  const conf = champ.confidence[kind]
  const series = history ? topTitleSeries(history[kind], 4) : []
  return (
    <section>
      <div className="section-head">
        <h2>{title}</h2>
        <ConfidenceBadge level={conf.level} />
      </div>
      <ConfidenceBasis lines={[conf.basis, conf.chance_basis]} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{kind === 'drivers' ? 'Driver' : 'Team'}</th>
              <th>Title chance</th>
              <th>Points now</th>
              <th>Expected final</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.driver_id ?? e.team_id}>
                <td>
                  <DriverName name={entryName(e)} color={e.color_hex} team={kind === 'drivers' ? e.team_name : null} />
                </td>
                <td>
                  <div className="prob"><div className="prob-fill" style={{ width: `${e.p_title * 100}%` }} /><span>{pct(e.p_title, 1)}</span></div>
                </td>
                <td className="pos">{Math.round(e.points_now)}</td>
                <td className="muted">{Math.round(e.expected_points)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {series.length > 0 && <TitleChart series={series} kind={kind} title={`${title}: chance after each round`} />}
    </section>
  )
}

export function ChampionshipPage() {
  const year = Number(useParams().year ?? SEASONS[0])
  const navigate = useNavigate()
  const champ = useAsync(() => api.championship(year), [year])
  const history = useAsync(() => api.championshipHistory(year), [year])
  const c = champ.data

  return (
    <main>
      <div className="page-head">
        <div>
          <h1>{year} championships</h1>
          {c && (
            <div className="muted">
              {roundLabel(c.after_round)} · {c.weekends_left} race weekend{c.weekends_left === 1 ? '' : 's'} left
            </div>
          )}
        </div>
        <label className="season-select">
          Season{' '}
          <select value={year} onChange={(e) => navigate(`/championship/${e.target.value}`)}>
            {SEASONS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
      </div>
      {champ.loading && <Loading what="title chances" />}
      {champ.error && <ErrorBox error={champ.error} />}
      {!champ.loading && !champ.error && !c && <p className="muted">No title projection for {year} yet.</p>}
      {c && KINDS.map((k) => <TitleSection key={k.key} kind={k.key} title={k.title} champ={c} history={history.data} />)}
      {c && (
        <p className="accuracy">
          <strong>How it works.</strong> Points so far plus every remaining race simulated 20,000 times with the real
          points system, each car and driver rated from earlier results only. Tested on every round of 2018–2025:
          when it gave a driver 90%+ for the title, that driver became champion in 80 of 80 cases; for constructors
          87.7% of 106. Confidence is measured on earlier seasons at the same stage and with a similar chance.
        </p>
      )}
    </main>
  )
}
