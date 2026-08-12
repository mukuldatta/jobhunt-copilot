import { useEffect, useRef, useState } from 'react'

/** True when the viewer has asked for reduced motion. Tracks changes live. */
export function useReducedMotion() {
  const [reduced, setReduced] = useState(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  )
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = (e) => setReduced(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduced
}

/**
 * One IntersectionObserver for scroll reveal, unobserving each element once it
 * has shown — so a row never re-animates when you scroll back up.
 */
export function useReveal(reduced) {
  const observer = useRef(null)

  useEffect(() => {
    if (reduced) return undefined
    observer.current = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return
          entry.target.dataset.shown = 'true'
          observer.current.unobserve(entry.target)
        })
      },
      { threshold: 0.12 }
    )
    return () => observer.current?.disconnect()
  }, [reduced])

  // Ref callback: attach to each row that should rise into view.
  return (node) => {
    if (!node || reduced) return
    if (node.dataset.shown === 'true') return
    observer.current?.observe(node)
  }
}

/**
 * Entrance stagger, capped so the last row of a long list is not held back.
 * Returns a style object; empty under reduced motion.
 */
export function stagger(index, { step = 35, cap = 6, reduced = false } = {}) {
  if (reduced) return undefined
  return { animationDelay: `${Math.min(index, cap) * step}ms` }
}
