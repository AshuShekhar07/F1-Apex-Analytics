import { motion } from 'motion/react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api'
import type { Circuit } from '../circuitTypes'
import { CircuitOutline } from '../fx/CircuitOutline'
import { CountUp } from '../fx/CountUp'
import { useAsync } from '../hooks'
import { useAccent } from '../theme/accent'
import { matchTrack } from '../trackMatch'

// F1 timing-screen colours: red, purple (fastest), green (personal best), yellow (flag)
const FEATURES = [
  { to: '/play/winner', tag: 'Game · Race', title: 'Predict the next GP winner', colour: '#e10600',
    text: 'Pick your winner for the next Grand Prix, lock it in, then see what Apex21 gives your driver.' },
  { to: '/play/constructors', tag: 'Game · Season', title: "Predict the Constructors' Champion", colour: '#b026ff',
    text: "Back a team for the title and face the model's championship odds, simulated race by race." },
  { to: '/drivers', tag: 'Grid · 2026', title: 'Drivers 2026', colour: '#00d26a',
    text: "The whole grid: points, wins, podiums, poles and careers, in each team's colours." },
  { to: '/tracks', tag: 'Circuits · 3D', title: 'Tracks', colour: '#ffd200',
    text: 'Spin every circuit in 3D, sector by sector, with its speeds, history and winners.' },
]

// Measured walk-forward: every race predicted only from races before it (see the README).
const STATS = [
  { value: 58.7, decimals: 1, unit: '%', what: 'of races won by our favourite (after qualifying)' },
  { value: 90.4, decimals: 1, unit: '%', what: 'winner was in our top 3 (after qualifying)' },
  { value: 80, decimals: 0, unit: '/80', what: "drivers' title calls right at 90%+ confidence" },
  { value: 167, decimals: 0, unit: '', what: 'races replayed to measure it, 2018–2026' },
]

const line = {
  hidden: { y: '110%' },
  show: (i: number) => ({ y: 0, transition: { delay: 0.15 + i * 0.12, duration: 0.7, ease: [0.2, 0.8, 0.2, 1] as const } }),
}

export function LandingPage() {
  const { preview } = useAccent()
  const next = useAsync(() => api.nextWinnerPrediction(), [])
  const [circuit, setCircuit] = useState<Circuit | null>(null)

  useEffect(() => {
    let live = true
    import('../circuits').then(({ CIRCUITS }) => {
      if (!live) return
      const name = next.data?.track
      const found = name
        ? CIRCUITS.find((c) => matchTrack(c.name, c.location, [{ id: 1, name }]))
        : undefined
      setCircuit(found ?? CIRCUITS.find((c) => c.id === 'sg-2008') ?? CIRCUITS[0])
    })
    return () => {
      live = false
    }
  }, [next.data?.track])

  return (
    <div className="page">
      <section className="hero" style={{ background: 'none', border: 0, padding: 0, backdropFilter: 'none' }}>
        <div>
          <motion.div className="eyebrow" initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }}>
            Apex21 · F1 prediction engine
          </motion.div>
          <h1>
            {['Predict', 'the race.', 'Before'].map((w, i) => (
              <span className="line" key={w}>
                <motion.span style={{ display: 'block' }} variants={line} initial="hidden" animate="show" custom={i}>
                  {w}
                </motion.span>
              </span>
            ))}
            <span className="line">
              <motion.span className="accent" style={{ display: 'block' }} variants={line} initial="hidden" animate="show" custom={3}>
                lights out.
              </motion.span>
            </span>
          </h1>
          <motion.p className="lead" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.8 }}>
            Race winners, the championship and the winning tyre strategy, from a model that is tested on every
            race since 2018 and saves its calls before the start.
          </motion.p>
          <motion.div className="ctas" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 1 }}>
            <Link to="/play/winner" className="btn-sharp">Pick the winner ▸</Link>
            <Link to="/tracks" className="btn-sharp ghost">Explore tracks</Link>
          </motion.div>
        </div>
        <motion.div className="hero-track" initial={{ opacity: 0, scale: 0.92 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 0.9, delay: 0.2 }}>
          {circuit && <CircuitOutline circuit={circuit} />}
          <div className="caption">
            {next.data ? 'Next race' : 'Circuit'}
            <strong>{next.data ? `${next.data.track} · Round ${next.data.round}` : circuit?.name}</strong>
          </div>
        </motion.div>
      </section>

      <div className="stat-strip">
        {STATS.map((s) => (
          <motion.div key={s.what} initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}>
            <div className="num">
              <CountUp to={s.value} decimals={s.decimals} />
              <small>{s.unit}</small>
            </div>
            <div className="what">{s.what}</div>
          </motion.div>
        ))}
      </div>
      <p className="stat-strip-note">Measured on past races, each predicted only from the races before it.</p>

      <div className="section-title">
        <h2>Choose your session</h2>
        <span className="eyebrow">04 modes</span>
      </div>
      <div className="feature-grid">
        {FEATURES.map((f, i) => (
          <motion.div
            key={f.to}
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-60px' }}
            transition={{ delay: i * 0.08, duration: 0.5 }}
            whileHover={{ y: -6 }}
          >
            <Link
              to={f.to}
              className="feature-card"
              style={{ ['--c' as string]: f.colour }}
              onMouseEnter={() => preview(f.colour)}
              onMouseLeave={() => preview(null)}
              onFocus={() => preview(f.colour)}
              onBlur={() => preview(null)}
            >
              <span className="bar" />
              <span className="index">0{i + 1}</span>
              <span className="tag">{f.tag}</span>
              <h3>{f.title}</h3>
              <p>{f.text}</p>
              <span className="go"><span>▸</span></span>
            </Link>
          </motion.div>
        ))}
      </div>
      <div className="more-links">
        <Link to="/championship/2026" className="btn-sharp ghost small">Championship odds</Link>
        <Link to="/next" className="btn-sharp ghost small">Next race forecast</Link>
        <Link to="/season/2026" className="btn-sharp ghost small">2026 results</Link>
      </div>
    </div>
  )
}
