import { useLayoutEffect, useRef } from 'react'

import { compoundColor, compoundLetter, pct } from '../format'
import { gsap, reducedMotion } from '../motion/gsap'
import { glowColour, normaliseHex } from '../theme/color'

export interface TowerRow {
  id: number
  name: string
  code: string
  team: string | null
  colour: string | null
  grid: number | null
  pWin: number
  pPodium: number
  predicted: number | null
  /** places gained (+) or lost (-) in the ranking since the other stage */
  change: number | null
  strategy: { sequence: string[]; stop_laps: number[] } | null
}

/**
 * The model's race prediction as a timing tower, ranked by win chance. Rows slide to their
 * new place when the ranking changes (switching stage, or new data after a session), and a
 * win chance that moves flashes green or red, like a timing screen.
 */
export function PredictionTower({ rows, stageKey }: { rows: TowerRow[]; stageKey: string }) {
  const body = useRef<HTMLTableSectionElement>(null)
  const tops = useRef(new Map<number, number>())
  const wins = useRef(new Map<number, number>())
  const lastStage = useRef<string | null>(null)
  const top = Math.max(...rows.map((r) => r.pWin), 0.0001)
  const showGrid = rows.some((r) => r.grid != null)
  const showPred = rows.some((r) => r.predicted != null)
  const showStrategy = rows.some((r) => r.strategy)
  const showChange = rows.some((r) => r.change != null)

  useLayoutEffect(() => {
    const tbody = body.current
    if (!tbody) return
    const motion = !reducedMotion()
    const first = tops.current.size === 0
    const sameStage = lastStage.current === stageKey
    for (const tr of Array.from(tbody.querySelectorAll<HTMLTableRowElement>('tr[data-id]'))) {
      const id = Number(tr.dataset.id)
      const top = tr.offsetTop
      const before = tops.current.get(id)
      if (motion && before !== undefined && before !== top) {
        gsap.fromTo(tr, { y: before - top }, { y: 0, duration: 0.7, ease: 'power3.inOut', overwrite: true })
      }
      tops.current.set(id, top)
      const win = Number(tr.dataset.win)
      const prevWin = wins.current.get(id)
      if (sameStage && prevWin !== undefined && Math.abs(win - prevWin) >= 0.005) {
        const cell = tr.querySelector('.pwin')
        cell?.classList.remove('flash-up', 'flash-down')
        void (cell as HTMLElement | null)?.offsetWidth
        cell?.classList.add(win > prevWin ? 'flash-up' : 'flash-down')
      }
      wins.current.set(id, win)
    }
    if (motion && first) {
      gsap.from(tbody.querySelectorAll('tr'), { opacity: 0, x: -10, duration: 0.4, stagger: 0.025, ease: 'power2.out' })
      gsap.from(tbody.querySelectorAll('.meter i'), { scaleX: 0, duration: 0.9, stagger: 0.025, ease: 'power3.out', delay: 0.1 })
    }
    lastStage.current = stageKey
  }, [rows, stageKey])

  return (
    <div className="table-wrap">
      <table className="timing">
        <thead>
          <tr>
            <th>Pos</th>
            {showChange && <th className="hide-sm" title="Places gained or lost since the other stage">+/-</th>}
            <th>Driver</th>
            {showGrid && <th className="r">Grid</th>}
            <th>Win</th>
            <th className="hide-sm">Podium</th>
            {showPred && <th className="r hide-sm" title="Predicted finishing position (race forecast)">Pred.</th>}
            {showStrategy && <th className="hide-sm">Strategy</th>}
          </tr>
        </thead>
        <tbody ref={body}>
          {rows.map((r, i) => {
            const colour = glowColour(normaliseHex(r.colour, '#888888'))
            return (
              <tr key={r.id} data-id={r.id} data-win={r.pWin}>
                <td className="pos-cell"><span className={`pos-box ${i < 3 ? `p${i + 1}` : ''}`}>{i + 1}</span></td>
                {showChange && (
                  <td className="hide-sm">
                    <span className={`delta ${r.change == null || r.change === 0 ? 'flat' : r.change > 0 ? 'up' : 'down'}`}>
                      {r.change == null ? '' : r.change === 0 ? '–' : r.change > 0 ? `▲${r.change}` : `▼${-r.change}`}
                    </span>
                  </td>
                )}
                <td>
                  <div className="driver-cell">
                    <span className="team-bar" style={{ background: colour }} />
                    <span className="code">{r.code}</span>
                    <span className="who">
                      <span className="name">{r.name}</span>
                      {r.team && <span className="team">{r.team}</span>}
                    </span>
                  </div>
                </td>
                {showGrid && <td className="r num">{r.grid ?? '–'}</td>}
                <td className="pwin">
                  <div className="prob-cell">
                    <span className="num">{pct(r.pWin, r.pWin < 0.1 ? 1 : 0)}</span>
                    <span className="meter"><i style={{ width: `${(r.pWin / top) * 100}%`, background: colour }} /></span>
                  </div>
                </td>
                <td className="hide-sm"><span className="num muted">{pct(r.pPodium, 0)}</span></td>
                {showPred && <td className="r num hide-sm">{r.predicted != null ? `P${r.predicted}` : '–'}</td>}
                {showStrategy && (
                  <td className="hide-sm">
                    {r.strategy && (
                      <span className="tyre-seq" title={r.strategy.sequence.join(' → ')}>
                        {r.strategy.sequence.map((c, k) => (
                          <span key={k} style={{ display: 'contents' }}>
                            <span className="tyre" style={{ borderColor: compoundColor(c) }}>{compoundLetter(c)}</span>
                            {k < r.strategy!.stop_laps.length && <span className="lap">L{r.strategy!.stop_laps[k]}</span>}
                          </span>
                        ))}
                      </span>
                    )}
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
