/** The fixed F1 backdrop: carbon weave, accent glow, moving speed streaks and a racing-grid floor. */
export function Backdrop() {
  return (
    <div className="backdrop" aria-hidden="true">
      <div className="glow" />
      <div className="streaks" />
      <div className="floor" />
    </div>
  )
}
