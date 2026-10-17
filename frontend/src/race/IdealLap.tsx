import { useRef, useState } from 'react'

import { api } from '../api'
import { useAsync } from '../hooks'
import { driverCode } from '../names'
import { glowColour, normaliseHex } from '../theme/color'
import type { IdealLap as IdealLapData } from '../types'
import { gapScale, headlines, placesInHand, SESSION_LABEL } from './idealLap'
import { lapTime } from './QualifyingTable'

const secs = (v: number | null | undefined) => (v == null ? '–' : v.toFixed(3))

/**
 * Ideal lap: each driver's best sector 1 + 2 + 3 of a session against their real best lap.
 * One row per driver on a shared scale (seconds from pole): a filled dot for the real lap, a
 * ring for the ideal lap, the line between them the time left on the table. Sector times in
 * purple are the fastest of the session. Every value is also printed in the row.
 */
export function IdealLap({ raceId }: { raceId: number }) {
  const [session, setSession] = useState<string | undefined>(undefined)
  const q = useAsync(() => api.idealLap(raceId, session), [raceId, session])
  const last = useRef<IdealLapData | null>(null)
  if (q.data) last.current = q.data
  const data = q.data ?? last.current
  if (!data || !data.sessions_available.length) return null

  const scale = gapScale(data.drivers, data.pole_lap)
  const pole = data.pole_lap ?? null
  const { mostLeft, idealBest } = headlines(data)
  const ultimateBy = (data.ultimate_sectors ?? []).map((s) => (s?.driver_name ? driverCode(s.driver_name) : '–'))

  return (
    <>
      <div className="section-head" style={{ marginTop: 26 }}><h2>Ideal lap</h2><span className="aside">best three sectors vs the real lap</span></div>
      <div className="trace-controls">
        <div className="tabs" role="tablist" aria-label="Session">
          {data.sessions_available.map((s) => (
            <button key={s} role="tab" aria-selected={data.session === s} className={data.session === s ? 'on' : ''}
                    onClick={() => setSession(s)} title={SESSION_LABEL[s]}>{s}</button>
          ))}
        </div>
      </div>
      <div className="panel" style={{ opacity: q.loading ? 0.55 : 1, transition: 'opacity .2s' }}>
        <div className="kv ideal-kv">
          <div>
            <div className="v num">{data.ultimate_lap ? lapTime(data.ultimate_lap) : '–'}</div>
            <div className="k">Ultimate lap · {ultimateBy.join(' / ')}</div>
          </div>
          <div>
            <div className="v num">{pole ? lapTime(pole) : '–'}</div>
            <div className="k">{data.session === 'Q' ? 'Pole lap' : 'Fastest lap'}{data.ultimate_lap && pole ? ` · ${secs(pole - data.ultimate_lap)}s off the ultimate` : ''}</div>
          </div>
          <div>
            <div className="v num">{mostLeft ? `${secs(mostLeft.left_on_table)}s` : '–'}</div>
            <div className="k">Most left on the table{mostLeft ? ` · ${driverCode(mostLeft.driver_name ?? '???')}` : ''}</div>
          </div>
        </div>

        <div className="ideal-rows" role="table" aria-label={`Ideal lap, ${SESSION_LABEL[data.session ?? ''] ?? data.session}`}>
          <div className="ideal-row head" role="row">
            <span role="columnheader">#</span>
            <span role="columnheader">Driver</span>
            <span role="columnheader" className="scale-head">
              <span>Gap to {data.session === 'Q' ? 'pole' : 'the fastest'}</span>
              <span className="hide-sm">● real lap ○ ideal lap</span>
            </span>
            <span role="columnheader" className="r hide-sm">Best lap</span>
            <span role="columnheader" className="r hide-sm">Ideal lap</span>
            <span role="columnheader" className="r">On the table</span>
            <span role="columnheader" className="r hide-md">S1</span>
            <span role="columnheader" className="r hide-md">S2</span>
            <span role="columnheader" className="r hide-md">S3</span>
          </div>
          {data.drivers.map((d) => {
            const c = glowColour(normaliseHex(d.color_hex, '#888888'))
            const bestGap = d.best_lap != null && pole != null ? d.best_lap - pole : null
            const idealGap = d.ideal_lap != null && pole != null ? d.ideal_lap - pole : null
            const a = idealGap != null ? scale.pct(idealGap) : null
            const b = bestGap != null ? scale.pct(bestGap) : null
            const gained = placesInHand(d)
            const summary = `${d.driver_name}: best ${lapTime(d.best_lap)}, ideal ${lapTime(d.ideal_lap)}, ${secs(d.left_on_table)}s left on the table`
            return (
              <div className="ideal-row" role="row" key={d.driver_id} title={summary}>
                <span className="num dim" role="cell">{d.position ?? '–'}</span>
                <span className="who" role="cell">
                  <span className="team-bar" style={{ background: c }} />
                  <b className="code">{driverCode(d.driver_name ?? '???')}</b>
                  <span className="name hide-sm">{d.driver_name}</span>
                </span>
                <span className="dumbbell" role="cell" aria-label={summary}>
                  <i className="pole" style={{ left: `${scale.pct(0)}%` }} />
                  {a != null && b != null && <i className="bar" style={{ left: `${Math.min(a, b)}%`, width: `${Math.abs(b - a)}%`, background: c }} />}
                  {a != null && <i className="ring" style={{ left: `${a}%`, borderColor: c }} />}
                  {b != null && <i className="dot" style={{ left: `${b}%`, background: c }} />}
                </span>
                <span className="r num hide-sm" role="cell">{lapTime(d.best_lap) || '–'}</span>
                <span className="r num hide-sm" role="cell">{lapTime(d.ideal_lap) || '–'}</span>
                <span className="r num" role="cell">
                  <b>{secs(d.left_on_table)}</b>
                  {gained > 0 && <span className="gain" title={`The ideal lap ranks P${d.ideal_rank}`}>▲P{d.ideal_rank}</span>}
                </span>
                {[0, 1, 2].map((k) => {
                  const s = d.sectors[k]
                  return (
                    <span key={k} role="cell" className={`r num hide-md sector ${s?.session_best ? 'purple' : ''}`}
                          title={s ? `Sector ${k + 1}: ${s.time.toFixed(3)} on lap ${s.lap}${s.session_best ? ', fastest of the session' : ''}` : undefined}>
                      {s ? s.time.toFixed(3) : '–'}
                    </span>
                  )
                })}
              </div>
            )
          })}
        </div>
        <div className="panel-foot">
          Ideal lap = a driver's best sector 1 + sector 2 + sector 3 from any of their laps in {SESSION_LABEL[data.session ?? ''] ?? 'the session'}.
          The line from ○ to ● is the time left on the table; ▲ is where the ideal lap would rank. Purple sectors are the fastest of the session.
          {data.session === 'Q' && ' Real laps are the official times.'} Sectors can come from different runs, and the track gets quicker through a session.
          {data.deleted_known === false && ' Track-limit deletions are not stored for this session, so a deleted lap’s sectors can count.'}
          {idealBest && pole != null && idealBest.ideal_lap != null && idealBest.ideal_lap < pole && idealBest.best_rank !== 1 &&
            ` ${idealBest.driver_name}'s ideal lap (${lapTime(idealBest.ideal_lap)}) would have been the fastest of all.`}
        </div>
      </div>
    </>
  )
}
