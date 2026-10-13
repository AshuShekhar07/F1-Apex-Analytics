import { useState } from 'react'

import { useWidth } from '../charts/useWidth'
import { driverCode } from '../names'
import { glowColour, normaliseHex } from '../theme/color'
import type { Overtakes as OvertakesData, OvertakesDriver } from '../types'
import { byPasses, headlines, signed } from './overtakes'

const BAND = { SC: 'SC', VSC: 'VSC', RED_FLAG: 'RED' } as const
const colourOf = (d: OvertakesDriver | undefined) => glowColour(normaliseHex(d?.color_hex, '#888888'))
const code = (d: OvertakesDriver | undefined) => driverCode(d?.driver_name ?? '???')

/** Overtakes and battles of one race: headline numbers, passes per lap, who made and lost
    places, and the longest nose-to-tail fights with how they ended. */
export function Overtakes({ data }: { data: OvertakesData }) {
  const byId = new Map(data.drivers.map((d) => [d.driver_id, d]))
  const h = headlines(data)
  const rows = byPasses(data.drivers)
  const maxBar = Math.max(1, ...rows.map((d) => Math.max(d.made, d.lost)))

  return (
    <>
      <div className="section-head" style={{ marginTop: 26 }}><h2>Overtakes &amp; battles</h2><span className="aside">passes on track, lap by lap</span></div>
      <div className="panel" style={{ marginBottom: 18 }}>
        <div className="kv ot-kv">
          <div><div className="v num">{data.total_overtakes}</div><div className="k">Overtakes on track{h.busiest ? ` · most on lap ${h.busiest.lap} (${h.busiest.n})` : ''}</div></div>
          <div><div className="v">{h.most ? code(h.most) : '–'}</div><div className="k">Most passes{h.most ? ` · ${h.most.made}` : ''}</div></div>
          <div><div className="v">{h.bestStart ? code(h.bestStart) : '–'}</div><div className="k">Best start{h.bestStart ? ` · ${signed(h.bestStart.start_gain)} places on lap 1` : ''}</div></div>
          <div>
            <div className="v">{h.longest ? `${code(byId.get(h.longest.attacker))} v ${code(byId.get(h.longest.defender))}` : '–'}</div>
            <div className="k">Longest battle{h.longest ? ` · ${h.longest.laps} laps` : ''}</div>
          </div>
        </div>
        <div className="panel-body"><PassesPerLap data={data} byId={byId} /></div>
      </div>

      <div className="cols-2" style={{ alignItems: 'start' }}>
        <div className="panel">
          <div className="panel-head"><h3>Passes by driver</h3><span className="aside">made · lost · lap 1</span></div>
          <div className="table-wrap">
            <table className="timing">
              <thead><tr><th>Driver</th><th className="r hide-sm">Grid → finish</th><th className="r">Lap 1</th><th>Made</th><th>Lost</th></tr></thead>
              <tbody>
                {rows.map((d) => (
                  <tr key={d.driver_id}>
                    <td><div className="driver-cell"><span className="team-bar" style={{ background: colourOf(d) }} /><span className="code">{code(d)}</span><span className="name hide-sm">{d.driver_name}</span></div></td>
                    <td className="r num hide-sm dim">{d.grid ? `P${d.grid}` : 'Pit'} → {d.finish ? (d.classified ? `P${d.finish}` : 'DNF') : '–'}</td>
                    <td className="r num">{signed(d.start_gain)}</td>
                    <td><span className="ot-bar"><span className="track"><i style={{ width: `${(d.made / maxBar) * 100}%`, background: 'var(--up)' }} /></span><b className="num">{d.made}</b></span></td>
                    <td><span className="ot-bar"><span className="track"><i style={{ width: `${(d.lost / maxBar) * 100}%`, background: 'var(--down)' }} /></span><b className="num">{d.lost}</b></span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel">
          <div className="panel-head"><h3>Longest battles</h3><span className="aside">within 1 s, lap after lap</span></div>
          {data.battles.length ? (
            <ul className="battle-list">
              {data.battles.map((b, i) => {
                const att = byId.get(b.attacker)
                const def = byId.get(b.defender)
                return (
                  <li key={i}>
                    <span className="pair">
                      <span className="team-bar" style={{ background: colourOf(att) }} /><b>{code(att)}</b>
                      <span className="dim">chasing</span>
                      <span className="team-bar" style={{ background: colourOf(def) }} /><b>{code(def)}</b>
                    </span>
                    <span className="meta num">L{b.from_lap}–{b.to_lap} · {b.laps} laps · closest {b.closest.toFixed(3)}s</span>
                    <span className={`outcome ${b.passed_on_lap ? 'up' : ''}`}>
                      {b.passed_on_lap
                        ? `▲ ${code(att)} got past on lap ${b.passed_on_lap}`
                        : b.changes ? `${code(def)} came out ahead` : `${code(def)} held on`}
                      {b.changes > 1 && <span className="dim"> · {b.changes} swaps</span>}
                    </span>
                  </li>
                )
              })}
            </ul>
          ) : <div className="empty">{data.total_laps ? 'No battle lasted two laps within a second.' : 'No lap timing for this race.'}</div>}
        </div>
      </div>
      <p className="dim small" style={{ marginTop: 10 }}>
        An overtake is a car getting ahead of another between the end of one lap and the next. Not counted: swaps from pit stops,
        laps under a Safety Car, VSC or red flag, passing a car about to retire, and the start (shown as Lap 1: places gained from the grid).
        {data.neutralised_known === false && ' Safety Car laps are not stored for this race, so a pass under one could be counted.'}
      </p>
    </>
  )
}

const M = { t: 18, r: 8, b: 22, l: 26 }

/** Bars: overtakes on each lap, with the neutralised laps shaded. Hover or tap a bar for the passes. */
function PassesPerLap({ data, byId }: { data: OvertakesData; byId: Map<number, OvertakesDriver> }) {
  const [box, W] = useWidth<HTMLDivElement>(800)
  const [lap, setLap] = useState<number | null>(null)
  const total = data.per_lap.length
  if (!total) return null
  const H = 150
  const top = Math.max(2, ...data.per_lap)
  const step = (W - M.l - M.r) / total
  const x = (l: number) => M.l + (l - 1) * step
  const y = (v: number) => M.t + (1 - v / top) * (H - M.t - M.b)
  const ticks = Array.from({ length: top + 1 }, (_, i) => i).filter((v) => top <= 4 || v % Math.ceil(top / 4) === 0)
  const xStep = total <= 30 ? 5 : W < 560 ? 20 : 10
  const passes = lap == null ? [] : data.overtakes.filter((o) => o.lap === lap)
  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    setLap(Math.min(total, Math.max(1, Math.floor(((e.clientX - r.left) / r.width) * total) + 1)))
  }
  return (
    <figure className="chart" style={{ margin: 0, position: 'relative' }} ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Overtakes on each of the ${total} laps`}>
        {(data.neutralised ?? []).map((e, i) => (
          <g key={i}>
            <rect x={x(e.start_lap)} y={M.t} width={Math.max(2, (e.end_lap - e.start_lap + 1) * step)} height={H - M.t - M.b}
                  fill={e.kind === 'RED_FLAG' ? 'var(--red)' : 'var(--yellow)'} opacity={e.kind === 'SC' || e.kind === 'RED_FLAG' ? 0.12 : 0.07} />
            <text x={x(e.start_lap) + ((e.end_lap - e.start_lap + 1) * step) / 2} y={M.t - 6} textAnchor="middle" fill="var(--text-2)"
                  fontSize="9" fontWeight="700" fontFamily="var(--mono)">{BAND[e.kind]}</text>
          </g>
        ))}
        {ticks.map((t) => (
          <g key={t}>
            <line className="gridline" x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} />
            <text x={M.l - 6} y={y(t) + 3} textAnchor="end" fill="var(--text-3)" fontSize="10" fontFamily="var(--mono)">{t}</text>
          </g>
        ))}
        {data.per_lap.map((n, i) => n > 0 && (
          <rect key={i} x={x(i + 1) + 1} y={y(n)} width={Math.max(1, step - 2)} height={y(0) - y(n)} rx={Math.min(2, step / 4)}
                fill={lap === i + 1 ? 'var(--text)' : 'var(--text-2)'} />
        ))}
        {[1, ...Array.from({ length: Math.floor(total / xStep) }, (_, i) => (i + 1) * xStep)].filter((l) => l <= total).map((l) => (
          <text key={l} x={x(l) + step / 2} y={H - 6} textAnchor="middle" fill="var(--text-3)" fontSize="10" fontFamily="var(--mono)">L{l}</text>
        ))}
        <rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b} fill="transparent" style={{ touchAction: 'pan-y' }}
              onPointerMove={onMove} onPointerDown={onMove} onPointerLeave={() => setLap(null)} />
      </svg>
      {lap != null && (
        <div className={`chart-tip ${W < 560 ? 'inline' : ''}`}
             style={W < 560 ? undefined : { left: `${((x(lap) + step / 2) / W) * 100}%`, top: 0, transform: x(lap) / W > 0.6 ? 'translateX(calc(-100% - 12px))' : 'translateX(12px)' }}>
          <div className="t">Lap {lap}</div>
          {passes.length ? passes.map((o, i) => (
            <div className="row" key={i}>
              <span className="team-bar" style={{ background: colourOf(byId.get(o.by)), height: 10 }} />
              <b>{code(byId.get(o.by))}</b><span className="dim">passed</span><b>{code(byId.get(o.on))}</b>
              <span className="num dim" style={{ marginLeft: 'auto' }}>P{o.for_position}</span>
            </div>
          )) : <div className="dim small">No passes on track</div>}
        </div>
      )}
    </figure>
  )
}
