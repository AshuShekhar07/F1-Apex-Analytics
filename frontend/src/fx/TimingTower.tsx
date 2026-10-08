import { useRef } from 'react'

import { gsap, reducedMotion, useGSAP } from '../motion/gsap'
import { afterIntro, PAGE_ENTER_DELAY } from '../motion/stage'
import { driverCode } from '../names'
import { glowColour, normaliseHex } from '../theme/color'
import type { WinnerPrediction } from '../types'

/**
 * The model's call for the next race laid out like a TV timing tower: position, team colour,
 * three-letter code and win chance. Rows deal in one by one, then a light sweeps down the
 * tower every few seconds. The numbers are the stored prediction, nothing else.
 */
export function TimingTower({ prediction, rows = 10 }: { prediction: WinnerPrediction; rows?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const stage = prediction.stages[prediction.stage] ?? Object.values(prediction.stages)[0]
  const drivers = [...(stage?.drivers ?? [])].sort((a, b) => b.p_win - a.p_win).slice(0, rows)
  const top = drivers[0]?.p_win || 1

  useGSAP(
    () => {
      if (reducedMotion() || !ref.current) return
      const list = ref.current.querySelectorAll('.tower-row')
      gsap.set(list, { xPercent: -105, opacity: 0 })
      return afterIntro(() => {
        gsap.to(list, { xPercent: 0, opacity: 1, duration: 0.55, ease: 'power4.out', stagger: 0.07, delay: PAGE_ENTER_DELAY + 0.5 })
        gsap.fromTo(
          ref.current!.querySelector('.tower-sweep'),
          { yPercent: -100 },
          { yPercent: 1100, duration: 1.6, ease: 'power2.inOut', repeat: -1, repeatDelay: 3.2, delay: PAGE_ENTER_DELAY + 1.6 },
        )
      })
    },
    { scope: ref, dependencies: [prediction.race_id] },
  )

  if (!drivers.length) return null
  return (
    <div className="tower" ref={ref}>
      <div className="tower-head">
        <span>Apex21</span>
        <b>{prediction.stage === 'post_qualifying' ? 'Win % · after quali' : 'Win % · before quali'}</b>
      </div>
      <div className="tower-body">
        <div className="tower-sweep" aria-hidden="true" />
        {drivers.map((d, i) => {
          const colour = glowColour(normaliseHex(d.color_hex, '#888888'))
          return (
            <div className={`tower-row ${i === 0 ? 'p1' : ''}`} key={d.driver_id} title={`${d.driver_name} · ${d.team_name}`}>
              <span className="pos">{i + 1}</span>
              <i style={{ background: colour }} />
              <span className="code">{driverCode(d.driver_name ?? '???')}</span>
              <span className="meter"><span style={{ width: `${(d.p_win / top) * 100}%`, background: colour }} /></span>
              <span className="pct">{(d.p_win * 100).toFixed(d.p_win < 0.1 ? 1 : 0)}%</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
