/** One place that registers the GSAP plugins, so every component imports gsap from here. */
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

gsap.registerPlugin(ScrollTrigger, useGSAP)
gsap.defaults({ ease: 'power3.out' })

export { gsap, ScrollTrigger, useGSAP }

/** True when the visitor asked the OS for less motion: effects then show their end state. */
export function reducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** True on a mouse/trackpad (not touch), where hover effects and the custom cursor make sense. */
export function finePointer(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(hover: hover) and (pointer: fine)').matches
}
