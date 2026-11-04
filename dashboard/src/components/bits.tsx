// Small animated primitives in the spirit of reactbits.dev, built on `motion`
// so the bundle stays self-contained (no GSAP/WebGL) and honours reduced motion.
import { animate, motion, useMotionTemplate, useMotionValue, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import type { Tone } from '../lib/types'

/** Number that counts smoothly to each new reading instead of jumping. */
export function CountUp({ value, digits = 1, className }: { value: number | null | undefined; digits?: number; className?: string }) {
  const reduce = useReducedMotion()
  const [shown, setShown] = useState(value ?? 0)
  const last = useRef(value ?? 0)
  useEffect(() => {
    if (typeof value !== 'number' || !Number.isFinite(value)) return
    if (reduce) { setShown(value); last.current = value; return }
    const controls = animate(last.current, value, {
      duration: 0.9,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: v => setShown(v),
    })
    last.current = value
    return () => controls.stop()
  }, [value, reduce])
  if (typeof value !== 'number' || !Number.isFinite(value)) return <span className={className}>—</span>
  return <span className={className}>{shown.toFixed(digits)}</span>
}

/** Card with a soft light that follows the cursor (reactbits "Spotlight Card"). */
export function SpotlightCard({ children, className = '', tone, style, as = 'section', ...rest }: {
  children: ReactNode
  className?: string
  tone?: Tone | 'solar' | 'grid' | 'price' | 'battery' | 'house'
  style?: CSSProperties
  as?: 'section' | 'article' | 'div'
  'aria-label'?: string
  'aria-labelledby'?: string
}) {
  const x = useMotionValue(-400), y = useMotionValue(-400)
  const background = useMotionTemplate`radial-gradient(420px circle at ${x}px ${y}px, var(--spot), transparent 70%)`
  const Tag = motion[as]
  return (
    <Tag
      {...rest}
      className={'card ' + className}
      data-tone={tone}
      style={style}
      onPointerMove={e => {
        const r = e.currentTarget.getBoundingClientRect()
        x.set(e.clientX - r.left); y.set(e.clientY - r.top)
      }}
      onPointerLeave={() => { x.set(-400); y.set(-400) }}
    >
      <motion.div className="card-spot" style={{ background }} aria-hidden="true" />
      {children}
    </Tag>
  )
}

/** Pulsing status light: the andon lamp. */
export function Beacon({ tone, size = 10 }: { tone: Tone; size?: number }) {
  return (
    <span className="beacon" data-tone={tone} style={{ width: size, height: size }} aria-hidden="true">
      <span className="beacon-ring" />
    </span>
  )
}

export interface SegmentOption<T extends string> { value: T; label: string; disabled?: boolean; title?: string }

/** Segmented control with a sliding active pill. */
export function Segmented<T extends string>({ options, value, onChange, label, id }: {
  options: SegmentOption<T>[]
  value: T
  onChange: (value: T) => void
  label: string
  id: string
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map(o => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          disabled={o.disabled}
          title={o.title}
          data-day={id === 'day' ? o.value : undefined}
          onClick={() => !o.disabled && onChange(o.value)}
        >
          {o.value === value && <motion.span layoutId={'seg-' + id} className="segmented-pill" transition={{ type: 'spring', stiffness: 520, damping: 38 }} />}
          <span className="segmented-label">{o.label}</span>
        </button>
      ))}
    </div>
  )
}

export function Switch({ on, disabled, onToggle, label }: { on: boolean; disabled?: boolean; onToggle: () => void; label: string }) {
  return (
    <button id="toggle" type="button" role="switch" aria-checked={on} aria-label={label} className={'switch' + (on ? ' on' : '')} disabled={disabled} onClick={onToggle}>
      <motion.span className="switch-knob" layout transition={{ type: 'spring', stiffness: 700, damping: 40 }} />
      <span className="switch-text">{on ? 'ON' : 'OFF'}</span>
    </button>
  )
}
