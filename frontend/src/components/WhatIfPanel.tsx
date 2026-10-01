import { useState } from 'react'

import { api } from '../api'
import { parseLaps, pct, positionChange } from '../format'
import type { Forecast, StrategyPlan, WhatIfResponse } from '../types'
import { DriverName } from './DriverName'

const COMPOUNDS = ['SOFT', 'MEDIUM', 'HARD']

interface Override {
  driverId: number
  sequence: string[]
  stopLaps: string
}

export function WhatIfPanel({ raceId, forecast }: { raceId: number; forecast: Forecast }) {
  const totalLaps = forecast.total_laps ?? 70
  const [scLaps, setScLaps] = useState('')
  const [vscLaps, setVscLaps] = useState('')
  const [override, setOverride] = useState<Override | null>(null)
  const [result, setResult] = useState<WhatIfResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  const drivers = [...forecast.drivers].sort((a, b) => a.grid - b.grid)

  async function run() {
    setError(null)
    try {
      const strategies: Record<number, StrategyPlan> = {}
      if (override) {
        const stops = parseLaps(override.stopLaps, totalLaps - 1)
        if (stops.length !== override.sequence.length - 1) {
          throw new Error(`${override.sequence.length} stints need ${override.sequence.length - 1} stop lap(s)`)
        }
        strategies[override.driverId] = { sequence: override.sequence, stop_laps: stops }
      }
      setRunning(true)
      setResult(await api.whatIf(raceId, {
        safety_car_laps: parseLaps(scLaps, totalLaps),
        vsc_laps: parseLaps(vscLaps, totalLaps),
        strategies,
        simulations: 1000,
      }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRunning(false)
    }
  }

  function startOverride() {
    const first = drivers[0]
    const plan = first.strategy ?? forecast.strategy_alternatives[0]
    setOverride({ driverId: first.driver_id, sequence: plan?.sequence ?? ['MEDIUM', 'HARD'],
                  stopLaps: (plan?.stop_laps ?? [Math.round(totalLaps / 2)]).join(', ') })
  }

  return (
    <div className="what-if">
      <p className="muted">
        Replays the race 1,000 times with and without your changes. The <em>difference</em> between the two is the
        useful part; the absolute numbers are less reliable than the forecast above.
      </p>
      <div className="what-if-form">
        <label>
          Safety Car laps
          <input value={scLaps} onChange={(e) => setScLaps(e.target.value)} placeholder="e.g. 20-23" />
        </label>
        <label>
          Virtual Safety Car laps
          <input value={vscLaps} onChange={(e) => setVscLaps(e.target.value)} placeholder="e.g. 35, 36" />
        </label>
        {!override ? (
          <button type="button" className="secondary" onClick={startOverride}>+ Change one driver's strategy</button>
        ) : (
          <fieldset>
            <legend>Strategy change <button type="button" className="link" onClick={() => setOverride(null)}>remove</button></legend>
            <label>
              Driver
              <select value={override.driverId}
                      onChange={(e) => setOverride({ ...override, driverId: Number(e.target.value) })}>
                {drivers.map((d) => <option key={d.driver_id} value={d.driver_id}>P{d.grid} {d.driver_name}</option>)}
              </select>
            </label>
            <div className="compounds">
              Tyres
              {override.sequence.map((c, i) => (
                <select key={i} value={c} onChange={(e) => {
                  const sequence = [...override.sequence]
                  sequence[i] = e.target.value
                  setOverride({ ...override, sequence })
                }}>
                  {COMPOUNDS.map((x) => <option key={x}>{x}</option>)}
                </select>
              ))}
              <button type="button" className="link" onClick={() => setOverride({ ...override, sequence: [...override.sequence, 'HARD'] })}>+ stint</button>
              {override.sequence.length > 2 && (
                <button type="button" className="link" onClick={() => setOverride({ ...override, sequence: override.sequence.slice(0, -1) })}>− stint</button>
              )}
            </div>
            <label>
              Stop laps
              <input value={override.stopLaps} onChange={(e) => setOverride({ ...override, stopLaps: e.target.value })}
                     placeholder="e.g. 18, 40" />
            </label>
          </fieldset>
        )}
        <button type="button" onClick={run} disabled={running}>{running ? 'Simulating…' : 'Run what-if'}</button>
      </div>
      {error && <div className="error-box">{error}</div>}
      {result && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Driver</th><th>Grid</th><th>Typical finish</th><th title="Change in average finishing position">Avg. change</th><th>Win</th><th>Podium</th></tr>
            </thead>
            <tbody>
              {result.results.map((r) => {
                const change = positionChange(r.expected_position_change)
                return (
                  <tr key={r.driver_id} className={override?.driverId === r.driver_id ? 'highlight' : ''}>
                    <td><DriverName name={r.driver_name ?? `Driver ${r.driver_id}`} color={r.color_hex} /></td>
                    <td>{r.grid}</td>
                    <td>P{r.baseline.median_position} → <strong>P{r.scenario.median_position}</strong></td>
                    <td className={change.tone}>{change.label}</td>
                    <td>{pct(r.baseline.p_win)} → {pct(r.scenario.p_win)}</td>
                    <td>{pct(r.baseline.p_podium)} → {pct(r.scenario.p_podium)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
