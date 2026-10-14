import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Suspense, lazy, useEffect, useLayoutEffect, type ReactNode } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'

import { Intro, useIntroOnce } from './fx/Intro'
import { installMusic } from './fx/music'
import { installAudioUnlock } from './fx/sound'
import { LiveWeekendProvider } from './live'
import { scrollToTop, SmoothScroll } from './motion/SmoothScroll'
import { ChampionshipPage } from './pages/ChampionshipPage'
import { DriversPage } from './pages/DriversPage'
import { PredictPage } from './pages/PredictPage'
import { RaceHubPage } from './pages/RaceHubPage'
import { RacePage } from './pages/RacePage'
import { RecordsPage } from './pages/RecordsPage'
import { ResultsPage } from './pages/ResultsPage'
import { TeamPage, TeamsPage } from './pages/TeamsPage'
import { SiteHeader } from './shell/Header'
import { AccentProvider } from './theme/accent'

// three.js and the circuit files only load when someone opens the tracks
const TracksPage = lazy(() => import('./pages/TracksPage').then((m) => ({ default: m.TracksPage })))

/** A quick fade between pages; the new page opens at its top. */
function Page({ children }: { children: ReactNode }) {
  const reduced = useReducedMotion()
  useLayoutEffect(() => scrollToTop(), [])
  return (
    <motion.div
      initial={{ opacity: 0, y: reduced ? 0 : 8 }}
      animate={{ opacity: 1, y: 0, transition: { duration: 0.25, ease: 'easeOut' } }}
      exit={{ opacity: 0, transition: { duration: 0.12 } }}
    >
      {children}
    </motion.div>
  )
}

function SeasonRedirect() {
  return <Navigate to={`/results/${useParams().year ?? ''}`} replace />
}

export function App() {
  const [intro, introDone] = useIntroOnce()
  const location = useLocation()
  useEffect(() => {
    const offUnlock = installAudioUnlock()
    const offMusic = installMusic()
    return () => {
      offUnlock()
      offMusic()
    }
  }, [])
  return (
    <AccentProvider>
      <LiveWeekendProvider>
        <SmoothScroll>
          {intro && <Intro onDone={introDone} />}
          <SiteHeader />
          <AnimatePresence mode="wait">
            <Routes location={location} key={location.pathname}>
              <Route path="/" element={<Page><RaceHubPage /></Page>} />
              <Route path="/championship/:year?" element={<Page><ChampionshipPage /></Page>} />
              <Route path="/results/:year?" element={<Page><ResultsPage /></Page>} />
              <Route path="/race/:id" element={<Page><RacePage /></Page>} />
              <Route path="/drivers" element={<Page><DriversPage /></Page>} />
              <Route path="/teams/:year?" element={<Page><TeamsPage /></Page>} />
              <Route path="/teams/:year/:id" element={<Page><TeamPage /></Page>} />
              <Route path="/records" element={<Page><RecordsPage /></Page>} />
              <Route
                path="/tracks"
                element={<Page><Suspense fallback={<div className="page muted">Loading circuits…</div>}><TracksPage /></Suspense></Page>}
              />
              {/* old addresses */}
              <Route path="/season/:year" element={<SeasonRedirect />} />
              <Route path="/seasons" element={<Navigate to="/results" replace />} />
              <Route path="/next" element={<Navigate to="/" replace />} />
              <Route path="/predict" element={<Page><PredictPage /></Page>} />
              <Route path="/play/winner" element={<Navigate to="/predict" replace />} />
              <Route path="/play/constructors" element={<Navigate to="/predict" replace />} />
              <Route
                path="*"
                element={<Page><div className="page"><h1 className="h1">Off track</h1><p className="muted">This page does not exist.</p><Link to="/" className="btn">Back to the race</Link></div></Page>}
              />
            </Routes>
          </AnimatePresence>
          <footer className="site-foot">
            <div className="row">
              <span>Apex21 · Formula 1 predictions from public timing data, 2018 onwards. Race predictions are saved before each race and scored after it.</span>
              <span>Independent fan project, not affiliated with Formula 1, the FIA or any team.</span>
            </div>
          </footer>
        </SmoothScroll>
      </LiveWeekendProvider>
    </AccentProvider>
  )
}
