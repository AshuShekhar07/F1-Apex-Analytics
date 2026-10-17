/**
 * The layers behind every page: a glow in the page's accent colour (team colour where a page
 * sets one, F1 red otherwise), a faint carbon-fibre weave and telemetry grid at the top, film
 * grain and a vignette. Pure CSS, fixed in place, never in the way of clicks.
 */
export function Backdrop() {
  return (
    <div className="backdrop" aria-hidden="true">
      <div className="bd-glow" />
      <div className="bd-carbon" />
      <div className="bd-grid" />
      <div className="bd-grain" />
      <div className="bd-vignette" />
    </div>
  )
}
