import { AnimatePresence, motion } from 'motion/react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api'
import { ErrorBox, Loading } from '../components/Status'
import { OddsBars, ordinal } from '../fx/OddsBars'
import { LightsOut } from '../fx/LightsOut'
import { useAsync } from '../hooks'
import { Chars } from '../motion/Chars'
import { assetUrl, readPick, writePick } from '../storage'
import { useAccent, usePageAccent } from '../theme/accent'
import { normaliseHex } from '../theme/color'

const SEASON = 2026

interface SavedPick {
  teamId: number
  locked: boolean
}

export function PickConstructorsPage() {
  const champ = useAsync(() => api.championship(SEASON), [])
  const roster = useAsync(() => api.roster(SEASON), [])
  const key = `constructors:${SEASON}`
  const [saved, setSaved] = useState<SavedPick | null>(null)
  const [countdown, setCountdown] = useState(false)
  const pick = saved ?? readPick<SavedPick>(key)
  const { preview } = useAccent()

  const teamInfo = useMemo(() => {
    const m = new Map<number, { colour: string | null; logo: string | null; drivers: string[] }>()
    for (const d of roster.data?.drivers ?? []) {
      const t = m.get(d.team.id) ?? { colour: d.team.color_hex, logo: d.team.logo_url, drivers: [] }
      t.drivers.push(d.name.split(' ').slice(-1)[0])
      m.set(d.team.id, t)
    }
    return m
  }, [roster.data])

  const teams = (champ.data?.constructors ?? []).map((t) => ({
    id: t.team_id ?? 0,
    name: t.team_name ?? '?',
    colour: t.color_hex ?? teamInfo.get(t.team_id ?? 0)?.colour ?? null,
    logo: teamInfo.get(t.team_id ?? 0)?.logo ?? null,
    drivers: teamInfo.get(t.team_id ?? 0)?.drivers ?? [],
    p: t.p_title,
    points: t.points_now,
  }))
  const picked = teams.find((t) => t.id === pick?.teamId)
  usePageAccent(picked?.colour ?? null)

  const save = (next: SavedPick | null) => {
    setSaved(next)
    writePick(key, next)
  }

  if (champ.loading) return <div className="page"><Loading what="the championship" /></div>
  if (champ.error) return <div className="page"><ErrorBox error={champ.error} /></div>
  if (!champ.data) return <div className="page"><p className="lead">No championship projection yet.</p></div>

  const sorted = [...teams].sort((a, b) => b.p - a.p)
  const rank = picked ? sorted.findIndex((t) => t.id === picked.id) + 1 : 0
  const c = champ.data

  return (
    <div className="page">
      <div className="game-head">
        <div>
          <span className="eyebrow">Game · {SEASON} season · after round {c.after_round}</span>
          <h1 className="display">
            <Chars text="Who takes the" /> <em><Chars text="constructors'" at={0.25} /></em> <Chars text="title?" at={0.5} />
          </h1>
          <p className="lead">
            Back a team. Apex21 simulates the {c.weekends_left} remaining weekends thousands of times from each
            team's form and gives every team its chance of the title.
          </p>
        </div>
        <span className={`badge ${c.confidence.constructors.level}`}>Confidence: {c.confidence.constructors.level}</span>
      </div>

      <div className="step"><span className="diamond"><span>1</span></span> Back a team</div>
      <div className="pick-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))' }}>
        {teams.map((t, i) => (
          <motion.button
            key={t.id}
            data-scramble
              className={`pick-tile ${t.id === pick?.teamId ? 'picked' : ''}`}
            style={{ ['--c' as string]: normaliseHex(t.colour, '#888888'), minHeight: 72 }}
            onClick={() => !pick?.locked && save({ teamId: t.id, locked: false })}
            data-cursor={pick?.locked ? undefined : 'Back'}
            onMouseEnter={() => !pick?.locked && preview(t.colour)}
            onMouseLeave={() => preview(null)}
            disabled={pick?.locked && t.id !== pick.teamId}
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.04 }}
            whileTap={{ scale: 0.97 }}
          >
            {t.logo ? <img src={assetUrl(t.logo) ?? ''} alt="" style={{ objectFit: 'contain', background: 'transparent' }} /> : null}
            <span className="who">
              <strong>{t.name}</strong>
              <span>{t.drivers.join(' · ') || `${t.points} pts`}</span>
            </span>
            <span className="grid-slot">{t.points}</span>
          </motion.button>
        ))}
      </div>

      <div className="lock-row">
        {countdown ? (
          <LightsOut
            onDone={() => {
              setCountdown(false)
              if (pick) save({ ...pick, locked: true })
            }}
          />
        ) : !pick?.locked ? (
          <button className="btn-sharp" disabled={!pick} onClick={() => setCountdown(true)} data-sound-click="lock" data-cursor="Lock">
            {picked ? `Back ${picked.name}` : 'Pick a team'}
          </button>
        ) : (
          <button className="btn-sharp ghost" onClick={() => save(null)}>Change my pick</button>
        )}
        <span className="muted small">Points shown are this season's so far.</span>
      </div>

      <AnimatePresence>
        {pick?.locked && picked && (
          <motion.div className="reveal chamfer" initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
            <div className="step" style={{ marginTop: 0 }}><span className="diamond"><span>2</span></span> Apex21 says</div>
            <p className="verdict">
              {rank === 1 ? (
                <>Same pick as Apex21: <em>{picked.name}</em> win the title in {(picked.p * 100).toFixed(0)}% of simulations.</>
              ) : (
                <>
                  <em>{picked.name}</em> are the {ordinal(rank)} favourites: title in {(picked.p * 100).toFixed(picked.p < 0.1 ? 1 : 0)}%
                  of simulations. Apex21 backs {sorted[0].name} ({(sorted[0].p * 100).toFixed(0)}%).
                </>
              )}
            </p>
            <OddsBars pickId={picked.id} entries={teams.map((t) => ({ id: t.id, name: t.name, colour: t.colour, value: t.p }))} />
            <p className="muted small" style={{ marginTop: 16 }}>{c.confidence.constructors.basis}</p>
            <div className="lock-row">
              <Link to={`/championship/${SEASON}`} className="btn-sharp small">Full championship odds</Link>
              <Link to="/drivers" className="btn-sharp ghost small">Meet the 2026 grid ▸</Link>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
