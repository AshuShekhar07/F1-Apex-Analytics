import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useState } from 'react'

import { sessionFlag, setSessionFlag } from '../storage'

const LIGHT_MS = 330
const ON_AT = 1150           // first red light
const OUT_AT = ON_AT + 5 * LIGHT_MS + 650

/**
 * The opening: speed streaks, the Apex21 logo wiping in with a light sweep, then the five
 * start lights come on one by one and go out -- lights out, the site opens.
 * Once per browser session; click or any key skips; reduced motion gets a short fade.
 */
export function Intro({ onDone }: { onDone: () => void }) {
  const reduced = useReducedMotion()
  const [lights, setLights] = useState(0)
  const [leaving, setLeaving] = useState(false)

  useEffect(() => {
    const finish = () => setLeaving(true)
    if (reduced) {
      const t = window.setTimeout(finish, 900)
      return () => window.clearTimeout(t)
    }
    const timers = [1, 2, 3, 4, 5].map((n) => window.setTimeout(() => setLights(n), ON_AT + (n - 1) * LIGHT_MS))
    timers.push(window.setTimeout(() => setLights(0), OUT_AT - 250))
    timers.push(window.setTimeout(finish, OUT_AT))
    return () => timers.forEach((t) => window.clearTimeout(t))
  }, [reduced])

  useEffect(() => {
    const skip = () => setLeaving(true)
    window.addEventListener('keydown', skip)
    return () => window.removeEventListener('keydown', skip)
  }, [])

  return (
    <AnimatePresence onExitComplete={onDone}>
      {!leaving && (
        <motion.div
          className="intro"
          onClick={() => setLeaving(true)}
          initial={{ opacity: 1 }}
          exit={reduced ? { opacity: 0 } : { clipPath: 'polygon(0 0, 0 0, -20% 100%, -20% 100%)' }}
          style={{ clipPath: 'polygon(0 0, 120% 0, 100% 100%, -20% 100%)' }}
          transition={{ duration: 0.75, ease: [0.7, 0, 0.2, 1] }}
          role="presentation"
        >
          {!reduced &&
            [22, 38, 61, 77].map((top, i) => (
              <motion.div
                key={top}
                className="speed"
                style={{ top: `${top}%` }}
                initial={{ x: '-110%', opacity: 0 }}
                animate={{ x: '110%', opacity: [0, 1, 0] }}
                transition={{ duration: 0.9, delay: 0.05 + i * 0.12, ease: 'easeIn' }}
              />
            ))}
          <div className="center">
            <motion.div
              className="logo-wrap"
              initial={reduced ? { opacity: 0 } : { clipPath: 'inset(0 100% 0 0)', filter: 'blur(8px)', x: -40 }}
              animate={reduced ? { opacity: 1 } : { clipPath: 'inset(0 0% 0 0)', filter: 'blur(0px)', x: 0 }}
              transition={{ duration: 0.7, delay: 0.35, ease: [0.2, 0.8, 0.2, 1] }}
            >
              <img src="/apex21-logo.png" alt="Apex21" />
              {!reduced && (
                <motion.div
                  className="sweep"
                  initial={{ backgroundPosition: '110% 0' }}
                  animate={{ backgroundPosition: '-10% 0' }}
                  transition={{ duration: 0.9, delay: 0.95, ease: 'easeInOut' }}
                />
              )}
            </motion.div>
            {!reduced && (
              <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.9 }}>
                <div className="lights" aria-hidden="true">
                  {[1, 2, 3, 4, 5].map((n) => (
                    <span key={n} className={lights >= n ? 'on' : ''} />
                  ))}
                </div>
              </motion.div>
            )}
            <motion.div className="tagline" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 1.1 }}>
              F1 predictions · tested before lights out
            </motion.div>
          </div>
          <div className="skip">Click or press any key to skip</div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

/** True on the first visit of this browser session (then remembered). */
export function useIntroOnce(): [boolean, () => void] {
  const [show, setShow] = useState(() => !sessionFlag('intro-seen'))
  const done = () => {
    setSessionFlag('intro-seen')
    setShow(false)
  }
  return [show, done]
}
