import { Link, useParams } from 'react-router-dom'

import { api } from '../api'
import { LapChart } from '../charts/LapChart'
import { RaceTrace } from '../charts/RaceTrace'
import { DataNotes, ErrorBox } from '../components/Status'
import { WhatIfPanel } from '../components/WhatIfPanel'
import { compoundColor, compoundLetter, pct } from '../format'
import { useAsync } from '../hooks'
import { driverCode } from '../names'
import { IdealLap } from '../race/IdealLap'
import { LongRuns } from '../race/LongRuns'
import { PredictionTower } from '../race/PredictionTower'
import { QualifyingTable } from '../race/QualifyingTable'
import { StrategyLane } from '../race/StrategyLane'
import { glowColour, normaliseHex } from '../theme/color'
import type { ActualDriverStrategy, Forecast, RaceResult, WinnerPrediction } from '../types'

export function RacePage() {
  const raceId = Number(useParams().id)
  const race = useAsync(() => api.race(raceId), [raceId])
  const forecast = useAsync(() => api.forecast(raceId), [raceId])
  const winner = useAsync(() => api.winnerPrediction(raceId), [raceId])
  const r = race.data
  const finished = (r?.results.length ?? 0) > 0
  const battle = useAsync(() => (finished ? api.positionBattle(raceId) : Promise.resolve(null)), [raceId, finished])
  const trace = useAsync(() => (finished ? api.raceTrace(raceId) : Promise.resolve(null)), [raceId, finished])
  const actual = useAsync(() => (finished ? api.actualStrategy(raceId) : Promise.resolve(null)), [raceId, finished])
  const strategy = useAsync(() => api.winningStrategy(raceId), [raceId])
  const quali = useAsync(() => api.qualifying(raceId), [raceId])
  const longRuns = useAsync(() => api.longRuns(raceId), [raceId])

  if (race.loading) return <div className="page"><div className="skeleton" style={{ height: 160 }} /></div>
  if (race.error || !r) return <div className="page"><ErrorBox error={race.error ?? new Error('Race not found')} /></div>

  const f = forecast.data
  const w = winner.data
  const totalLaps = strategy.data?.total_laps ?? f?.total_laps ?? null
  const date = new Date(`${r.race_date}T12:00:00`)

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <span className="kicker"><b>Round {r.round_number}</b> · {r.season_year}{r.weekend_format && r.weekend_format !== 'conventional' ? ' · Sprint weekend' : ''}</span>
          <h1>{r.track_name}</h1>
          <div className="sub">
            {date.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
            {r.country && ` · ${r.country}`}{totalLaps ? ` · ${totalLaps} laps` : ''}{r.length_km ? ` · ${Number(r.length_km).toFixed(3)} km lap` : ''}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span className={`badge ${finished ? 'high' : 'medium'}`}>{finished ? 'Result' : 'Upcoming'}</span>
          <Link to={`/results/${r.season_year}`} className="btn">← {r.season_year} results</Link>
        </div>
      </div>

      {finished ? (
        <>
          <div className="grid-main">
            <div className="panel">
              <div className="panel-head"><h3>Race classification</h3><span className="aside">{r.results.length} cars</span></div>
              <Classification results={r.results} />
            </div>
            <div className="stack">
              {w && <PredictionVerdict prediction={w} results={r.results} forecast={f ?? null} />}
              {strategy.data?.prediction && totalLaps && (
                <div className="panel">
                  <div className="panel-head"><h3>Predicted winning strategy</h3><span className="aside">front of the grid</span></div>
                  <div className="panel-body">
                    <StrategyLane plan={strategy.data.prediction} alternative={strategy.data.prediction.alternative} totalLaps={totalLaps}
                                  stopChances={strategy.data.prediction.stop_chances} />
                  </div>
                </div>
              )}
            </div>
          </div>

          {trace.data && trace.data.drivers.length > 0 && (
            <>
              <div className="section-head" style={{ marginTop: 26 }}><h2>Race trace</h2><span className="aside">gap to the leader, lap by lap</span></div>
              <RaceTrace data={trace.data} />
            </>
          )}

          {battle.data && battle.data.results.some((d) => d.positions.length) && (
            <>
              <div className="section-head" style={{ marginTop: 26 }}><h2>Lap chart</h2><span className="aside">position on every lap</span></div>
              <div className="panel panel-pad"><LapChart battle={battle.data} /></div>
            </>
          )}

          {actual.data && actual.data.results.length > 0 && (
            <>
              <div className="section-head" style={{ marginTop: 26 }}><h2>Tyre strategies</h2><span className="aside">what each car ran</span></div>
              <div className="panel panel-pad"><TyreStrategies drivers={actual.data.results} totalLaps={totalLaps ?? 0} /></div>
            </>
          )}
        </>
      ) : (
        <div className="grid-main">
          <div className="panel">
            <div className="panel-head"><h3>Race win probability</h3><span className="aside">{w ? (w.stage === 'post_qualifying' ? 'after qualifying' : 'before qualifying') : ''}</span></div>
            {w ? (
              <PredictionTower stageKey={w.stage} rows={[...(w.stages[w.stage]?.drivers ?? [])].sort((a, b) => b.p_win - a.p_win).map((d) => ({
                id: d.driver_id, name: d.driver_name ?? '?', code: driverCode(d.driver_name ?? '???'), team: d.team_name, colour: d.color_hex ?? null,
                grid: d.grid, pWin: d.p_win, pPodium: d.p_podium,
                predicted: f?.drivers.find((x) => x.driver_id === d.driver_id)?.predicted_position ?? null, change: null,
                strategy: f?.drivers.find((x) => x.driver_id === d.driver_id)?.strategy ?? null,
              }))} />
            ) : <div className="empty">{winner.loading ? 'Loading…' : 'No prediction for this race yet.'}</div>}
          </div>
          <div className="stack">
            {strategy.data?.prediction && totalLaps && (
              <div className="panel">
                <div className="panel-head"><h3>Predicted winning strategy</h3><span className="aside">front of the grid</span></div>
                <div className="panel-body">
                  <StrategyLane plan={strategy.data.prediction} alternative={strategy.data.prediction.alternative} totalLaps={totalLaps}
                                stopChances={strategy.data.prediction.stop_chances} />
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {(quali.data?.results.length || longRuns.data?.drivers.length) ? (
        <>
          <div className="section-head" style={{ marginTop: 26 }}><h2>Weekend sessions</h2><span className="aside">qualifying and Friday pace</span></div>
          <div className="cols-2" style={{ alignItems: 'start' }}>
            {quali.data?.results.length ? (
              <div className="panel">
                <div className="panel-head"><h3>Qualifying</h3><span className="aside">Q1 · Q2 · Q3</span></div>
                <QualifyingTable rows={quali.data.results} />
              </div>
            ) : <div />}
            {longRuns.data && (
              <div className="panel">
                <div className="panel-head"><h3>Practice long runs</h3><span className="aside">race-trim pace</span></div>
                <div className="panel-body"><LongRuns data={longRuns.data} /></div>
              </div>
            )}
          </div>
        </>
      ) : null}

      <IdealLap raceId={raceId} />

      {f && (
        <details className="panel" style={{ marginTop: 26 }}>
          <summary className="panel-head" style={{ cursor: 'pointer' }}><h3>What if…? Safety Car and strategy scenarios</h3><span className="aside">open</span></summary>
          <div className="panel-body">
            <WhatIfPanel raceId={r.id} forecast={f} />
            <DataNotes notes={f.data_notes} />
          </div>
        </details>
      )}
    </div>
  )
}

function Classification({ results }: { results: RaceResult[] }) {
  return (
    <div className="table-wrap">
      <table className="timing">
        <thead>
          <tr><th>Pos</th><th>Driver</th><th className="r hide-sm">Grid</th><th className="r hide-sm">+/-</th><th>Time / status</th><th className="r">Pts</th></tr>
        </thead>
        <tbody>
          {results.map((x) => {
            const gained = x.finishing_position != null && x.starting_grid_position ? x.starting_grid_position - x.finishing_position : null
            return (
              <tr key={x.driver_id}>
                <td className="pos-cell">
                  <span className={`pos-box ${x.finishing_position && x.finishing_position <= 3 ? `p${x.finishing_position}` : ''}`}>
                    {x.finishing_position ?? '–'}
                  </span>
                </td>
                <td>
                  <div className="driver-cell">
                    <span className="team-bar" style={{ background: glowColour(normaliseHex(x.color_hex, '#888')) }} />
                    <span className="code">{driverCode(x.driver_name)}</span>
                    <span className="who"><span className="name">{x.driver_name}</span><span className="team">{x.team_name}</span></span>
                  </div>
                </td>
                <td className="r num hide-sm">{x.starting_grid_position ?? '–'}</td>
                <td className="hide-sm r">
                  {gained != null && <span className={`delta ${gained > 0 ? 'up' : gained < 0 ? 'down' : 'flat'}`}>{gained === 0 ? '–' : gained > 0 ? `▲${gained}` : `▼${-gained}`}</span>}
                </td>
                <td className="num muted">{x.display ?? x.status ?? ''}</td>
                <td className="r num" style={{ fontWeight: 700 }}>{x.points ? Number(x.points) : ''}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** What we predicted against what happened, for a race that has been run. */
function PredictionVerdict({ prediction, results, forecast }: { prediction: WinnerPrediction; results: RaceResult[]; forecast: Forecast | null }) {
  const stage = prediction.stages.post_qualifying ? 'post_qualifying' : 'pre_qualifying'
  const drivers = [...(prediction.stages[stage]?.drivers ?? [])].sort((a, b) => b.p_win - a.p_win)
  const fav = drivers[0]
  const winner = results.find((x) => x.finishing_position === 1)
  const winnerRank = winner ? drivers.findIndex((d) => d.driver_id === winner.driver_id) + 1 : 0
  const favFinish = fav ? results.find((x) => x.driver_id === fav.driver_id) : undefined
  const actual = new Map(results.map((x) => [x.driver_id, x.finishing_position]))
  const errors = (forecast?.drivers ?? []).map((d) => {
    const a = actual.get(d.driver_id)
    return a != null ? Math.abs(a - d.predicted_position) : null
  }).filter((e): e is number => e != null)
  const mae = errors.length ? errors.reduce((a, b) => a + b, 0) / errors.length : null
  if (!fav) return null
  return (
    <div className="panel">
      <div className="panel-head"><h3>Prediction vs result</h3><span className="aside">{stage === 'post_qualifying' ? 'after qualifying' : 'before qualifying'}</span></div>
      <div className="record-list">
        <div>
          <div className="cap">Our favourite</div>
          <div className="big" style={{ fontSize: '1.5rem', marginTop: 6 }}>{fav.driver_name}</div>
          <div className="small muted">{pct(fav.p_win, 0)} to win · finished {favFinish?.finishing_position ? `P${favFinish.finishing_position}` : favFinish?.status ?? '–'}</div>
        </div>
        <div>
          <div className="cap">Winner</div>
          <div className="big" style={{ fontSize: '1.5rem', marginTop: 6 }}>{winner?.driver_name ?? '–'}</div>
          <div className={`small ${winnerRank === 1 ? 'hit' : 'muted'}`}>{winnerRank ? `our #${winnerRank} · ${pct(drivers[winnerRank - 1].p_win, 0)} to win` : 'not in our list'}</div>
        </div>
        {mae != null && (
          <div>
            <div className="cap">Finishing order</div>
            <div className="big">{mae.toFixed(1)}<small>places</small></div>
            <div className="small muted">average miss of the predicted positions</div>
          </div>
        )}
        {forecast && (
          <div>
            <div className="cap">Model test average</div>
            <div className="big">{forecast.validation.predicted_position.mean_abs_error_places.toFixed(1)}<small>places</small></div>
            <div className="small muted">grid order alone: {forecast.validation.predicted_position.grid_only_mean_abs_error_places.toFixed(1)}</div>
          </div>
        )}
      </div>
    </div>
  )
}

function TyreStrategies({ drivers, totalLaps }: { drivers: ActualDriverStrategy[]; totalLaps: number }) {
  const laps = Math.max(totalLaps, ...drivers.flatMap((d) => d.stints.map((s) => s.end_lap)))
  const sorted = [...drivers].sort((a, b) => (a.finishing_position ?? 99) - (b.finishing_position ?? 99))
  return (
    <div>
      {sorted.map((d) => (
        <div key={d.driver_id} style={{ display: 'grid', gridTemplateColumns: '34px 46px 1fr', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <span className="num dim small">{d.finishing_position ? `P${d.finishing_position}` : '–'}</span>
          <span className="code" style={{ borderLeft: `3px solid ${glowColour(normaliseHex(d.color_hex, '#888'))}`, paddingLeft: 6 }}>{driverCode(d.driver_name)}</span>
          <div className="lane" style={{ marginTop: 0, height: 18 }}
               aria-label={d.stints.map((s) => `${s.compound ?? 'unknown'} laps ${s.start_lap}-${s.end_lap}`).join(', ')} role="img">
            {d.stints.map((s) => (
              <div key={s.start_lap} className="stint" title={`${s.compound ?? 'Unknown'}: laps ${s.start_lap}–${s.end_lap}`}
                   style={{ left: `${((s.start_lap - 1) / laps) * 100}%`, width: `${((s.end_lap - s.start_lap + 1) / laps) * 100}%`, background: compoundColor(s.compound), fontSize: '0.6rem' }}>
                {compoundLetter(s.compound)}
              </div>
            ))}
          </div>
        </div>
      ))}
      <div className="lap-axis" style={{ marginLeft: 88 }}><span>L1</span><span>L{Math.round(laps / 2)}</span><span>L{laps}</span></div>
      <div className="legend" style={{ marginTop: 12 }}>
        {['SOFT', 'MEDIUM', 'HARD', 'INTERMEDIATE', 'WET'].map((c) => (
          <span key={c}><i style={{ background: compoundColor(c) }} />{c.toLowerCase()}</span>
        ))}
        <span className="dim">Two stints on the same compound show as one.</span>
      </div>
    </div>
  )
}
