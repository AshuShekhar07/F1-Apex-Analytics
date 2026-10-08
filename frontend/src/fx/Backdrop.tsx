import { SpeedField } from './SpeedField'

/** The fixed F1 backdrop: carbon weave, accent glow (one patch follows the pointer), light
    streaks on a canvas, a racing-grid floor, film grain and faint scanlines. */
export function Backdrop() {
  return (
    <div className="backdrop" aria-hidden="true">
      <div className="glow" />
      <div className="pointer-glow" />
      <SpeedField />
      <div className="floor" />
      <div className="scan" />
      <div className="grain" />
    </div>
  )
}
