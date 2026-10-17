/** Is a finished touch a swipe? Pure. A swipe moves at least 60 px sideways, at least twice as
    far sideways as up or down, within 0.7 s; anything else (a scroll, a tap, a slow drag) is not. */
export function swipeDirection(dx: number, dy: number, ms: number): 'left' | 'right' | null {
  if (Math.abs(dx) < 60 || Math.abs(dx) < 2 * Math.abs(dy) || ms > 700) return null
  return dx < 0 ? 'left' : 'right'
}

/** The neighbour of `current` in `order` the swipe moves to (left = next, right = previous),
    skipping unavailable ones; null at either end. */
export function swipeTarget<T>(order: T[], current: T, dir: 'left' | 'right', available: (x: T) => boolean = () => true): T | null {
  const step = dir === 'left' ? 1 : -1
  for (let i = order.indexOf(current) + step; i >= 0 && i < order.length; i += step) if (available(order[i])) return order[i]
  return null
}
