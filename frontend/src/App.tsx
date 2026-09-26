import { Link, Navigate, Route, Routes } from 'react-router-dom'

import { RacePage } from './pages/RacePage'
import { SEASONS, SeasonPage } from './pages/SeasonPage'

export function App() {
  return (
    <>
      <header className="top-bar">
        <Link to="/" className="brand">APEX<span>21</span></Link>
        <nav>
          <Link to={`/season/${SEASONS[0]}`}>Seasons</Link>
        </nav>
      </header>
      <Routes>
        <Route path="/" element={<Navigate to={`/season/${SEASONS[0]}`} replace />} />
        <Route path="/season/:year" element={<SeasonPage />} />
        <Route path="/race/:id" element={<RacePage />} />
        <Route path="*" element={<main><h1>Page not found</h1><Link to="/">Back to the season</Link></main>} />
      </Routes>
    </>
  )
}
