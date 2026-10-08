import { AnimatePresence, motion } from 'motion/react'
import { Suspense, lazy, useEffect, useRef, type ReactNode } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { Backdrop } from './fx/Backdrop'
import { Intro, useIntroOnce } from './fx/Intro'
import { ChampionshipPage } from './pages/ChampionshipPage'
import { DriversPage } from './pages/DriversPage'
import { LandingPage } from './pages/LandingPage'
import { NextRacePage } from './pages/NextRacePage'
import { PickConstructorsPage } from './pages/PickConstructorsPage'
import { PickWinnerPage } from './pages/PickWinnerPage'
import { RacePage } from './pages/RacePage'
import { SEASONS, SeasonPage } from './pages/SeasonPage'
import { AccentProvider } from './theme/accent'

// three.js and the circuit files only load when someone opens the tracks
const TracksPage = lazy(() => import('./pages/TracksPage').then((m) => ({ default: m.TracksPage })))

const NAV = [
  { to: '/play/winner', label: 'Pick the winner' },
  { to: '/play/constructors', label: 'Constructors' },
  { to: '/drivers', label: 'Drivers 2026' },
  { to: '/tracks', label: 'Tracks' },
  { to: `/championship/${SEASONS[0]}`, label: 'Championship' },
  { to: `/season/${SEASONS[0]}`, label: 'Results' },
]

function Page({ children }: { children: ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 18, filter: 'blur(6px)' }}
      animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      exit={{ opacity: 0, y: -12, filter: 'blur(6px)' }}
      transition={{ duration: 0.35, ease: [0.2, 0.8, 0.2, 1] }}
    >
      {children}
    </motion.div>
  )
}

export function App() {
  const [intro, introDone] = useIntroOnce()
  const location = useLocation()
  const nav = useRef<HTMLElement>(null)
  // on a phone the menu is one swipeable row: keep the current page's button in view
  useEffect(() => {
    nav.current?.querySelector('a.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [location.pathname])
  return (
    <AccentProvider>
      <Backdrop />
      {intro && <Intro onDone={introDone} />}
      <header className="top-bar">
        <Link to="/" aria-label="Apex21 home">
          <img className="brand-logo" src="/apex21-logo.png" alt="Apex21" />
        </Link>
        <nav ref={nav}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? 'active' : '')}>
              {n.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <AnimatePresence mode="wait">
        <Routes location={location} key={location.pathname}>
          <Route path="/" element={<Page><LandingPage /></Page>} />
          <Route path="/play/winner" element={<Page><PickWinnerPage /></Page>} />
          <Route path="/play/constructors" element={<Page><PickConstructorsPage /></Page>} />
          <Route path="/drivers" element={<Page><DriversPage /></Page>} />
          <Route
            path="/tracks"
            element={<Page><Suspense fallback={<div className="page muted">Loading circuits…</div>}><TracksPage /></Suspense></Page>}
          />
          <Route path="/season/:year" element={<Page><SeasonPage /></Page>} />
          <Route path="/race/:id" element={<Page><RacePage /></Page>} />
          <Route path="/next" element={<Page><NextRacePage /></Page>} />
          <Route path="/championship/:year" element={<Page><ChampionshipPage /></Page>} />
          <Route path="/seasons" element={<Navigate to={`/season/${SEASONS[0]}`} replace />} />
          <Route path="*" element={<Page><div className="page"><h1 className="display">Off track</h1><Link to="/" className="btn-sharp">Back to the pits</Link></div></Page>} />
        </Routes>
      </AnimatePresence>
    </AccentProvider>
  )
}
