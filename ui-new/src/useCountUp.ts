import { useEffect, useRef, useState } from 'react'

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true
}

// Shared ease-out rAF driver used by both hooks below. Calls `onUpdate` on every frame
// with the interpolated value, then a final call pinned exactly to `to`. Returns a cancel
// function so effect cleanup can abort an in-flight animation (e.g. the value changes
// again, or the component unmounts, before it finishes).
function animateValue(from: number, to: number, duration: number, onUpdate: (value: number) => void): () => void {
  if (prefersReducedMotion() || from === to || !Number.isFinite(from) || !Number.isFinite(to)) {
    onUpdate(to)
    return () => {}
  }
  let rafId: number
  let start: number | null = null
  const step = (timestamp: number) => {
    if (start === null) start = timestamp
    const elapsed = timestamp - start
    const progress = Math.min(1, elapsed / duration)
    const eased = 1 - (1 - progress) ** 3
    onUpdate(from + (to - from) * eased)
    if (progress < 1) rafId = window.requestAnimationFrame(step)
  }
  rafId = window.requestAnimationFrame(step)
  return () => window.cancelAnimationFrame(rafId)
}

/**
 * Animates a single numeric value from its previous value (0 on first mount) up/down to
 * `target` over `duration`ms with an ease-out curve. Re-plays whenever `target` changes.
 * A no-op (jumps straight to `target`) when the user has prefers-reduced-motion set.
 */
export function useCountUp(target: number, duration = 600): number {
  const [display, setDisplay] = useState(0)
  const previousRef = useRef(0)

  useEffect(() => {
    const from = previousRef.current
    previousRef.current = target
    return animateValue(from, target, duration, (value) => setDisplay(Math.round(value)))
  }, [target, duration])

  return display
}

const NUMERIC_TOKEN_RE = /-?\d+(?:\.\d+)?/g

/**
 * Same count-up idea as useCountUp, but for an already-formatted display string that
 * embeds one or more numbers (e.g. "12 / 34", "5 项", durations, KPI text produced by
 * format.ts helpers) — every numeric substring animates independently while the
 * surrounding text/punctuation stays fixed. This is what drives the count-up effect on
 * DashboardMetric, whose `value` prop is already a composed string rather than a bare
 * number.
 */
export function useCountUpText(text: string, duration = 600): string {
  const [display, setDisplay] = useState(text)
  const previousNumbersRef = useRef<number[]>([])

  useEffect(() => {
    const matches = text.match(NUMERIC_TOKEN_RE)
    if (!matches || !matches.length) {
      previousNumbersRef.current = []
      setDisplay(text)
      return
    }
    const targets = matches.map(Number)
    const from = targets.map((_, index) => previousNumbersRef.current[index] ?? 0)
    const current = [...from]

    const render = () => {
      let tokenIndex = 0
      setDisplay(
        text.replace(NUMERIC_TOKEN_RE, () => {
          const rendered = String(Math.round(current[tokenIndex]))
          tokenIndex += 1
          return rendered
        }),
      )
    }

    const cancels = targets.map((target, index) =>
      animateValue(from[index], target, duration, (value) => {
        current[index] = value
        render()
      }),
    )
    previousNumbersRef.current = targets
    return () => cancels.forEach((cancel) => cancel())
  }, [text, duration])

  return display
}
