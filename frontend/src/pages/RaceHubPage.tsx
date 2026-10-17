import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api'
import type { Circuit } from '../circuitTypes'
import { CircuitPhotoBg, useCircuitPhoto } from '../components/CircuitPhoto'
import { Countdown } from '../components/Countdown'
import { Empty, SessionWait, Skeleton } from '../components/Empty'
import { ErrorBox } from '../components/Status'
import { CircuitOutline } from '../fx/CircuitOutline'
import { localDay, nightRace, pct, sessionStates } from '../format'
import { useAsync, useLive } from '../hooks'
import { useLiveWeekend } from '../live'
import { driverCode } from '../names'
import { LongRuns } from '../race/LongRuns'
import { PreStartPlans } from '../race/PreStartPlans'
import { PredictionTower, type TowerRow } from '../race/PredictionTower'
import { QualifyingTable } from '../race/QualifyingTable'
import { SlopeChart } from '../race/SlopeChart'
import { StrategyLane } from '../race/StrategyLane'
import { WinShare } from '../race/WinShare'
import { usePageAccent } from '../theme/accent'
import { bestMatch } from '../trackMatch'
import type { LiveRecord, PredictionStage, WinnerPrediction } from '../types'
import { LightsOut } from '../components/LightsOut'
import { swipeTarget } from '../motion/swipe'
import { useSwipe } from '../motion/useSwipe'

const STAGE_LABEL: Record<PredictionStage, string> = { pre_qualifying: 'Before qualifying', post_qualifying: 'After qualifying' }
const STAGES: PredictionStage[] = ['pre_qualifying', 'post_qualifying']

function rankings(p: WinnerPrediction, stage: PredictionStage): Map<number, number> {
  const drivers = [...(p.stages[stage]?.drivers ?? [])].sort((a, b) => b.p_win - a.p_win)
  return new Map(drivers.map((d, i) => [d.driver_id, i + 1]))
}

export function RaceHubPage() {
  const { prediction, sessions } = useLiveWeekend()
  const p = prediction.data
  const raceId = p?.race_id
  const [picked, setPicked] = useState<PredictionStage | null>(null)
  const stage: PredictionStage | null = p ? (picked && p.stages[picked] ? picked : p.stage) : null
  const forecast = useLive(() => (raceId ? api.forecast(raceId) : Promise.resolve(null)), [raceId], 120_000)
  const strategy = useLive(() => (raceId ? api.winningStrategy(raceId) : Promise.resolve(null)), [raceId], 300_000)
  const record = useAsync(() => api.liveRecord(), [])
  const quali = useLive(() => (raceId ? api.qualifying(raceId) : Promise.resolve(null)), [raceId], 120_000)
  const longRuns = useLive(() => (raceId ? api.longRuns(raceId) : Promise.resolve(null)), [raceId], 300_000)
  const preStart = useLive(() => (raceId ? api.preStart(raceId) : Promise.resolve(null)), [raceId], 120_000)
  const [circuit, setCircuit] = useState<Circuit | null>(null)
  const raceInfo = useAsync(() => (raceId ? api.race(raceId) : Promise.resolve(null)), [raceId])
  const trackId = raceInfo.data?.track_id
  const weather = useLive(() => (trackId && p ? api.strategyPrediction(trackId, p.race_date).catch(() => null) : Promise.resolve(null)),
    [trackId, p?.race_date], 1_800_000)
  const photo = useCircuitPhoto({ track: p?.track })

  useEffect(() => {
    if (!p) return
    let live = true
    import('../circuits').then(({ CIRCUITS }) => {
      if (live) setCircuit(bestMatch(CIRCUITS, p.track))
    })
    return () => {
      live = false
    }
  }, [p?.track])

  const current = p && stage ? p.stages[stage] : undefined
  usePageAccent(current?.favourite.color_hex ?? null)
  // phones: swipe the prediction between before and after qualifying
  const board = useRef<HTMLDivElement>(null)
  useSwipe(board, (dir) => {
    const next = p && stage ? swipeTarget(STAGES, stage, dir, (s) => !!p.stages[s]) : null
    if (next) setPicked(next)
  }, !!current)

  const rows: TowerRow[] = useMemo(() => {
    if (!p || !stage || !current) return []
    const other = STAGES.find((s) => s !== stage && p.stages[s])
    const before = other ? rankings(p, other) : null
    const now = rankings(p, stage)
    const fc = stage === 'post_qualifying' && forecast.data?.race_id === p.race_id ? forecast.data : null
    const byDriver = new Map((fc?.drivers ?? []).map((d) => [d.driver_id, d]))
    return [...current.drivers]
      .sort((a, b) => b.p_win - a.p_win)
      .map((d) => {
        const f = byDriver.get(d.driver_id)
        const prev = before?.get(d.driver_id)
        const rank = now.get(d.driver_id)!
        return {
          id: d.driver_id, name: d.driver_name ?? '?', code: driverCode(d.driver_name ?? '???'), team: d.team_name,
          colour: d.color_hex ?? null, grid: d.grid, pWin: d.p_win, pPodium: d.p_podium,
          predicted: f?.predicted_position ?? null,
          // compared with the earlier stage only, so "after qualifying" shows what qualifying changed
          change: other && stage === 'post_qualifying' && prev != null ? prev - rank : null,
          strategy: f?.strategy ?? null,
        }
      })
  }, [p, stage, current, forecast.data])

  if (prediction.loading) return <div className="page"><LightsOut height={180} /></div>
  if (prediction.error) return <div className="page"><ErrorBox error={prediction.error} /></div>
  if (!p || !stage || !current) {
    return (
      <div className="page">
        <Empty panel icon="flag" title="No upcoming race in the calendar">
          The season is over or the next calendar is not out yet. <Link to="/results">See the season results</Link>.
        </Empty>
      </div>
    )
  }

  const fav = current.favourite
  const conf = current.confidence
  const fc = forecast.data?.race_id === p.race_id ? forecast.data : null
  const totalLaps = strategy.data?.total_laps ?? fc?.total_laps ?? null
  const plan = strategy.data?.prediction
  const s = sessions.data
  const raceDate = new Date(`${p.race_date}T12:00:00`)
  const states = s ? sessionStates(s.sessions, s.race_date, s.weekend_format, s.season, localDay()) : []
  const qualiWaiting = s ? s.sessions.some((x, i) => x.type === 'Q' && states[i] === 'waiting') : false

  return (
    <div className="page">
      <section className={`event-hero ${nightRace(p.track) ? 'night' : ''} ${weather.data?.weather?.rain_expected ? 'rain' : ''}`}>
        <CircuitPhotoBg photo={photo} />
        <div className="hero-fx" aria-hidden="true"><i className="flood l" /><i className="flood r" /><i className="rainfall" /><i className="trace-line" /></div>
        <div className="hero-copy">
          <div className="hero-chips">
            {nightRace(p.track) && <span className="hero-chip night">Night race</span>}
            {weather.data?.weather?.rain_expected && <span className="hero-chip rain">Rain forecast</span>}
          </div>
          <span className="kicker"><b>Round {p.round}</b> · {p.season} season{s?.weekend_format && s.weekend_format !== 'conventional' ? ' · Sprint weekend' : ''}</span>
          <h1>{p.track}</h1>
          <div className="event-meta">
            <div><span>Race</span><b>{raceDate.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}</b></div>
            {totalLaps && <div><span>Laps</span><b>{totalLaps}</b></div>}
            {circuit?.lengthM && <div><span>Lap length</span><b>{(circuit.lengthM / 1000).toFixed(3)} km</b></div>}
            <div><span>Apex21 favourite</span><b>{fav.driver_name} · {pct(fav.p_win, 0)}</b></div>
            <div><span>Prediction</span><b>{STAGE_LABEL[stage]}</b></div>
          </div>
        </div>
        <div className="event-side">
          <div className="event-map">{circuit && <CircuitOutline circuit={circuit} sectors width={9} />}</div>
          {s && <Countdown sessions={s.sessions} />}
        </div>
      </section>

      {p.stage === 'pre_qualifying' && qualiWaiting && (
        <div className="notice" style={{ marginBottom: 14 }}>
          Qualifying has run but its data is not in the database yet. This page switches to the
          after-qualifying prediction by itself as soon as the pipeline loads it.
        </div>
      )}

      <div className="hub-bar">
        <div className="tabs" role="tablist" aria-label="Prediction stage">
          {STAGES.map((st) => (
            <button key={st} role="tab" aria-selected={stage === st} className={stage === st ? 'on' : ''}
                    disabled={!p.stages[st]} onClick={() => setPicked(st)}>
              {STAGE_LABEL[st]}
            </button>
          ))}
        </div>
        <span className={`badge ${conf.level}`}>Confidence: {conf.level}</span>
        <Link to="/predict" className="btn">Make your picks</Link>
        <span className="spacer" />
        {prediction.updatedAt && (
          <span className="freshness"><i />Checked {new Date(prediction.updatedAt).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}</span>
        )}
      </div>

      <div className="grid-main" ref={board}>
        <div className="panel">
          <div className="panel-head">
            <h3>Race win probability</h3>
            <span className="aside">{current.drivers.length} drivers · ranked by win chance</span>
          </div>
          <PredictionTower rows={rows} stageKey={stage} />
          <div className="panel-foot">{conf.basis}</div>
        </div>

        <div className="stack">
          <div className="panel">
            <div className="panel-head"><h3>Who can win</h3><span className="aside">share of the win chance</span></div>
            <div className="panel-body">
              <WinShare stageKey={stage} slices={current.drivers.map((d) => ({
                id: d.driver_id, code: driverCode(d.driver_name ?? '???'), name: d.driver_name ?? '?', colour: d.color_hex ?? null, p: d.p_win,
              }))} />
            </div>
          </div>

          <div className="panel">
            <div className="panel-head"><h3>Grid → predicted finish</h3><span className="aside">race forecast</span></div>
            <div className="panel-body">
              {fc && stage === 'post_qualifying' ? (
                <SlopeChart lines={fc.drivers.map((d) => ({
                  id: d.driver_id, code: driverCode(d.driver_name), name: d.driver_name, colour: d.color_hex, from: d.grid, to: d.predicted_position,
                }))} />
              ) : (
                <p className="muted small" style={{ margin: 0 }}>
                  The finishing-order forecast is made once the starting grid is known (after qualifying).
                </p>
              )}
            </div>
          </div>

          {preStart.data && totalLaps && Object.keys(preStart.data.plans).length > 0 && (
            <div className="panel">
              <div className="panel-head"><h3>Race-day tyre plans</h3><span className="aside">starting tyres declared</span></div>
              <div className="panel-body"><PreStartPlans data={preStart.data} totalLaps={totalLaps} /></div>
            </div>
          )}

          <div className="panel">
            <div className="panel-head"><h3>Predicted winning strategy</h3><span className="aside">front of the grid</span></div>
            <div className="panel-body">
              {plan && totalLaps ? (
                <>
                  <StrategyLane plan={plan} alternative={plan.alternative} totalLaps={totalLaps} stopChances={plan.stop_chances} />
                  <p className="dim small" style={{ margin: '12px 0 0' }}>
                    {strategy.data?.practice_wear_measured ? 'Stop count from this weekend’s practice tyre wear. ' : 'Practice tyre wear not measured yet: stop count from earlier races. '}
                    A Safety Car can open an earlier, cheaper stop.
                  </p>
                </>
              ) : (
                strategy.loading ? <Skeleton rows={2} /> : <Empty icon="tyre" title="No strategy prediction for this race yet" />
              )}
            </div>
          </div>

          {record.data && <ModelRecord record={record.data} season={p.season} />}
        </div>
      </div>

      <div className="section-head" style={{ marginTop: 28 }}><h2>Weekend so far</h2><span className="aside">updates as sessions load</span></div>
      <div className="cols-2" style={{ alignItems: 'start' }}>
        <div className="panel">
          <div className="panel-head"><h3>Qualifying</h3><span className="aside">Q1 · Q2 · Q3</span></div>
          {quali.data?.results.length ? <QualifyingTable rows={quali.data.results} /> : (
            <div className="panel-body">
              {quali.loading ? <Skeleton rows={5} />
                : <SessionWait sessions={s?.sessions} types={['Q']} what="The Q1, Q2 and Q3 times" none="Qualifying results are not in yet" />}
            </div>
          )}
        </div>
        <div className="panel">
          <div className="panel-head"><h3>Practice long runs</h3><span className="aside">race-trim pace</span></div>
          <div className="panel-body">
            {longRuns.data?.drivers.length ? <LongRuns data={longRuns.data} />
              : longRuns.loading ? <Skeleton rows={4} />
              : <SessionWait sessions={s?.sessions} types={['FP1', 'FP2', 'FP3']} what="Race-trim pace from the long runs" none="No long runs in practice yet" />}
          </div>
        </div>
      </div>
    </div>
  )
}

/** How the frozen pre-race predictions have done: overall and this season's latest calls. */
function ModelRecord({ record, season }: { record: LiveRecord; season: number }) {
  const post = record.summary.post_qualifying
  const pre = record.summary.pre_qualifying
  const calls = record.calls.filter((c) => c.season === season && c.stage === 'post_qualifying').slice(-5).reverse()
  if (!post && !pre) return null
  return (
    <div className="panel">
      <div className="panel-head"><h3>Live record</h3><span className="aside">predictions saved before the race</span></div>
      <div className="record-list">
        {post && (
          <div>
            <div className="big">{post.favourite_won}<small>/ {post.calls}</small></div>
            <div className="cap">Favourite won · after qualifying</div>
          </div>
        )}
        {post && (
          <div>
            <div className="big">{post.winner_in_top3}<small>/ {post.calls}</small></div>
            <div className="cap">Winner in our top 3</div>
          </div>
        )}
      </div>
      {calls.length > 0 && (
        <div className="table-wrap">
        <table className="timing">
          <tbody>
            {calls.map((c) => (
              <tr key={`${c.race_id}-${c.stage}`}>
                <td className="num dim">R{c.round}</td>
                <td>{c.track}</td>
                <td className="code">{driverCode(c.favourite)}</td>
                <td className={c.favourite_won ? 'hit' : 'miss'}>{c.favourite_won ? 'Won' : c.winner ? `${driverCode(c.winner)} won` : '–'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      <div className="panel-foot"><Link to="/results">Full prediction log →</Link></div>
    </div>
  )
}
