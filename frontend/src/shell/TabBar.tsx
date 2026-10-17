import { NavLink } from 'react-router-dom'

import { openSearch } from './QuickSearch'

const TABS = [
  { to: '/', label: 'Race', end: true, icon: <path d="M6 21V4m0 0h11l-2.5 4L17 12H6" /> },
  { to: '/championship', label: 'Standings', icon: <path d="M3 20h18M5 20v-6h4v6m1 0V8h4v12m1 0v-9h4v9" /> },
  { to: '/results', label: 'Results', icon: <path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" /> },
  { to: '/predict', label: 'Predict', icon: <><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4.5" /><path d="M12 12h.01" /></> },
]

/** Phones: the main sections in a bar along the bottom, in thumb reach; "More" lists every
    section (and searches). Hidden on wider screens, where the header shows them all. */
export function TabBar() {
  return (
    <nav className="tab-bar" aria-label="Sections">
      {TABS.map((t) => (
        <NavLink key={t.to} to={t.to} end={t.end}>
          <svg viewBox="0 0 24 24" aria-hidden="true">{t.icon}</svg>
          <span>{t.label}</span>
        </NavLink>
      ))}
      <button type="button" onClick={() => openSearch(true)}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h10" /><circle cx="18.5" cy="17" r="2.5" /></svg>
        <span>More</span>
      </button>
    </nav>
  )
}
