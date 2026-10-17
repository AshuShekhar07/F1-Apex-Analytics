import { useEffect, useMemo, useRef, useState } from 'react'

import { useFlip } from '../motion/useFlip'
import { reducedMotion } from '../motion/gsap'
import { racedRounds, standingsAfter } from '../seasonReplay'
import { glowColour, normaliseHex } from '../theme/color'
import type { SeasonResults } from '../types'

const SHOWN = 10
const STEP_MS = 1300

/** The championship replayed round by round: the bars grow with each result and the order
    changes place by place, like a timing tower. */
export function SeasonReplay({ matrix, kind }: { matrix: SeasonResults; kind: 'drivers' | 'constructors' }) {
  const rounds = useMemo(() => racedRounds(matrix), [matrix])
  const [i, setI] = useState(rounds.length - 1)
  const [playing, setPlaying] = useState(false)
  const list = useRef<HTMLOListElement>(null)
  useEffect(() => setI(rounds.length - 1), [rounds.length, kind])
  const round = rounds[Math.max(0, i)]
  const rows = useMemo(() => (round == null ? [] : standingsAfter(matrix, kind, round)).slice(0, SHOWN), [matrix, kind, round])
  useFlip(list, [round, kind])

  useEffect(() => {
    if (!playing) return
    if (i >= rounds.length - 1) {
      setPlaying(false)
      return
    }
    const t = window.setTimeout(() => setI((x) => x + 1), reducedMotion() ? STEP_MS / 2 : STEP_MS)
    return () => window.clearTimeout(t)
  }, [playing, i, rounds.length])

  if (!rounds.length) return null
  const top = Math.max(1, ...rows.map((r) => r.points))
  const info = matrix.rounds.find((r) => r.round === round)
  return (
    <div className="panel season-replay">
      <div className="panel-head">
        <h3>Replay the season</h3>
        <span className="aside">{kind === 'drivers' ? 'drivers' : 'constructors'} · top {SHOWN}</span>
      </div>
      <div className="sr-now">
        <b>After round {round}</b><span>{info?.track_name ?? ''}</span>
      </div>
      <ol className="sr-bars" ref={list}>
        {rows.map((r, n) => {
          const colour = glowColour(normaliseHex(r.colour, '#888888'))
          return (
            <li key={r.id} data-flip={`${kind}-${r.id}`}>
              <span className="pos">{n + 1}</span>
              <span className="name">{r.name}</span>
              <span className="bar"><i style={{ width: `${(r.points / top) * 100}%`, background: colour }} /></span>
              <span className="pts num">{Math.round(r.points)}</span>
            </li>
          )
        })}
      </ol>
      <div className="replay-controls">
        <button type="button" className="btn" onClick={() => {
          if (i >= rounds.length - 1) setI(0)
          setPlaying(!playing)
        }}>{playing ? '❚❚ Pause' : '▶ Replay'}</button>
        <input type="range" className="replay-scrub" min={0} max={rounds.length - 1} step={1} value={i} aria-label="Round"
               onChange={(e) => { setPlaying(false); setI(Number(e.target.value)) }} />
        <span className="small dim num">R{round} of {rounds[rounds.length - 1]}</span>
      </div>
    </div>
  )
}
