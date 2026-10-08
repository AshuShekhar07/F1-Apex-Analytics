import { useEffect, useRef } from 'react'

import { gsap, reducedMotion } from '../motion/gsap'
import { scrollVelocity } from '../motion/SmoothScroll'

const COUNT = 64
const ANGLE = (-14 * Math.PI) / 180     // streaks run slightly uphill, left to right
const COS = Math.cos(ANGLE)
const SIN = Math.sin(ANGLE)

interface Streak {
  x: number
  y: number
  len: number
  speed: number
  depth: number     // 0.2 (far, faint, slow) .. 1 (near)
}

/**
 * Light streaks rushing past, drawn on a canvas behind everything. They speed up while the
 * page scrolls, shift with the pointer (near ones more: parallax) and take the accent colour.
 * Under reduced motion one still frame is drawn.
 */
export function SpeedField() {
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    let w = 0
    let h = 0
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5)
    const resize = () => {
      w = window.innerWidth
      h = window.innerHeight
      el.width = Math.round(w * dpr)
      el.height = Math.round(h * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const spawn = (s: Partial<Streak> = {}): Streak => {
      const depth = 0.2 + Math.random() * 0.8
      return { x: Math.random() * w * 1.4 - w * 0.2, y: Math.random() * h * 1.3, len: 60 + depth * 260, speed: 2 + depth * 9, depth, ...s }
    }
    const streaks = Array.from({ length: COUNT }, () => spawn())
    let rgb = '225, 6, 0'
    let frameNo = 0
    let mx = 0
    let my = 0
    let tx = 0
    let ty = 0
    let boost = 0
    const pointer = (e: PointerEvent) => {
      tx = e.clientX / w - 0.5
      ty = e.clientY / h - 0.5
    }

    const draw = (dt: number) => {
      if (frameNo++ % 20 === 0) rgb = getComputedStyle(document.documentElement).getPropertyValue('--accent-rgb').trim() || rgb
      mx += (tx - mx) * 0.05
      my += (ty - my) * 0.05
      boost += (Math.min(Math.abs(scrollVelocity()), 60) / 6 - boost) * 0.1
      ctx.clearRect(0, 0, w, h)
      for (const s of streaks) {
        s.x += s.speed * (1 + boost) * dt
        s.y += s.speed * (1 + boost) * dt * (SIN / COS)
        if (s.x - s.len > w * 1.2 || s.y < -s.len) Object.assign(s, spawn({ x: -Math.random() * w * 0.3 - s.len, y: Math.random() * h * 1.4 }))
        const px = s.x - mx * 60 * s.depth
        const py = s.y - my * 40 * s.depth
        const tail = s.len * (1 + boost * 0.6)
        const x0 = px - COS * tail
        const y0 = py - SIN * tail
        const g = ctx.createLinearGradient(x0, y0, px, py)
        const alpha = 0.05 + s.depth * 0.32
        g.addColorStop(0, `rgba(${rgb}, 0)`)
        g.addColorStop(0.75, `rgba(${rgb}, ${alpha})`)
        g.addColorStop(1, `rgba(255, 255, 255, ${alpha * 0.9})`)
        ctx.strokeStyle = g
        ctx.lineWidth = 0.6 + s.depth * 1.4
        ctx.beginPath()
        ctx.moveTo(x0, y0)
        ctx.lineTo(px, py)
        ctx.stroke()
      }
    }

    window.addEventListener('resize', resize)
    if (reducedMotion()) {
      draw(0)
      return () => window.removeEventListener('resize', resize)
    }
    window.addEventListener('pointermove', pointer, { passive: true })
    const tick = (_time: number, deltaMs: number) => draw(Math.min(deltaMs, 50) / 16.7)
    gsap.ticker.add(tick)
    return () => {
      gsap.ticker.remove(tick)
      window.removeEventListener('resize', resize)
      window.removeEventListener('pointermove', pointer)
    }
  }, [])

  return <canvas className="speed-field" ref={canvas} aria-hidden="true" />
}
