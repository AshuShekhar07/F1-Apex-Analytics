import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { F1_RED, glowColour, hexToRgb, inkOn, normaliseHex } from './color'

interface AccentApi {
  accent: string
  /** Temporarily theme the page in a team colour (hover); null returns to the page colour. */
  preview: (hex: string | null | undefined) => void
  /** The page's own colour (e.g. the picked team); null returns to F1 red. */
  setBase: (hex: string | null | undefined) => void
}

const AccentContext = createContext<AccentApi>({ accent: F1_RED, preview: () => {}, setBase: () => {} })

/** Drives the --accent CSS variables the whole site (and the animated backdrop) is painted with. */
export function AccentProvider({ children }: { children: ReactNode }) {
  const [base, setBaseState] = useState(F1_RED)
  const [hover, setHover] = useState<string | null>(null)
  const accent = hover ?? base

  useEffect(() => {
    const root = document.documentElement
    const glow = glowColour(accent)
    root.style.setProperty('--accent', glow)
    root.style.setProperty('--accent-raw', accent)
    root.style.setProperty('--accent-rgb', hexToRgb(glow).join(', '))
    root.style.setProperty('--accent-ink', inkOn(glow))
  }, [accent])

  const preview = useCallback((hex: string | null | undefined) => setHover(hex ? normaliseHex(hex) : null), [])
  const setBase = useCallback((hex: string | null | undefined) => setBaseState(hex ? normaliseHex(hex) : F1_RED), [])
  const api = useMemo(() => ({ accent, preview, setBase }), [accent, preview, setBase])
  return <AccentContext.Provider value={api}>{children}</AccentContext.Provider>
}

export function useAccent(): AccentApi {
  return useContext(AccentContext)
}

/** Sets the page colour while a page is mounted and resets it on leave. */
export function usePageAccent(hex: string | null | undefined) {
  const { setBase, preview } = useAccent()
  useEffect(() => {
    setBase(hex)
    return () => {
      setBase(null)
      preview(null)
    }
  }, [hex, setBase, preview])
}
