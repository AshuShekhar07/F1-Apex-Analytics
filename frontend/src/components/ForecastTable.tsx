import { pct } from '../format'
import type { Forecast, RaceResult } from '../types'
import { DriverName } from './DriverName'

function Prob({ value }: { value: number }) {
  return (
    <div className="prob">
      <div className="prob-fill" style={{ width: `${Math.min(100, value * 100)}%` }} />
      <span>{pct(value)}</span>
    </div>
  )
}

/** Forecast per driver; once the race is run, the actual finish is shown next to it. */
export function ForecastTable({ forecast, results }: { forecast: Forecast; results: RaceResult[] }) {
  const actual = new Map(results.map((r) => [r.driver_id, r]))
  const finished = results.length > 0
  const v = forecast.validation.predicted_position
  return (
    <>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th title="Predicted finishing position">Pred.</th>
              <th>Driver</th>
              <th>Grid</th>
              <th>Win</th>
              <th>Podium</th>
              {finished && <th title="Actual finishing position; (+n) = n places worse than predicted, (−n) = better">Actual</th>}
            </tr>
          </thead>
          <tbody>
            {forecast.drivers.map((d) => {
              const a = actual.get(d.driver_id)
              const diff = a?.finishing_position != null ? a.finishing_position - d.predicted_position : null
              return (
                <tr key={d.driver_id}>
                  <td className="pos">P{d.predicted_position}</td>
                  <td><DriverName name={d.driver_name} color={d.color_hex} team={d.team_name} /></td>
                  <td>{d.grid}</td>
                  <td><Prob value={d.p_win} /></td>
                  <td><Prob value={d.p_podium} /></td>
                  {finished && (
                    <td>
                      {a?.finishing_position != null ? `P${a.finishing_position}` : a?.display ?? '–'}
                      {diff != null && diff !== 0 && (
                        <span className={`small ${Math.abs(diff) <= 3 ? 'muted' : 'miss'}`}> ({diff > 0 ? '+' : ''}{diff})</span>
                      )}
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="accuracy">
        <strong>How accurate is this?</strong> Tested on 160 races from 2019–26 using only earlier data: predicted positions were
        off by <strong>{v.mean_abs_error_places.toFixed(1)} places</strong> on average (grid order alone:{' '}
        {v.grid_only_mean_abs_error_places.toFixed(1)}). Win and podium chances are how often drivers from each grid
        slot have won or reached the podium.
      </p>
    </>
  )
}

export function ResultsTable({ results }: { results: RaceResult[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Pos.</th><th>Driver</th><th>Grid</th><th>Time / status</th><th>Pts</th></tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr key={r.driver_id}>
              <td className="pos">{r.finishing_position ?? '–'}</td>
              <td><DriverName name={r.driver_name} color={r.color_hex} team={r.team_name} /></td>
              <td>{r.starting_grid_position || 'Pit'}</td>
              <td>{r.display ?? r.status}</td>
              <td>{r.points ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
