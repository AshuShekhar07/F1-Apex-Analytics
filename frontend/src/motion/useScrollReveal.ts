import { useEffect } from 'react'

import { reducedMotion } from './gsap'

/** What slides in as it scrolls into view. Nested targets move with their parent. */
const TARGETS = [
  '.page .panel', '.page .section-head', '.page .event-hero', '.page .verdict-card', '.page .era-banner',
  '.page .stat-grid > *', '.page .notice', '.page .hub-bar',
].join(', ')

/**
 * Scroll reveals for the whole site: panels, section titles and heroes fade and rise into place
 * as they come into view (a few at a time, one after another), and their table rows cascade in.
 * Content that arrives later (data loading, page changes) is picked up as it appears. Off under
 * reduced motion, and everything stays visible if the browser lacks IntersectionObserver.
 */
export function useScrollReveal(): void {
  useEffect(() => {
    if (reducedMotion() || typeof IntersectionObserver === 'undefined' || typeof MutationObserver === 'undefined') return
    const html = document.documentElement
    html.classList.add('reveal-on')
    const io = new IntersectionObserver((entries) => {
      let i = 0
      for (const e of entries) {
        if (!e.isIntersecting) continue
        const el = e.target as HTMLElement
        const delay = Math.min(i++, 6) * 70
        el.style.setProperty('--rv-delay', `${delay}ms`)
        el.classList.add('in')
        io.unobserve(el)
        // once in place, hover effects should answer at once
        window.setTimeout(() => el.style.removeProperty('--rv-delay'), delay + 900)
      }
    }, { rootMargin: '0px 0px -5% 0px', threshold: 0.04 })
    const register = () => {
      document.querySelectorAll<HTMLElement>(TARGETS).forEach((el) => {
        if (el.classList.contains('rv') || el.parentElement?.closest('.rv')) return
        el.classList.add('rv')
        io.observe(el)
      })
    }
    register()
    let queued = 0
    const mo = new MutationObserver(() => {
      if (queued) return
      queued = window.requestAnimationFrame(() => {
        queued = 0
        register()
      })
    })
    mo.observe(document.body, { childList: true, subtree: true })
    return () => {
      mo.disconnect()
      io.disconnect()
      window.cancelAnimationFrame(queued)
      html.classList.remove('reveal-on')
    }
  }, [])
}
