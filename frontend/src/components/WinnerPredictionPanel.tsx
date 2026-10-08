import { Link } from 'react-router-dom'

import { pct, stageLabel } from '../format'
import type { PredictionStage, WinnerPrediction, WinnerStage } from '../types'
import { ConfidenceBadge, ConfidenceBasis } from './Confidence'
import { DriverName } from './DriverName'

const ORDER: PredictionStage[] = ['pre_qualifying', 'post_qualifying']

function StageCard({ stage, block, current }: { stage: PredictionStage; block: WinnerStage; current: boolean }) {
  const fav = block.favourite
  return (
    <div className={`stage-card${current ? ' current' : ''}`}>
      <div className="stage-card-head">
        <span className="label">{stageLabel(stage)}</span>
        {current && <span className="label now">Current</span>}
      </div>
      <div className="favourite">
        <DriverName name={fav.driver_name ?? `Driver ${fav.driver_id}`} color={fav.color_hex} team={fav.team_name} />
        <span className="big-number">{pct(fav.p_win, 1)}</span>
      </div>
      <div className="muted small">to win · {pct(fav.p_podium, 0)} podium</div>
      <ConfidenceBadge level={block.confidence.level} />
      <ConfidenceBasis lines={[block.confidence.basis, block.confidence.data_basis]} />
      <div className="table-wrap">
        <table className="compact">
          <thead>
            <tr>
              {stage === 'post_qualifying' && <th>Grid</th>}
              <th>Driver</th>
              <th>Win</th>
              <th>Podium</th>
            </tr>
          </thead>
          <tbody>
            {block.drivers.slice(0, 6).map((d) => (
              <tr key={d.driver_id}>
                {stage === 'post_qualifying' && <td className="pos">{d.grid ? `P${d.grid}` : '–'}</td>}
                <td><DriverName name={d.driver_name ?? `Driver ${d.driver_id}`} color={d.color_hex} /></td>
                <td>
                  <div className="prob"><div className="prob-fill" style={{ width: `${d.p_win * 100}%` }} /><span>{pct(d.p_win, 1)}</span></div>
                </td>
                <td className="muted">{pct(d.p_podium, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function WinnerPredictionPanel({ prediction, showRaceLink = false }: { prediction: WinnerPrediction; showRaceLink?: boolean }) {
  const p = prediction
  const ev = p.evidence
  return (
    <div className="winner-prediction">
      <p className="muted small">
        {showRaceLink && <><Link to={`/race/${p.race_id}`}>{p.season} R{p.round} {p.track}</Link> · </>}
        Based on {ev.races_this_season} race{ev.races_this_season === 1 ? '' : 's'} run this season
        {ev.new_regulations ? ' under brand-new regulations' : ''} and earlier seasons, using only races before{' '}
        {p.race_date}. {p.grid_source ? `Grid: ${p.grid_source.replace('_', ' ')}.` : 'Grid not known yet: the chances sharpen after qualifying.'}
      </p>
      <div className="stage-cards">
        {ORDER.filter((s) => p.stages[s]).map((s) => (
          <StageCard key={s} stage={s} block={p.stages[s]!} current={s === p.stage} />
        ))}
      </div>
      <p className="accuracy">
        <strong>How good is this?</strong> Tested on 167 races (2019–2026), each predicted only from earlier races:
        after qualifying the favourite won 58.7% and the winner was in the top 3 in 90.4%; before qualifying
        44.3% and 70.7%. Crashes, rain and retirements decide many races; the confidence level says how often
        calls like this one came true.
      </p>
    </div>
  )
}
