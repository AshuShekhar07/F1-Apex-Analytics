import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api'
import type { Circuit } from '../circuitTypes'
import { CircuitOutline } from '../fx/CircuitOutline'
import { Marquee } from '../fx/Marquee'
import { TimingTower } from '../fx/TimingTower'
import { useAsync } from '../hooks'
import { Chars } from '../motion/Chars'
import { gsap, reducedMotion, ScrollTrigger, useGSAP } from '../motion/gsap'
import { useReveal } from '../motion/reveal'
import { afterIntro, PAGE_ENTER_DELAY } from '../motion/stage'
import { driverCode, splitName } from '../names'
import { assetUrl } from '../storage'
import { useAccent } from '../theme/accent'
import { glowColour, normaliseHex } from '../theme/color'
import { matchTrack } from '../trackMatch'
import type { Championship, Roster, WinnerPrediction } from '../types'

// F1 timing-screen colours: red, purple (fastest), green (personal best), yellow (flag)
const FEATURES = [
  { kind: 'winner', to: '/play/winner', tag: 'Game · Race', title: 'Predict the next GP winner', colour: '#e10600',
    text: 'Pick your winner for the next Grand Prix, lock it in, then see what Apex21 gives your driver.' },
  { kind: 'constructors', to: '/play/constructors', tag: 'Game · Season', title: "Predict the Constructors' Champion", colour: '#b026ff',
    text: "Back a team for the title and face the model's championship odds, simulated race by race." },
  { kind: 'drivers', to: '/drivers', tag: 'Grid · 2026', title: 'Drivers 2026', colour: '#00d26a',
    text: "The whole grid: points, wins, podiums, poles and careers, in each team's colours." },
  { kind: 'tracks', to: '/tracks', tag: 'Circuits · 3D', title: 'Tracks', colour: '#ffd200',
    text: 'Spin every circuit in 3D, sector by sector, with its speeds, history and winners.' },
] as const

// Measured walk-forward: every race predicted only from races before it (see the README).
const STATS = [
  { value: 58.7, decimals: 1, unit: '%', fill: 0.587, what: 'of races won by our favourite (after qualifying)' },
  { value: 90.4, decimals: 1, unit: '%', fill: 0.904, what: 'winner was in our top 3 (after qualifying)' },
  { value: 80, decimals: 0, unit: '/80', fill: 1, what: "drivers' title calls right at 90%+ confidence" },
  { value: 167, decimals: 0, unit: '', fill: null, what: 'races replayed to measure it, 2018–2026' },
]

export function LandingPage() {
  const { preview } = useAccent()
  const scope = useRef<HTMLDivElement>(null)
  const pin = useRef<HTMLElement>(null)
  const track = useRef<HTMLDivElement>(null)
  const next = useAsync(() => api.nextWinnerPrediction(), [])
  const champ = useAsync(() => api.championship(2026), [])
  const roster = useAsync(() => api.roster(2026), [])
  const [circuit, setCircuit] = useState<Circuit | null>(null)

  useEffect(() => {
    let live = true
    import('../circuits').then(({ CIRCUITS }) => {
      if (!live) return
      const name = next.data?.track
      const found = name ? CIRCUITS.find((c) => matchTrack(c.name, c.location, [{ id: 1, name }])) : undefined
      setCircuit(found ?? CIRCUITS.find((c) => c.id === 'sg-2008') ?? CIRCUITS[0])
    })
    return () => {
      live = false
    }
  }, [next.data?.track])

  useReveal(scope, [next.data, champ.data, roster.data])

  // hero: copy and broadcast frame fade in after the headline starts
  useGSAP(
    () => {
      if (reducedMotion()) return
      const items = gsap.utils.toArray<HTMLElement>('[data-hero-fade]')
      gsap.set(items, { opacity: 0, y: 30 })
      return afterIntro(() => gsap.to(items, { opacity: 1, y: 0, duration: 1, ease: 'expo.out', stagger: 0.12, delay: PAGE_ENTER_DELAY + 0.55 }))
    },
    { scope },
  )

  // hero parallax on scroll: the headline drifts up faster than the broadcast frame
  useGSAP(
    () => {
      if (reducedMotion()) return
      gsap.to('.hero-copy', { yPercent: -18, ease: 'none', scrollTrigger: { trigger: '.hero-x', start: 'top top', end: 'bottom top', scrub: true } })
      gsap.to('.hero-broadcast', { yPercent: -6, ease: 'none', scrollTrigger: { trigger: '.hero-x', start: 'top top', end: 'bottom top', scrub: true } })
      gsap.to('.outro-mark', { xPercent: -12, ease: 'none', scrollTrigger: { trigger: '.outro', start: 'top bottom', end: 'bottom bottom', scrub: true } })
    },
    { scope },
  )

  // stats: count up and fill their bars when they arrive
  useGSAP(
    () => {
      gsap.utils.toArray<HTMLElement>('.stat-cell').forEach((cell) => {
        const num = cell.querySelector<HTMLElement>('[data-count]')
        const bar = cell.querySelector<HTMLElement>('.fill')
        const to = Number(num?.dataset.count ?? 0)
        const decimals = Number(num?.dataset.decimals ?? 0)
        if (reducedMotion()) {
          if (num) num.textContent = to.toFixed(decimals)
          if (bar) gsap.set(bar, { scaleX: Number(bar.dataset.fill) })
          return
        }
        const counter = { v: 0 }
        ScrollTrigger.create({
          trigger: cell,
          start: 'top 85%',
          once: true,
          onEnter: () => {
            gsap.to(counter, { v: to, duration: 1.8, ease: 'expo.out', onUpdate: () => num && (num.textContent = counter.v.toFixed(decimals)) })
            if (bar) gsap.fromTo(bar, { scaleX: 0 }, { scaleX: Number(bar.dataset.fill), duration: 1.6, ease: 'expo.out', delay: 0.15 })
          },
        })
      })
    },
    { scope },
  )

  // the four modes: pinned, scrolled sideways; each panel paints the page in its colour
  useGSAP(
    () => {
      const mm = gsap.matchMedia()
      mm.add('(min-width: 900px) and (prefers-reduced-motion: no-preference)', () => {
        const strip = track.current
        if (!strip || !pin.current) return
        const distance = () => Math.max(0, strip.scrollWidth - window.innerWidth + 80)
        const slide = gsap.to(strip, {
          x: () => -distance(),
          ease: 'none',
          scrollTrigger: {
            trigger: pin.current,
            start: 'top top',
            end: () => `+=${distance()}`,
            pin: true,
            scrub: 0.9,
            invalidateOnRefresh: true,
            onLeave: () => preview(null),
            onLeaveBack: () => preview(null),
          },
        })
        gsap.utils.toArray<HTMLElement>('.feature-panel', strip).forEach((panel) => {
          ScrollTrigger.create({
            trigger: panel,
            containerAnimation: slide,
            start: 'left 55%',
            end: 'right 45%',
            onToggle: (self) => self.isActive && preview(panel.dataset.colour),
          })
          gsap.fromTo(panel.querySelector('.index'), { xPercent: 40 }, {
            xPercent: -40, ease: 'none',
            scrollTrigger: { trigger: panel, containerAnimation: slide, start: 'left right', end: 'right left', scrub: true },
          })
          const visual = panel.querySelectorAll('.visual > *')
          if (visual.length) {
            gsap.from(visual, {
              y: 40, opacity: 0, stagger: 0.06, duration: 0.8, ease: 'expo.out',
              scrollTrigger: { trigger: panel, containerAnimation: slide, start: 'left 75%' },
            })
          }
        })
        return () => preview(null)
      })
      return () => mm.revert()
    },
    { scope, dependencies: [next.data, champ.data, roster.data], revertOnUpdate: true },
  )

  const p = next.data
  const ticker = tickerItems(p)

  return (
    <div className="landing" ref={scope}>
      <section className="hero-x">
        <div className="hero-copy">
          <span className="eyebrow" data-scramble>Apex21 · F1 prediction engine</span>
          <h1 className="mega">
            <span className="row"><Chars text="Predict" /></span>
            <span className="row"><Chars text="the race." at={0.12} /></span>
            <span className="row"><Chars text="Before" at={0.24} /></span>
            <span className="row accent"><Chars text="lights out." at={0.34} /></span>
          </h1>
          <p className="lead" data-hero-fade>
            Race winners, the championship and the winning tyre strategy, from a model that is tested on every
            race since 2018 and saves its calls before the start.
          </p>
          <div className="ctas" data-hero-fade>
            <Link to="/play/winner" className="btn-sharp" data-cursor="Play">Pick the winner ▸</Link>
            <Link to="/tracks" className="btn-sharp ghost" data-cursor="Explore">Explore tracks</Link>
          </div>
        </div>

        <div className="hero-broadcast hud" data-hero-fade>
          <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
          <div className="hud-top">
            <span className="live-dot" />
            <span>{p ? `Next race · Round ${p.round} · ${p.season}` : 'Circuit'}</span>
          </div>
          <div className="hero-circuit">{circuit && <CircuitOutline circuit={circuit} />}</div>
          {p && <TimingTower prediction={p} />}
          <div className="hud-bottom">
            <strong>{p ? p.track : circuit?.name}</strong>
            {p && <span>{new Date(`${p.race_date}T12:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}</span>}
          </div>
        </div>

        <div className="scroll-cue" aria-hidden="true"><span>Scroll</span><i /></div>
      </section>

      <Marquee items={ticker} />

      <section className="stats-x">
        {STATS.map((s) => (
          <div className="stat-cell" key={s.what} data-reveal>
            <div className="num">
              <span data-count={s.value} data-decimals={s.decimals}>{reducedMotion() ? s.value.toFixed(s.decimals) : '0'}</span>
              <small>{s.unit}</small>
            </div>
            {s.fill != null && <div className="gauge"><span className="fill" data-fill={s.fill} /></div>}
            <div className="what">{s.what}</div>
          </div>
        ))}
        <p className="stat-note" data-reveal>Measured on past races, each predicted only from the races before it.</p>
      </section>

      <section className="modes" ref={pin}>
        <div className="modes-head">
          <span className="eyebrow">Choose your session</span>
          <h2 className="section-h"><Chars text="Four ways to play" play="scroll" /></h2>
        </div>
        <div className="modes-track" ref={track}>
          {FEATURES.map((f, i) => (
            <Link
              key={f.to}
              to={f.to}
              className="feature-panel"
              data-colour={f.colour}
              data-cursor="Enter"
              style={{ ['--c' as string]: f.colour }}
              onMouseEnter={() => preview(f.colour)}
              onMouseLeave={() => preview(null)}
              onFocus={() => preview(f.colour)}
              onBlur={() => preview(null)}
              data-reveal
            >
              <span className="index">0{i + 1}</span>
              <span className="tag">{f.tag}</span>
              <h3>{f.title}</h3>
              <p>{f.text}</p>
              <div className="visual">
                <ModeVisual kind={f.kind} prediction={p} champ={champ.data ?? null} roster={roster.data ?? null} circuit={circuit} />
              </div>
              <span className="go"><span>▸</span></span>
            </Link>
          ))}
        </div>
      </section>

      <section className="outro">
        <div className="more-links" data-reveal>
          <Link to="/championship/2026" className="btn-sharp ghost small">Championship odds</Link>
          <Link to="/next" className="btn-sharp ghost small">Next race forecast</Link>
          <Link to="/season/2026" className="btn-sharp ghost small">2026 results</Link>
        </div>
        <img className="outro-mark" src="/apex21-logo.png" alt="" aria-hidden="true" />
        <p className="muted small" data-reveal>Model estimates from public F1 timing data, 2018–2026. Every prediction is saved before the race.</p>
      </section>
    </div>
  )
}

function tickerItems(p: WinnerPrediction | null | undefined): string[] {
  const items = ['Favourite won 58.7% of races since 2018', 'Winner in our top 3: 90.4%']
  if (!p) return ['Apex21 · F1 prediction engine', ...items]
  const fav = p.predicted_winner
  return [
    `Next race · ${p.track}`,
    `Round ${p.round} · ${p.season}`,
    `Apex21 favourite · ${fav.driver_name ?? '?'} ${(fav.p_win * 100).toFixed(0)}%`,
    `Model confidence · ${p.confidence.level}`,
    ...items,
  ]
}

/** A small live picture of each mode, from real data (nothing shown when it isn't loaded). */
function ModeVisual({ kind, prediction, champ, roster, circuit }: {
  kind: (typeof FEATURES)[number]['kind']
  prediction: WinnerPrediction | null | undefined
  champ: Championship | null
  roster: Roster | null
  circuit: Circuit | null
}) {
  if (kind === 'winner' && prediction) {
    const stage = prediction.stages[prediction.stage] ?? Object.values(prediction.stages)[0]
    const top = [...(stage?.drivers ?? [])].sort((a, b) => b.p_win - a.p_win).slice(0, 3)
    const order = [top[1], top[0], top[2]].filter(Boolean)
    return (
      <div className="podium">
        {order.map((d) => (
          <div key={d.driver_id} className={d === top[0] ? 'first' : ''} style={{ ['--h' as string]: `${30 + d.p_win * 160}%` }}>
            <span className="code">{driverCode(d.driver_name ?? '???')}</span>
            <span className="step" style={{ background: glowColour(normaliseHex(d.color_hex, '#888')) }}>{(d.p_win * 100).toFixed(0)}%</span>
          </div>
        ))}
      </div>
    )
  }
  if (kind === 'constructors' && champ?.constructors?.length) {
    const teams = [...champ.constructors].sort((a, b) => b.p_title - a.p_title).slice(0, 4)
    return (
      <div className="mini-bars">
        {teams.map((t) => (
          <div key={t.team_id ?? t.team_name}>
            <span>{t.team_name}</span>
            <span className="bar"><i style={{ width: `${Math.max(2, t.p_title * 100)}%`, background: glowColour(normaliseHex(t.color_hex, '#888')) }} /></span>
            <b>{(t.p_title * 100).toFixed(0)}%</b>
          </div>
        ))}
      </div>
    )
  }
  if (kind === 'drivers' && roster?.drivers?.length) {
    return (
      <div className="mini-grid">
        {roster.drivers.slice(0, 3).map((d) => {
          const photo = assetUrl(d.photo_url)
          return (
            <div key={d.id} style={{ ['--c' as string]: glowColour(normaliseHex(d.team.color_hex, '#888')) }}>
              {photo ? <img src={photo} alt="" loading="lazy" /> : <span className="ph">{driverCode(d.name)}</span>}
              <b>{splitName(d.name)[1]}</b>
              <small>{d.season.points} pts</small>
            </div>
          )
        })}
      </div>
    )
  }
  if (kind === 'tracks' && circuit) {
    return (
      <div className="mini-circuit">
        <CircuitOutline circuit={circuit} colour="#ffd200" />
      </div>
    )
  }
  return null
}
