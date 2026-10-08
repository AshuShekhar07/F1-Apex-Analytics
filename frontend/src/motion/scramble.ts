/**
 * The timing-screen "decode" effect: text resolves left to right out of random glyphs.
 * It edits the existing text nodes in place (never replaces them), so React keeps owning
 * them; if React changes a node mid-effect, the effect stops and leaves React's text alone.
 */
const GLYPHS = 'ABCDEFGHJKLMNPRSTUVWXYZ0123456789#/\\_<>'

/** The text at `progress` (0..1): the first part final, the rest random glyphs (spaces kept). */
export function scrambleFrame(final: string, progress: number, rand: () => number = Math.random): string {
  const settled = Math.floor(Math.max(0, Math.min(1, progress)) * final.length)
  let out = ''
  for (let i = 0; i < final.length; i++) {
    const ch = final[i]
    out += i < settled || ch.trim() === '' ? ch : GLYPHS[Math.floor(rand() * GLYPHS.length)]
  }
  return out
}

const running = new WeakMap<Element, () => void>()

/** Scramble every text node inside `el` back to itself over `duration` seconds. */
export function scrambleElement(el: Element, duration = 0.45): void {
  running.get(el)?.()
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  const nodes: { node: Text; final: string; written: string }[] = []
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = n as Text
    if (text.nodeValue && text.nodeValue.trim()) nodes.push({ node: text, final: text.nodeValue, written: text.nodeValue })
  }
  if (!nodes.length) return
  const start = performance.now()
  let frame = 0
  const stop = () => {
    cancelAnimationFrame(frame)
    running.delete(el)
    for (const n of nodes) if (n.node.nodeValue === n.written) n.node.nodeValue = n.final
  }
  const step = (now: number) => {
    const p = (now - start) / (duration * 1000)
    for (const n of nodes) {
      if (n.node.nodeValue !== n.written) {
        // React changed this text meanwhile: it wins
        n.final = n.node.nodeValue ?? ''
        n.written = n.final
        continue
      }
      n.written = p >= 1 ? n.final : scrambleFrame(n.final, p)
      n.node.nodeValue = n.written
    }
    if (p < 1) frame = requestAnimationFrame(step)
    else running.delete(el)
  }
  running.set(el, stop)
  frame = requestAnimationFrame(step)
}
