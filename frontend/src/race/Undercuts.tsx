import { compoundColor, compoundLetter } from '../format'
import { driverCode } from '../names'
import { glowColour, normaliseHex } from '../theme/color'
import type { Undercuts as UndercutsData } from '../types'
import { biggestGain, duelScale, gapWords, verdict } from './undercuts'

/**
 * Undercut checker: every pit-stop duel between cars running close together. Each row is the
 * first stopper's gap to its rival on one scale (behind to the left, ahead to the right): the
 * ring before the stops, the dot after both stops, so the arrow shows what the pit cycle did.
 */
export function Undercuts({ data }: { data: UndercutsData }) {
  const byId = new Map(data.drivers.map((d) => [d.driver_id, d]))
  const code = (id: number) => driverCode(byId.get(id)?.driver_name ?? '???')
  const colour = (id: number) => glowColour(normaliseHex(byId.get(id)?.color_hex, '#888888'))
  const scale = duelScale(data.duels)
  const best = biggestGain(data.duels)
  const u = data.summary.undercut
  const o = data.summary.overcut

  return (
    <>
      <div className="section-head" style={{ marginTop: 26 }}><h2>Undercut checker</h2><span className="aside">pit first, or stay out?</span></div>
      <div className="panel">
        <div className="kv ot-kv">
          <div><div className="v num">{u.tried ? `${u.worked}/${u.tried}` : '–'}</div><div className="k">Undercuts that worked</div></div>
          <div><div className="v num">{o.tried ? `${o.worked}/${o.tried}` : '–'}</div><div className="k">Overcuts that worked</div></div>
          <div>
            <div className="v">{best ? code(best.gain >= 0 ? best.first : best.second) : '–'}</div>
            <div className="k">{best ? `Biggest swing · ${Math.abs(best.gain).toFixed(1)}s on ${code(best.gain >= 0 ? best.second : best.first)}` : 'Biggest swing'}</div>
          </div>
          <div><div className="v num">{data.skipped_neutralised}</div><div className="k">Duels under SC / VSC (left out)</div></div>
        </div>

        {data.duels.length ? (
          <div className="duel-rows">
            <div className="duel-row head">
              <span>Laps</span><span>Pitted first · then</span>
              <span className="duel-scale-head"><span>◂ behind</span><span>level</span><span>ahead ▸</span></span>
              <span>Verdict</span>
            </div>
            {data.duels.map((d, i) => {
              const f = code(d.first)
              const s = code(d.second)
              const v = verdict(d, f, s)
              const a = scale.pct(d.gap_before)
              const b = scale.pct(d.gap_after)
              return (
                <div className="duel-row" key={i} title={`${v.detail}: ${gapWords(d.gap_before)} before, ${gapWords(d.gap_after)} after`}>
                  <span className="num dim">L{d.first_lap} · L{d.second_lap}{d.for_position ? <span className="hide-sm"> · P{d.for_position}</span> : null}</span>
                  <span className="pair">
                    <span className="team-bar" style={{ background: colour(d.first) }} /><b>{f}</b>
                    <i className="tyre" style={{ background: compoundColor(d.first_compound) }} title={d.first_compound ?? ''}>{compoundLetter(d.first_compound)}</i>
                    <span className="dim">then</span>
                    <span className="team-bar" style={{ background: colour(d.second) }} /><b>{s}</b>
                    <i className="tyre" style={{ background: compoundColor(d.second_compound) }} title={d.second_compound ?? ''}>{compoundLetter(d.second_compound)}</i>
                  </span>
                  <span className="duel-scale" aria-label={`${f}: ${gapWords(d.gap_before)} before, ${gapWords(d.gap_after)} after`}>
                    <i className="level" />
                    <i className="arrow" style={{ left: `${Math.min(a, b)}%`, width: `${Math.abs(b - a)}%`, background: colour(d.first) }} />
                    <i className="ring" style={{ left: `${a}%`, borderColor: colour(d.first) }} />
                    <i className="dot" style={{ left: `${b}%`, background: colour(d.first) }} />
                    <span className="lbl num">{gapWords(d.gap_before)} → {gapWords(d.gap_after)}</span>
                  </span>
                  <span className={`verdict ${v.good ? 'up' : ''}`}><b>{v.title}</b><span className="dim small hide-sm">{v.detail}</span></span>
                </div>
              )
            })}
          </div>
        ) : <div className="empty">No pit-stop duels between cars within {data.reach} s{data.skipped_neutralised ? ' under green flag' : ''} in this race.</div>}
        <div className="panel-foot">
          A duel: two cars within {data.reach} s when the first of them pits, the other pitting within {data.window} laps.
          Each row is the first stopper's gap to the other car: ○ before the stops, ● after both (after the second car's out-lap).
          Undercut = the car behind pits first to use fresh tyres; overcut = the car behind stays out to get ahead.
          Stops under a Safety Car, VSC or red flag are much cheaper, so those duels are left out.
          {data.neutralised_known === false && ' Safety Car laps are not stored for this race, so a cheap stop could be included.'}
        </div>
      </div>
    </>
  )
}
