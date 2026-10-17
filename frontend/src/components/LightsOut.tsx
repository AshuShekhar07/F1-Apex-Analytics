/**
 * The loading state: five red start lights come on one by one and go out, like the start of a
 * race, until the data arrives. Reduced motion: the lights stay on, nothing moves.
 */
export function LightsOut({ height = 180, label = 'Loading' }: { height?: number; label?: string }) {
  return (
    <div className="lights-loader" style={{ minHeight: height }} role="status" aria-live="polite">
      <div className="lights" aria-hidden="true">
        {[0, 1, 2, 3, 4].map((i) => <span key={i} style={{ ['--i' as string]: i }}><i /><i /></span>)}
      </div>
      <span className="lights-label">{label}…</span>
    </div>
  )
}
