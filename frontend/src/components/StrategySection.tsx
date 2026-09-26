import { evidenceLabel, pct } from '../format'
import type { ActualDriverStrategy, StrategyPrediction } from '../types'
import { DriverName } from './DriverName'
import { CompoundLegend, StintBar } from './StintBar'

export function PredictedStrategy({ prediction }: { prediction: StrategyPrediction }) {
  const v = prediction.validation.dry.era_mode
  const options = [prediction.primary_strategy, ...prediction.alternatives]
  return (
    <>
      <CompoundLegend />
      <div className="strategy-list">
        {options.map((o, i) => (
          <div key={o.sequence.join('-') + o.stop_laps.join(',')} className="strategy-row">
            <div className="strategy-label">
              {i === 0 ? <strong>Most likely</strong> : <span className="muted">Alternative</span>}
              <span className="small muted">
                {o.sequence.length - 1}-stop{o.stop_laps.length ? `, lap ${o.stop_laps.join(' & ')}` : ''}
                {o.share != null && ` · ${pct(o.share)} of cars`}
              </span>
            </div>
            <StintBar stints={o.stints} totalLaps={prediction.total_laps} />
          </div>
        ))}
      </div>
      {prediction.weather?.wet_strategy_trigger && (
        <div className="warning">Rain is forecast during the race: this dry strategy is low confidence.</div>
      )}
      <p className="accuracy">
        <strong>How accurate is this?</strong> Based on {evidenceLabel(prediction.source)}.
        In 2024–25 testing it matched a car's exact strategy {pct(v.sequence_accuracy)} of the time and its number of
        stops {pct(v.stop_count_accuracy)}; the first stop was off by about{' '}
        {v.first_stop_mean_abs_error_laps.toFixed(0)} laps. Treat it as the typical plan, not a per-car call.
      </p>
    </>
  )
}

export function ActualStrategies({ drivers, totalLaps }: { drivers: ActualDriverStrategy[]; totalLaps: number }) {
  const laps = Math.max(totalLaps, ...drivers.flatMap((d) => d.stints.map((s) => s.end_lap)))
  return (
    <div className="strategy-list actual">
      {drivers.map((d) => (
        <div key={d.driver_id} className="strategy-row">
          <div className="strategy-label">
            <span className="pos">{d.finishing_position ? `P${d.finishing_position}` : '–'}</span>
            <DriverName name={d.driver_name} color={d.color_hex} />
          </div>
          <StintBar stints={d.stints} totalLaps={laps} />
        </div>
      ))}
    </div>
  )
}
