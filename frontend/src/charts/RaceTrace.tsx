import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { compoundColor, compoundLetter } from '../format'
import { gsap, reducedMotion } from '../motion/gsap'
import { driverCode } from '../names'
import {
  axis, dashedIds, formatTick, formatValue, neutralAt, orderAt, presetIds, valueAt, type Preset, type TraceMode,
} from '../race/raceTrace'
import { glowColour, normaliseHex } from '../theme/color'
import type { RaceTrace as RaceTraceData, RaceTraceDriver } from '../types'
import { spread } from './LineChart'
import { useWidth } from './useWidth'

const M = { t: 26, r: 50, b: 26, l: 52 }
const CLIP = 20000          // the reveal animates a clip this wide, whatever the chart's width
const BAND = { SC: 'SC', VSC: 'VSC', RED_FLAG: 'RED' } as const
const PRESETS: [Preset, string][] = [['top5', 'Top 5'], ['top10', 'Top 10'], ['all', 'All']]

const colourOf = (d: RaceTraceDriver) => glowColour(normaliseHex(d.color_hex, '#888888'))

/**
 * Race trace: every car's gap to the leader on every lap (or its time against the winner's
 * average pace), in team colours with a team's second car dashed. Pit stops are dots in the
 * colour of the tyre fitted; Safety Car, VSC and red-flag laps are shaded bands. A crosshair
 * reads the running order on any lap; the legend switches cars on and off.
 */
export function RaceTrace({ data }: { data: RaceTraceData }) {
  const [mode, setMode] = useState<TraceMode>('gap')
  const [preset, setPreset] = useState<Preset | null>('top10')
  const [shown, setShown] = useState<Set<number>>(() => new Set(presetIds(data.drivers, 'top10')))
  const [focus, setFocus] = useState<number | null>(null)
  const [lap, setLap] = useState<number | null>(null)
  const [box, W] = useWidth<HTMLDivElement>(900)
  const svg = useRef<SVGSVGElement>(null)
  const clipId = `trace-clip-${useId().replace(/:/g, '')}`

  useEffect(() => {
    setShown(new Set(presetIds(data.drivers, 'top10')))
    setPreset('top10')
    setLap(null)
  }, [data])

  const total = data.total_laps ?? Math.max(1, ...data.drivers.map((d) => d.gap.length))
  const dashed = useMemo(() => dashedIds(data.drivers), [data])
  const visible = useMemo(() => data.drivers.filter((d) => shown.has(d.driver_id)), [data, shown])
  const ax = useMemo(() => axis(visible, mode), [visible, mode])
  const narrow = W < 560          // phones: the lap readout sits under the chart, not over it
  const H = narrow ? 320 : 420
  const plotW = W - M.l - M.r
  const plotH = H - M.t - M.b
  const x = (l: number) => M.l + (total > 1 ? (l - 1) / (total - 1) : 0) * plotW
  const y = (v: number) => (mode === 'gap'
    ? M.t + ((v - ax.lo) / (ax.hi - ax.lo)) * plotH
    : M.t + ((ax.hi - v) / (ax.hi - ax.lo)) * plotH)

  const path = (d: RaceTraceDriver) => {
    let out = ''
    let pen = false
    ;(mode === 'gap' ? d.gap : d.trace).forEach((v, i) => {
      if (v == null) { pen = false; return }
      out += `${pen ? 'L' : 'M'}${x(i + 1).toFixed(1)},${y(v).toFixed(1)}`
      pen = true
    })
    return out
  }
  const lastPoint = (d: RaceTraceDriver) => {
    const values = mode === 'gap' ? d.gap : d.trace
    for (let i = values.length - 1; i >= 0; i--) if (values[i] != null) return { lap: i + 1, v: values[i] as number }
    return null
  }
  // names at the right edge for every classified car (lapped ones too), spread so they never
  // overlap; a car that retired is named where its line stops
  const finishers = visible.filter((d) => d.classified && lastPoint(d) != null)
  const labelYs = spread(finishers.map((d) => y(lastPoint(d)!.v)), 11, M.t, H - M.b)

  useLayoutEffect(() => {
    if (!svg.current || reducedMotion()) return
    const rect = svg.current.querySelector(`#${clipId} rect`)
    if (rect) gsap.fromTo(rect, { attr: { width: 0 } }, { attr: { width: CLIP }, duration: 1.6, ease: 'power2.inOut' })
  }, [data, clipId]) // eslint-disable-line react-hooks/exhaustive-deps

  const choosePreset = (p: Preset) => {
    setPreset(p)
    setShown(new Set(presetIds(data.drivers, p)))
  }
  const toggle = (id: number) => {
    setPreset(null)
    setShown((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next.size ? next : s
    })
  }
  const lapFromPointer = (e: React.PointerEvent<SVGRectElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const f = (e.clientX - r.left) / r.width
    setLap(Math.min(total, Math.max(1, Math.round(f * (total - 1)) + 1)))
  }
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault()
      setLap((l) => Math.min(total, Math.max(1, (l ?? (e.key === 'ArrowRight' ? 0 : total + 1)) + (e.key === 'ArrowRight' ? 1 : -1))))
    } else if (e.key === 'Escape') setLap(null)
  }

  const xStep = total <= 30 ? 5 : W < 560 ? 20 : 10
  const xTicks = [1, ...Array.from({ length: Math.floor(total / xStep) }, (_, i) => (i + 1) * xStep).filter((l) => l > 1 && l <= total)]
  const order = lap == null ? [] : orderAt(visible, mode, lap)
  const flag = lap == null ? null : neutralAt(data, lap)
  const hx = lap == null ? 0 : x(lap)
  const hasRed = data.neutralised.some((e) => e.kind === 'RED_FLAG')

  return (
    <>
      <div className="trace-controls">
        <div className="tabs" role="tablist" aria-label="View">
          <button role="tab" aria-selected={mode === 'gap'} className={mode === 'gap' ? 'on' : ''} onClick={() => setMode('gap')}>Gap to leader</button>
          <button role="tab" aria-selected={mode === 'trace'} className={mode === 'trace' ? 'on' : ''} onClick={() => setMode('trace')}>Race trace</button>
        </div>
        <div className="tabs" role="group" aria-label="Cars shown">
          {PRESETS.map(([p, label]) => (
            <button key={p} aria-pressed={preset === p} className={preset === p ? 'on' : ''} onClick={() => choosePreset(p)}>{label}</button>
          ))}
        </div>
      </div>

      <div className="panel panel-pad">
        <figure className="chart" style={{ margin: 0, position: 'relative' }} ref={box} tabIndex={0} onKeyDown={onKey}
                aria-label={`${mode === 'gap' ? 'Gap to the leader' : 'Race trace'}, lap by lap. Use the arrow keys to read a lap.`}>
          <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="img"
               aria-label={`${mode === 'gap' ? 'Gap to the leader' : 'Time against the winner’s average pace'} for ${visible.length} cars over ${total} laps`}>
            <defs><clipPath id={clipId}><rect x={0} y={0} width={CLIP} height={H} /></clipPath></defs>

            {data.neutralised.map((e, i) => {
              const x0 = Math.max(M.l, x(Math.max(1, e.start_lap - 1)))
              const x1 = Math.min(W - M.r, x(e.end_lap))
              const red = e.kind === 'RED_FLAG'
              return (
                <g key={i}>
                  <rect x={x0} y={M.t} width={Math.max(2, x1 - x0)} height={plotH}
                        fill={red ? 'var(--red)' : 'var(--yellow)'} opacity={red ? 0.14 : e.kind === 'SC' ? 0.12 : 0.07} />
                  <text x={(x0 + x1) / 2} y={M.t - 9} textAnchor="middle" fill="var(--text-2)" fontSize="9.5" fontWeight="700"
                        fontFamily="var(--mono)">{BAND[e.kind]}</text>
                </g>
              )
            })}

            {ax.ticks.map((t) => (
              <g key={t}>
                <line className="gridline" x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} />
                <text x={M.l - 8} y={y(t) + 3} textAnchor="end" fill="var(--text-3)" fontSize="10" fontFamily="var(--mono)">{formatTick(t, mode)}</text>
              </g>
            ))}
            {mode === 'trace' && <line x1={M.l} x2={W - M.r} y1={y(0)} y2={y(0)} stroke="var(--text-3)" strokeWidth={1} />}
            {xTicks.map((l) => (
              <text key={l} x={x(l)} y={H - 8} textAnchor="middle" fill="var(--text-3)" fontSize="10" fontFamily="var(--mono)">L{l}</text>
            ))}

            <g clipPath={`url(#${clipId})`}>
              {visible.map((d) => {
                const dim = focus != null && focus !== d.driver_id
                return (
                  <g key={d.driver_id} opacity={dim ? 0.12 : 1}>
                    <path d={path(d)} fill="none" stroke={colourOf(d)} strokeWidth={focus === d.driver_id ? 2.6 : 1.6}
                          strokeLinejoin="round" strokeLinecap="round" strokeDasharray={dashed.has(d.driver_id) ? '5 4' : undefined} />
                    {d.pits.map((p) => {
                      const v = valueAt(d, mode, p.lap)
                      return v == null ? null : (
                        <circle key={p.lap} cx={x(p.lap)} cy={y(v)} r={4} fill={compoundColor(p.compound)} stroke="var(--surface)" strokeWidth={2} />
                      )
                    })}
                  </g>
                )
              })}
            </g>

            {visible.map((d) => {
              const end = lastPoint(d)
              if (!end || d.classified) return null
              const dim = focus != null && focus !== d.driver_id
              return (
                <text key={d.driver_id} x={x(end.lap) + 5} y={y(end.v) + 3.5} opacity={dim ? 0.25 : 1} fill={d.classified ? 'var(--text-2)' : 'var(--down)'}
                      fontSize="10" fontWeight="700" fontFamily="var(--mono)">{driverCode(d.driver_name ?? '???')}{d.classified ? '' : ' ✕'}</text>
              )
            })}
            {finishers.map((d, i) => (
              <text key={d.driver_id} x={W - M.r + 8} y={labelYs[i] + 3.5} opacity={focus != null && focus !== d.driver_id ? 0.25 : 1}
                    fill="var(--text)" fontSize="10" fontWeight="700" fontFamily="var(--mono)">{driverCode(d.driver_name ?? '???')}</text>
            ))}

            {lap != null && (
              <g pointerEvents="none">
                <line x1={hx} x2={hx} y1={M.t} y2={H - M.b} stroke="var(--text-3)" strokeWidth={1} />
                {order.map((r) => (
                  <circle key={r.driver.driver_id} cx={hx} cy={y(r.value)} r={3.5} fill={colourOf(r.driver)} stroke="var(--surface)" strokeWidth={2} />
                ))}
              </g>
            )}
            <rect x={M.l} y={M.t} width={plotW} height={plotH} fill="transparent" style={{ touchAction: 'pan-y' }}
                  onPointerMove={lapFromPointer} onPointerDown={lapFromPointer} onPointerLeave={() => setLap(null)} />
          </svg>

          {lap != null && order.length > 0 && (
            <div className={`chart-tip ${narrow ? 'inline' : ''}`}
                 style={narrow ? undefined : { left: `${(hx / W) * 100}%`, top: M.t, transform: hx / W > 0.55 ? 'translateX(calc(-100% - 12px))' : 'translateX(12px)' }}>
              <div className="t">Lap {lap}{flag ? ` · ${BAND[flag]}` : ''}</div>
              {order.slice(0, 12).map((r) => (
                <div className="row trace-row" key={r.driver.driver_id}>
                  <i className={`key ${dashed.has(r.driver.driver_id) ? 'dash' : ''}`} style={{ color: colourOf(r.driver) }} />
                  <b className="num">{formatValue(r.value, mode)}</b>
                  <span className="who">{driverCode(r.driver.driver_name ?? '???')}</span>
                  {mode === 'gap' && r.interval != null && <span className="dim num small">{`+${r.interval.toFixed(3)}`}</span>}
                  {r.pitted && <span className="pit-tag" style={{ color: compoundColor(r.pitted.compound) }}>PIT {compoundLetter(r.pitted.compound)}</span>}
                </div>
              ))}
              {order.length > 12 && <div className="dim small">+{order.length - 12} more</div>}
            </div>
          )}
        </figure>

        <div className="trace-keys" role="group" aria-label="Cars: click to show or hide">
          {data.drivers.map((d) => {
            const on = shown.has(d.driver_id)
            return (
              <button key={d.driver_id} className={on ? 'on' : ''} aria-pressed={on} title={`${d.driver_name ?? ''} · ${d.team_name ?? ''}`}
                      onClick={() => toggle(d.driver_id)} onMouseEnter={() => on && setFocus(d.driver_id)} onMouseLeave={() => setFocus(null)}
                      onFocus={() => on && setFocus(d.driver_id)} onBlur={() => setFocus(null)}>
                <i className={`key ${dashed.has(d.driver_id) ? 'dash' : ''}`} style={{ color: colourOf(d) }} />
                {driverCode(d.driver_name ?? '???')}
              </button>
            )
          })}
        </div>

        <p className="dim small" style={{ margin: '10px 0 0' }}>
          {mode === 'gap'
            ? 'Seconds behind the leader at the end of each lap (a lapped car is compared on its own lap count). '
            : `Seconds ahead (+) or behind (−) a car lapping at the winner’s average pace${data.reference_lap ? ` (${data.reference_lap.toFixed(3)}s a lap)` : ''}: a climbing line is faster than that pace, a drop is a pit stop. `}
          Dots are pit stops, coloured by the tyre fitted; shaded laps had a Safety Car (SC), Virtual Safety Car (VSC) or red flag.
          {data.neutralised_known === false && ' Safety Car laps are not stored for this race.'}
          {mode === 'trace' && hasRed && ' The red-flag stoppage drops every line by the same time.'}
        </p>

        <details className="notes" style={{ marginTop: 10 }}>
          <summary className="small">Data table</summary>
          <div className="table-wrap">
            <table className="timing">
              <thead><tr><th>Car</th>{Array.from({ length: total }, (_, i) => <th key={i} className="r">L{i + 1}</th>)}</tr></thead>
              <tbody>
                {visible.map((d) => (
                  <tr key={d.driver_id}>
                    <td>{d.driver_name}</td>
                    {Array.from({ length: total }, (_, i) => {
                      const v = valueAt(d, mode, i + 1)
                      return <td key={i} className="r num">{v == null ? '' : mode === 'gap' ? v.toFixed(3) : v.toFixed(1)}</td>
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </div>
    </>
  )
}
