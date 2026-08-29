import { useState } from 'react'
import CircuitScene from './components/CircuitScene'
import { TRACKS } from './trackRegistry'
import './App.css'

export default function App() {
  const [selectedId, setSelectedId] = useState(TRACKS[0].id)
  const track = TRACKS.find((t) => t.id === selectedId).data
  const { properties, colors } = track

  return (
    <div className="app-root">
      <CircuitScene key={selectedId} track={track} />

      <div className="hud">
        <h1>{properties.Name}</h1>
        <p className="hud-sub">Drag to orbit &middot; scroll to zoom &middot; {TRACKS.length} circuits</p>

        <div className="track-switcher">
          {TRACKS.map((t) => (
            <button
              key={t.id}
              className={`track-btn ${t.id === selectedId ? 'active' : ''}`}
              onClick={() => setSelectedId(t.id)}
            >
              {t.data.properties.Name.replace(' Circuit', '')}
            </button>
          ))}
        </div>

        <div className="legend">
          <LegendRow label="Sector — fastest" hex={fastestColor(colors).hex} speed={fastestColor(colors).avg_speed_kmh} />
          <LegendRow label="Sector — mid" hex={midColor(colors).hex} speed={midColor(colors).avg_speed_kmh} />
          <LegendRow label="Sector — slowest" hex={slowestColor(colors).hex} speed={slowestColor(colors).avg_speed_kmh} />
        </div>
      </div>
    </div>
  )
}

function findByRole(colors, role) {
  return Object.values(colors).find((c) => c.role === role)
}
function fastestColor(colors) { return findByRole(colors, 'fastest') }
function midColor(colors) { return findByRole(colors, 'mid') }
function slowestColor(colors) { return findByRole(colors, 'slowest') }

function LegendRow({ label, hex, speed }) {
  return (
    <div className="legend-row">
      <span className="swatch" style={{ background: hex, boxShadow: `0 0 8px ${hex}` }} />
      <span className="legend-label">{label}</span>
      <span className="legend-speed">{speed} km/h avg</span>
    </div>
  )
}
