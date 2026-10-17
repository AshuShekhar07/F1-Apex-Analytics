import { useEffect, useMemo, useRef, useState } from 'react'

import { lapPoints, type Circuit, type Pt } from '../circuitTypes'
import { driverCode } from '../names'
import { glowColour, normaliseHex } from '../theme/color'
import { bestMatch } from '../trackMatch'
import type { RaceTrace } from '../types'
import { crossingTimes, lapPath, neutralisedOn, pointAt, projector, replayEnd, runningOrder, type Running } from './replay'

const SPEEDS: [number, string][] = [[6, 'Slow'], [3, 'Normal'], [1.2, 'Fast']]   // seconds of replay per lap
const LABELS = 3                                                                    // cars labelled on the map

/**
 * The race replayed on the circuit map: every car a dot in its team colour, moving at its real
 * pace lap by lap (from the stored lap timing), the running order beside it, Safety Car and
 * VSC laps shown on the track. Starts as lap 1 ends (lap 1 itself has no timing).
 */
export function RaceReplay({ trace, track }: { trace: RaceTrace; track: string }) {
  const [circuit, setCircuit] = useState<Circuit | null | undefined>(undefined)
  useEffect(() => {
    let live = true
    import('../circuits').then(({ CIRCUITS }) => {
      if (live) setCircuit(bestMatch(CIRCUITS, track))
    })
    return () => {
      live = false
    }
  }, [track])

  if (circuit === undefined) return <div className="panel panel-pad dim small">Loading the circuit…</div>
  if (!circuit || !trace.reference_lap || !trace.total_laps) {
    return <div className="panel panel-pad dim small">No circuit map or lap timing for a replay of this race.</div>
  }
  return <Replay trace={trace} circuit={circuit} />
}

function Replay({ trace, circuit }: { trace: RaceTrace; circuit: Circuit }) {
  const ref = trace.reference_lap!
  const total = trace.total_laps!
  const geo = useMemo(() => {
    const at = projector(lapPoints(circuit))
    const sectors = circuit.sectors.map((s) => s.points.map((p) => at(p) as Pt))
    const d = (pts: Pt[]) => pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join('')
    return {
      path: lapPath(sectors, circuit.sectors.map((s) => s.avgSpeedKmh)),
      lap: `${d(lapPoints(circuit).map((p) => at(p) as Pt))}Z`,
      parts: circuit.sectors.map((s, i) => ({ d: d(sectors[i]), hex: s.hex })),
    }
  }, [circuit])
  const cars = useMemo(() => trace.drivers.map((d) => ({
    id: d.driver_id, d, code: driverCode(d.driver_name ?? '???'), colour: glowColour(normaliseHex(d.color_hex, '#888888')),
    times: crossingTimes(d, ref),
  })), [trace, ref])
  const end = useMemo(() => replayEnd(trace), [trace])

  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(3)
  const [T, setT] = useState(0)                   // the clock the controls and the tower show
  const clock = useRef(0)                         // the clock the animation runs on
  const [order, setOrder] = useState<Running[]>(() => runningOrder(cars, 0, total, ref))
  const [focus, setFocus] = useState<number | null>(null)
  const dots = useRef(new Map<number, SVGGElement>())

  // place every car for the clock value t
  const draw = (t: number) => {
    const now = runningOrder(cars, t, total, ref)
    const rank = new Map(now.map((r, i) => [r.id, i]))
    for (const c of cars) {
      const g = dots.current.get(c.id)
      if (!g) continue
      const r = now.find((x) => x.id === c.id)
      if (!r) {
        g.style.opacity = '0'
        continue
      }
      const [x, y] = pointAt(geo.path, r.state.finished ? 0 : r.state.frac)
      g.style.opacity = '1'
      g.setAttribute('transform', `translate(${x.toFixed(1)},${y.toFixed(1)})`)
      g.classList.toggle('labelled', (rank.get(c.id) ?? 99) < LABELS || c.id === focus)
    }
    return now
  }

  useEffect(() => {
    setOrder(draw(clock.current))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geo, cars, focus])

  useEffect(() => {
    if (!playing) return
    let frame = 0, last = performance.now(), lastUi = 0
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      clock.current = Math.min(end, clock.current + dt * (ref / speed))
      const o = draw(clock.current)
      if (now - lastUi > 200 || clock.current >= end) {
        lastUi = now
        setT(clock.current)
        setOrder(o)
      }
      if (clock.current >= end) {
        setPlaying(false)
        return
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, speed, end, ref])

  const seek = (t: number) => {
    clock.current = t
    setT(t)
    setOrder(draw(t))
  }
  const lead = order[0]?.state
  const lap = Math.min(total, lead ? (lead.finished ? total : lead.laps + 1) : 1)
  const flag = neutralisedOn(trace, lap)
  const done = !!lead?.finished

  return (
    <div className="panel replay">
      <div className="replay-stage">
        <svg viewBox="0 0 1000 1000" className={`replay-map ${flag ? `flag-${flag.toLowerCase()}` : ''}`} role="img"
             aria-label={`${trace.track} ${trace.season}: replay, lap ${lap} of ${total}`}>
          <path d={geo.lap} className="replay-track-base" />
          {geo.parts.map((p, i) => <path key={i} d={p.d} className="replay-track" style={{ stroke: p.hex }} />)}
          {cars.map((c) => (
            <g key={c.id} ref={(el) => { if (el) dots.current.set(c.id, el); else dots.current.delete(c.id) }}
               className={`replay-car ${focus === c.id ? 'focus' : ''}`} style={{ opacity: 0 }}>
              <circle r={15} fill={c.colour} />
              <text y={-24} textAnchor="middle">{c.code}</text>
            </g>
          ))}
        </svg>
        <div className="replay-hud">
          <span className="replay-lap"><b>Lap {lap}</b><span>/ {total}</span></span>
          {flag && <span className={`replay-flag ${flag.toLowerCase()}`}>{flag === 'SC' ? 'Safety Car' : flag === 'VSC' ? 'Virtual Safety Car' : 'Red flag'}</span>}
          {done && <span className="replay-flag done"><i className="chequer" /> Chequered flag</span>}
        </div>
      </div>
      <div className="replay-side">
        <ol className="replay-tower">
          {order.map((r, i) => {
            const c = cars.find((x) => x.id === r.id)!
            const gap = r.state.laps > 0 ? c.d.gap[Math.min(r.state.laps, c.d.gap.length) - 1] : null
            const pitting = c.d.pits.some((p) => p.lap === r.state.laps + 1)
            return (
              <li key={r.id} className={focus === r.id ? 'focus' : ''} onClick={() => setFocus(focus === r.id ? null : r.id)}>
                <span className="pos">{i + 1}</span>
                <span className="team-bar" style={{ background: c.colour }} />
                <span className="code">{c.code}</span>
                <span className="gap num">
                  {r.state.finished ? <i className="chequer" /> : pitting ? <b className="pit">PIT</b>
                    : i === 0 ? 'Leader' : gap != null ? `+${gap.toFixed(1)}` : ''}
                </span>
              </li>
            )
          })}
        </ol>
      </div>
      <div className="replay-controls">
        <button type="button" className="btn" onClick={() => {
          if (clock.current >= end) seek(0)
          setPlaying(!playing)
        }} aria-label={playing ? 'Pause' : 'Play'}>{playing ? '❚❚ Pause' : '▶ Play'}</button>
        <button type="button" className="btn" onClick={() => { setPlaying(false); seek(0) }} aria-label="Back to lap 1">⟲</button>
        <input type="range" className="replay-scrub" min={0} max={end} step={0.5} value={T} aria-label="Replay position"
               onChange={(e) => seek(Number(e.target.value))} />
        <div className="tabs small-tabs" role="tablist" aria-label="Replay speed">
          {SPEEDS.map(([s, label]) => (
            <button key={s} role="tab" aria-selected={speed === s} className={speed === s ? 'on' : ''} onClick={() => setSpeed(s)}>{label}</button>
          ))}
        </div>
      </div>
      <p className="small dim replay-note">
        Every car at its real pace, lap by lap, from the timing data: between two timing lines a car moves round the lap faster through the
        circuit's quicker sectors. Lap 1 has no timing, so the replay starts as the leader completes it. Click a driver to follow them.
      </p>
    </div>
  )
}
