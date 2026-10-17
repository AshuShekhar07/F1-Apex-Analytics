import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '../api'
import { lockScroll } from '../motion/SmoothScroll'
import { driverCode } from '../names'
import { search, type SearchItem, type SearchKind } from '../search'
import { SEASONS } from '../seasons'
import { glowColour, normaliseHex } from '../theme/color'

const PAGES: SearchItem[] = [
  { kind: 'page', title: 'Race weekend', sub: 'The next race: win chances, strategy and sessions', words: 'home hub next prediction', to: '/' },
  { kind: 'page', title: 'Championship', sub: 'Standings and title chances', words: 'standings title drivers constructors', to: '/championship' },
  { kind: 'page', title: 'Results', sub: 'Every race of a season', words: 'season calendar races', to: '/results' },
  { kind: 'page', title: 'Teams', sub: 'Every team of a season', words: 'constructors', to: '/teams' },
  { kind: 'page', title: 'Drivers', sub: `The ${SEASONS[0]} drivers`, words: 'roster teammates wet weather', to: '/drivers' },
  { kind: 'page', title: 'Tracks', sub: 'Every circuit in 3D', words: 'circuits map', to: '/tracks' },
  { kind: 'page', title: 'Records', sub: 'Streaks, hat-tricks and the prediction record', words: 'streaks hat tricks', to: '/records' },
  { kind: 'page', title: 'Cars & liveries', sub: 'Every car since 2018', words: 'liveries cars', to: '/liveries' },
  { kind: 'page', title: 'Compare two cars', sub: 'A fair comparison across eras', words: 'liveries cars versus vs', to: '/liveries?view=compare' },
  { kind: 'page', title: 'Best cars ranking', sub: 'Every car against its own season', words: 'liveries cars dominant', to: '/liveries?view=ranking' },
  { kind: 'page', title: 'Predict', sub: 'Make your picks for the next race', words: 'game picks play', to: '/predict' },
]

const KIND_LABEL: Record<SearchKind, string> = {
  page: 'Page', race: 'Race', driver: 'Driver', team: 'Team', car: 'Car', track: 'Circuit', season: 'Season',
}

const recent = (season: number) => (season - 2000) / 100

/** Everything that can be found, loaded the first time the search opens. A source that fails
    is left out, and the next opening tries again. */
let indexOnce: Promise<SearchItem[]> | null = null
function loadIndex(): Promise<SearchItem[]> {
  indexOnce ??= (async () => {
    let failed = false
    const safe = <T,>(p: Promise<T>) => p.catch(() => {
      failed = true
      return null
    })
    const [roster, cars, circuits, ...seasons] = await Promise.all([
      safe(api.roster(SEASONS[0])), safe(api.cars()), safe(import('../circuits')), ...SEASONS.map((s) => safe(api.races(s))),
    ])
    if (failed) indexOnce = null
    const items: SearchItem[] = [...PAGES]
    for (const s of SEASONS) {
      items.push({ kind: 'season', title: `${s} championship`, sub: 'Standings and title chances', words: 'season standings', to: `/championship/${s}`, rank: recent(s) })
      items.push({ kind: 'season', title: `${s} results`, sub: 'Every race of the season', words: 'season calendar races', to: `/results/${s}`, rank: recent(s) })
    }
    const teams = new Map<number, SearchItem>()
    for (const d of roster?.drivers ?? []) {
      items.push({ kind: 'driver', title: d.name, sub: d.team.name, words: `${d.nationality ?? ''} ${d.number ?? ''} ${driverCode(d.name)}`,
        to: `/drivers?driver=${d.id}`, colour: d.team.color_hex })
      teams.set(d.team.id, { kind: 'team', title: d.team.name, sub: `${SEASONS[0]} team`, words: 'constructor',
        to: `/teams/${SEASONS[0]}/${d.team.id}`, colour: d.team.color_hex })
    }
    items.push(...teams.values())
    for (const races of seasons) {
      for (const r of races ?? []) {
        items.push({ kind: 'race', title: `${r.season_year} ${r.track_name}`,
          sub: `Round ${r.round_number}${r.winner_name ? ` · won by ${r.winner_name}` : ''}`,
          words: `${r.country ?? ''} grand prix gp ${r.winner_name ?? ''}`, to: `/race/${r.id}`, colour: r.winner_color_hex, rank: recent(r.season_year) })
      }
    }
    for (const c of circuits?.CIRCUITS ?? []) {
      items.push({ kind: 'track', title: c.name, sub: c.location, words: 'circuit track', to: `/tracks?circuit=${c.id}` })
    }
    for (const c of cars?.cars ?? []) {
      items.push({ kind: 'car', title: `${c.season} ${c.team_name ?? ''}`, sub: [c.car, c.pu].filter(Boolean).join(' · '),
        words: `${c.car ?? ''} car livery ${c.drivers.map((d) => d.name ?? '').join(' ')}`,
        to: `/liveries?season=${c.season}#${c.key}`, colour: c.color_hex, rank: recent(c.season) })
    }
    return items
  })()
  return indexOnce
}

/** Opens the quick search from anywhere (a button, the phone tab bar). As a menu (the tab bar's
    "More") it lists every section without focusing the box, so no phone keyboard pops up. */
export function openSearch(menu = false): void {
  window.dispatchEvent(new CustomEvent('apex:search', { detail: { menu } }))
}

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))

/** Quick search over pages, races, drivers, teams, cars and circuits: Ctrl+K (⌘K) or "/". */
export function QuickSearch() {
  const [open, setOpen] = useState<false | { menu: boolean }>(false)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === 'k' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault()
        setOpen((o) => (o ? false : { menu: false }))
      } else if (e.key === '/' && !typing(e.target)) {
        e.preventDefault()
        setOpen({ menu: false })
      }
    }
    const onOpen = (e: Event) => setOpen({ menu: !!(e as CustomEvent<{ menu?: boolean }>).detail?.menu })
    window.addEventListener('keydown', onKey)
    window.addEventListener('apex:search', onOpen)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('apex:search', onOpen)
    }
  }, [])
  return open ? <Palette menu={open.menu} onClose={() => setOpen(false)} /> : null
}

function Palette({ menu, onClose }: { menu: boolean; onClose: () => void }) {
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const [items, setItems] = useState<SearchItem[] | null>(null)
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLUListElement>(null)

  useEffect(() => {
    let live = true
    loadIndex().then((x) => live && setItems(x))
    return () => {
      live = false
    }
  }, [])
  useEffect(() => {
    const before = document.activeElement
    if (!menu) input.current?.focus()
    lockScroll(true)
    return () => {
      lockScroll(false)
      if (before instanceof HTMLElement) before.focus()
    }
  }, [menu])

  const results = useMemo(() => (q.trim() ? search(items ?? PAGES, q) : PAGES), [q, items])
  useEffect(() => setActive(0), [q])
  useEffect(() => {
    // keep the highlighted result in view inside the list only
    const box = list.current
    const row = box?.children[active] as HTMLElement | undefined
    if (!box || !row) return
    if (row.offsetTop < box.scrollTop) box.scrollTop = row.offsetTop
    else if (row.offsetTop + row.offsetHeight > box.scrollTop + box.clientHeight) box.scrollTop = row.offsetTop + row.offsetHeight - box.clientHeight
  }, [active, results])

  const go = (item: SearchItem | undefined) => {
    if (!item) return
    onClose()
    navigate(item.to)
  }
  const onKey = (e: ReactKeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(results.length - 1, a + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(0, a - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      go(results[active])
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    } else if (e.key === 'Tab') {
      e.preventDefault()                                   // focus stays in the search box
    }
  }

  return (
    <div className="qs-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()} data-lenis-prevent
         onKeyDown={(e) => e.key === 'Escape' && onClose()}>
      <div className="qs-box" role="dialog" aria-modal="true" aria-label="Search">
        <div className="qs-input">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5 21 21" /></svg>
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}
                 placeholder="Search races, drivers, teams, cars, circuits…" aria-label="Search"
                 role="combobox" aria-expanded="true" aria-controls="qs-results" aria-autocomplete="list"
                 aria-activedescendant={results[active] ? `qs-${active}` : undefined} autoComplete="off" spellCheck={false} />
          <button type="button" className="qs-esc" onClick={onClose} aria-label="Close search">Esc</button>
        </div>
        <ul className="qs-results" id="qs-results" role="listbox" ref={list} aria-label="Results">
          {results.map((r, i) => (
            <li key={`${r.kind}-${r.to}`} id={`qs-${i}`} role="option" aria-selected={i === active}
                className={i === active ? 'on' : ''} onMouseMove={() => i !== active && setActive(i)} onClick={() => go(r)}>
              <span className="qs-bar" style={{ background: r.colour ? glowColour(normaliseHex(r.colour, '#888888')) : undefined }} />
              <span className="qs-text"><b>{r.title}</b>{r.sub && <span>{r.sub}</span>}</span>
              <span className="qs-kind">{KIND_LABEL[r.kind]}</span>
            </li>
          ))}
          {q.trim() && !results.length && (
            <li className="qs-none" role="presentation">{items ? `Nothing found for “${q.trim()}”.` : 'Loading everything to search…'}</li>
          )}
        </ul>
        <div className="qs-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> close</span></div>
      </div>
    </div>
  )
}
