import { AnimatePresence, motion } from 'motion/react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api'
import { ErrorBox, Loading } from '../components/Status'
import { LightsOut } from '../fx/LightsOut'
import { OddsBars, ordinal } from '../fx/OddsBars'
import { useAsync } from '../hooks'
import { Chars } from '../motion/Chars'
import { splitName } from '../names'
import { readPick, writePick } from '../storage'
import { useAccent, usePageAccent } from '../theme/accent'
import { normaliseHex } from '../theme/color'

interface SavedPick {
  driverId: number
  locked: boolean
}

export function PickWinnerPage() {
  const prediction = useAsync(() => api.nextWinnerPrediction(), [])
  const p = prediction.data
  const roster = useAsync(() => (p ? api.roster(p.season) : Promise.resolve(null)), [p?.season])
  const key = p ? `winner:${p.race_id}` : null
  const [saved, setSaved] = useState<SavedPick | null>(null)
  const [countdown, setCountdown] = useState(false)
  const pick = saved ?? (key ? readPick<SavedPick>(key) : null)
  const { preview } = useAccent()

  const stage = p ? p.stages[p.stage] : undefined
  const drivers = stage?.drivers ?? []
  const photos = useMemo(() => new Map((roster.data?.drivers ?? []).map((d) => [d.id, d])), [roster.data])
  const picked = drivers.find((d) => d.driver_id === pick?.driverId)
  usePageAccent(picked?.color_hex ?? null)

  const choose = (driverId: number) => {
    if (pick?.locked || !key) return
    const next = { driverId, locked: false }
    setSaved(next)
    writePick(key, next)
  }
  const lock = () => {
    if (!pick || !key) return
    setCountdown(false)
    const next = { ...pick, locked: true }
    setSaved(next)
    writePick(key, next)
  }
  const reset = () => {
    if (!key) return
    setSaved(null)
    writePick(key, null)
  }

  if (prediction.loading) return <div className="page"><Loading what="the next race" /></div>
  if (prediction.error) return <div className="page"><ErrorBox error={prediction.error} /></div>
  if (!p || !stage) return <div className="page"><p className="lead">No upcoming race to predict yet.</p></div>

  const sorted = [...drivers].sort((a, b) => b.p_win - a.p_win)
  const rank = picked ? sorted.findIndex((d) => d.driver_id === picked.driver_id) + 1 : 0
  const fav = sorted[0]

  return (
    <div className="page">
      <div className="game-head">
        <div>
          <span className="eyebrow">Game · Round {p.round} · {p.season}</span>
          <h1 className="display">
            <Chars text="Who wins" /> <em><Chars text={p.track} at={0.18} /></em><Chars text="?" at={0.45} />
          </h1>
          <p className="lead">
            Pick your winner and lock it in. Then see the odds Apex21 gives your driver
            ({p.stage === 'post_qualifying' ? 'after qualifying, from the starting grid' : 'before qualifying'}).
          </p>
        </div>
        <span className={`badge ${p.confidence.level}`}>Model confidence: {p.confidence.level}</span>
      </div>

      <div className="step"><span className="diamond"><span>1</span></span> Make your pick</div>
      <div className="pick-grid">
        {[...drivers].sort((a, b) => (a.grid ?? 99) - (b.grid ?? 99)).map((d, i) => {
          const info = photos.get(d.driver_id)
          const [first, last] = splitName(d.driver_name ?? '?')
          return (
            <motion.button
              key={d.driver_id}
              data-scramble
              className={`pick-tile ${d.driver_id === pick?.driverId ? 'picked' : ''}`}
              style={{ ['--c' as string]: normaliseHex(d.color_hex, '#888888') }}
              onClick={() => choose(d.driver_id)}
              data-cursor={pick?.locked ? undefined : 'Pick'}
              onMouseEnter={() => !pick?.locked && preview(d.color_hex)}
              onMouseLeave={() => preview(null)}
              disabled={pick?.locked && d.driver_id !== pick.driverId}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.025 }}
              whileTap={{ scale: 0.97 }}
            >
              {info?.photo_url ? <img src={info.photo_url} alt="" loading="lazy" /> : <span style={{ width: 44 }} />}
              <span className="who">
                <small>{first}</small>
                <strong>{last}</strong>
                <span>{d.team_name}</span>
              </span>
              {d.grid != null && <span className="grid-slot">P{d.grid}</span>}
            </motion.button>
          )
        })}
      </div>

      <div className="lock-row">
        {countdown ? (
          <LightsOut onDone={lock} />
        ) : !pick?.locked ? (
          <button className="btn-sharp" disabled={!pick} onClick={() => setCountdown(true)} data-sound-click="lock" data-cursor="Lock">
            {picked ? `Lock in ${picked.driver_name}` : 'Pick a driver'}
          </button>
        ) : (
          <button className="btn-sharp ghost" onClick={reset}>Change my pick</button>
        )}
        <span className="muted small">Your pick stays on this device only.</span>
      </div>

      <AnimatePresence>
        {pick?.locked && picked && (
          <motion.div
            className="reveal chamfer"
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.5 }}
          >
            <div className="step" style={{ marginTop: 0 }}><span className="diamond"><span>2</span></span> Apex21 says</div>
            <p className="verdict">
              {rank === 1 ? (
                <>You and Apex21 agree: <em>{picked.driver_name}</em> is the favourite at {(picked.p_win * 100).toFixed(0)}%.</>
              ) : (
                <>
                  Apex21 rates <em>{picked.driver_name}</em> the {ordinal(rank)} favourite at{' '}
                  {(picked.p_win * 100).toFixed(picked.p_win < 0.1 ? 1 : 0)}%. Its pick: {fav.driver_name} ({(fav.p_win * 100).toFixed(0)}%).
                </>
              )}
            </p>
            <OddsBars
              pickId={picked.driver_id}
              entries={drivers.map((d) => ({ id: d.driver_id, name: d.driver_name ?? '?', colour: d.color_hex, value: d.p_win }))}
            />
            <p className="muted small" style={{ marginTop: 16 }}>{p.confidence.basis}</p>
            <div className="lock-row">
              <Link to={`/race/${p.race_id}`} className="btn-sharp small">Full race forecast</Link>
              <Link to="/play/constructors" className="btn-sharp ghost small">Next game: the constructors ▸</Link>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
