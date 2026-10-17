/** Small colour helpers for team theming. Pure: tested in color.test.ts. */

export const F1_RED = '#e10600'

/** '#abc', 'abc', '#aabbcc' or 'aabbcc' -> '#aabbcc'; anything else -> fallback. */
export function normaliseHex(value: string | null | undefined, fallback = F1_RED): string {
  if (!value) return fallback
  let hex = value.trim().replace(/^#/, '').toLowerCase()
  if (/^[0-9a-f]{3}$/.test(hex)) hex = hex.split('').map((c) => c + c).join('')
  return /^[0-9a-f]{6}$/.test(hex) ? `#${hex}` : fallback
}

export function hexToRgb(value: string): [number, number, number] {
  const hex = normaliseHex(value).slice(1)
  return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number]
}

/** Relative luminance (WCAG), 0 = black, 1 = white. */
export function luminance(value: string): number {
  const [r, g, b] = hexToRgb(value).map((c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** Text colour that reads on top of the given background. */
export function inkOn(value: string): string {
  return luminance(value) > 0.45 ? '#0b0b0f' : '#ffffff'
}

/** A team colour lifted so it glows on a near-black page (very dark colours become visible). */
export function glowColour(value: string): string {
  const [r, g, b] = hexToRgb(value)
  const lum = luminance(value)
  if (lum >= 0.08) return normaliseHex(value)
  const lift = (c: number) => Math.round(c + (255 - c) * 0.35)
  return `#${[lift(r), lift(g), lift(b)].map((c) => c.toString(16).padStart(2, '0')).join('')}`
}
