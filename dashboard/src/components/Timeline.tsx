import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import type { DashboardState, Decision, Telemetry } from '../lib/types'
import { actionShort } from '../lib/schedule'
import {
  domain, inspect, observedRows, priceAxis, priceOf, scheduleTrack, selectedKeys, socSeries, visiblePrices,
  type DayView, type PriceBasis,
} from '../lib/series'
import { linePath, niceTicks, smoothPath, stepArea, stepPath } from '../lib/geometry'
import { QUARTER, clock, dayKey, dayLabel, dayStart, finite, fmt, minuteOfDay, ms } from '../lib/time'
import { Readout } from './Readout'

const PAD_L = 56, PAD_R = 48
const PLOT_TOP = 30, PLOT_H = 300, BAND_GAP = 10, BAND_H = 26, AXIS_H = 28
const BAND_TOP = PLOT_TOP + PLOT_H + BAND_GAP
const HEIGHT = BAND_TOP + BAND_H + AXIS_H
/** Solar is a ground layer: it uses the lower part of the plot on its own kW scale. */
const SOLAR_SHARE = 0.5

export type Layer = 'price' | 'battery' | 'solar'
const LAYERS: { key: Layer; label: string }[] = [
  { key: 'price', label: 'Price' },
  { key: 'battery', label: 'Battery' },
  { key: 'solar', label: 'Solar' },
]
const STORAGE = 'energy-layers'

function useLayers() {
  const [layers, setLayers] = useState<Record<Layer, boolean>>(() => {
    const all = { price: true, battery: true, solar: true }
    try { return { ...all, ...JSON.parse(localStorage.getItem(STORAGE) || '{}') } } catch { return all }
  })
  const toggle = (key: Layer) => setLayers(prev => {
    const next = { ...prev, [key]: !prev[key] }
    try { localStorage.setItem(STORAGE, JSON.stringify(next)) } catch { /* private mode */ }
    return next
  })
  return [layers, toggle] as const
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(900)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(320, Math.round(entry.contentRect.width))))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}

export function Timeline({ s, plan, view, basis }: { s: DashboardState; plan: Decision[]; view: DayView; basis: PriceBasis }) {
  const [ref, width] = useWidth()
  const reduce = useReducedMotion()
  const [layers, toggle] = useLayers()
  const [hover, setHover] = useState<{ index: number; px: number; py: number } | null>(null)
  const [pinned, setPinned] = useState(false)
  const observed: Telemetry[] = useMemo(() => observedRows(s), [s])

  const m = useMemo(() => {
    const rows = visiblePrices(s, plan, view)
    const { start, end } = domain(s, rows, view)
    const plotW = width - PAD_L - PAD_R
    const x = (t: number) => PAD_L + ((t - start) / (end - start)) * plotW
    const now = ms(s.now)
    const nowX = now > start && now < end ? x(now) : null
    const bottom = PLOT_TOP + PLOT_H

    const axis = priceAxis(rows.map(r => priceOf(r, basis)))
    const py = (v: number) => (axis ? PLOT_TOP + ((axis.max - v) / (axis.max - axis.min)) * PLOT_H : 0)
    const steps = rows.map(r => {
      const v = priceOf(r, basis), t = ms(r.timestamp)
      return { x0: x(t), x1: x(t + QUARTER), y: finite(v) && axis ? py(v) : null }
    })
    const priced = rows.filter(r => finite(priceOf(r, basis)))
    const low = priced.length ? priced.reduce((a, b) => (priceOf(a, basis)! <= priceOf(b, basis)! ? a : b)) : null
    const high = priced.length ? priced.reduce((a, b) => (priceOf(a, basis)! >= priceOf(b, basis)! ? a : b)) : null
    const priceTicks = axis ? niceTicks(axis.min, axis.max, 5).filter(v => v >= axis.min && v <= axis.max) : []

    const by = (v: number) => PLOT_TOP + ((100 - v) / 100) * PLOT_H
    const soc = socSeries(s, rows, observed, start, end)
    const toXY = (p: { t: number; v: number }[]) => p.map(q => [x(q.t), by(q.v), q.t] as [number, number, number])

    const solarActual = observed.filter(r => { const t = ms(r.timestamp); return t >= start && t < end && finite(r.solar_kw) })
    const forecastRows = rows.filter(r => finite(r.solar_kw))
    const solarMax = Math.max(0.5, Math.ceil(Math.max(0, ...solarActual.map(r => r.solar_kw), ...forecastRows.map(r => r.solar_kw!)) * 2) / 2)
    const sy = (v: number) => bottom - (v / solarMax) * PLOT_H * SOLAR_SHARE
    const solarSteps = rows.map(r => ({ x0: x(ms(r.timestamp)), x1: x(ms(r.timestamp) + QUARTER), y: finite(r.solar_kw) ? sy(r.solar_kw) : null }))

    const track = scheduleTrack(s, plan, start, end)

    const twoDays = end - start > 26 * 3600000
    const ticks: { x: number; label: string }[] = []
    const every = (twoDays ? 360 : 180) * (plotW < 520 ? 2 : 1)
    for (let t = start; t <= end + 1; t += QUARTER) {
      const minute = minuteOfDay(t)
      if (minute % every === 0) ticks.push({ x: x(t), label: t >= end - 1 && minute === 0 ? '24:00' : clock(t) })
    }
    const keys = selectedKeys(s.now, view)
    const boundaries = keys.slice(1).map(k => ({ x: x(dayStart(k)), key: k }))

    return { rows, start, end, x, now, nowX, bottom, axis, py, steps, low, high, priceTicks, by, soc, toXY, solarActual, solarMax, sy, solarSteps, track, ticks, keys, boundaries, plotW }
  }, [s, plan, view, basis, width, observed])

  const { rows, x, nowX, axis } = m

  const indexAt = (clientX: number, rect: DOMRect) => {
    if (!rows.length) return null
    const t = m.start + ((clientX - rect.left - PAD_L) / m.plotW) * (m.end - m.start)
    if (t < m.start || t >= m.end) return null
    const slot = m.start + Math.floor((t - m.start) / QUARTER) * QUARTER
    const i = rows.findIndex(r => ms(r.timestamp) === slot)
    return i >= 0 ? i : null
  }
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (pinned) return
    const rect = e.currentTarget.getBoundingClientRect()
    const index = indexAt(e.clientX, rect)
    setHover(index === null ? null : { index, px: e.clientX - rect.left, py: e.clientY - rect.top })
  }
  const onClick = (e: PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const index = indexAt(e.clientX, rect)
    if (index === null) { setPinned(false); setHover(null); return }
    if (pinned && hover?.index === index) { setPinned(false); return }
    setHover({ index, px: e.clientX - rect.left, py: e.clientY - rect.top })
    setPinned(true)
  }
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') { setPinned(false); setHover(null); return }
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key) || !rows.length) return
    e.preventDefault()
    let i = hover?.index ?? rows.findIndex(r => ms(r.timestamp) <= m.now && m.now < ms(r.timestamp) + QUARTER)
    if (i < 0) i = 0
    i = e.key === 'Home' ? 0 : e.key === 'End' ? rows.length - 1 : Math.max(0, Math.min(rows.length - 1, i + (e.key === 'ArrowLeft' ? -1 : 1)))
    setHover({ index: i, px: x(ms(rows[i].timestamp) + QUARTER / 2), py: PLOT_TOP + 40 })
    setPinned(true)
  }
  useEffect(() => { setHover(null); setPinned(false) }, [view])

  const active = hover && rows[hover.index] ? rows[hover.index] : null
  const detail = active ? inspect(s, plan, observed, active) : null
  const activeT = active ? ms(active.timestamp) : null
  const colX0 = activeT !== null ? x(activeT) : 0, colX1 = activeT !== null ? x(activeT + QUARTER) : 0
  const draw = reduce ? {} : { initial: { pathLength: 0 }, animate: { pathLength: 1 }, transition: { duration: 1.1, ease: [0.16, 1, 0.3, 1] as const } }

  return (
    <>
      <div className="layer-bar" role="group" aria-label="Chart layers">
        {LAYERS.map(l => (
          <button key={l.key} type="button" className={'layer ' + l.key} aria-pressed={layers[l.key]} onClick={() => toggle(l.key)}>
            <i aria-hidden="true" />{l.label}
          </button>
        ))}
        <span className="layer-note">{layers.solar ? `Solar on its own scale · 0–${fmt(m.solarMax, m.solarMax % 1 ? 1 : 0)} kW` : ''}</span>
      </div>
      <div
        className={'timeline' + (pinned ? ' pinned' : '')}
        ref={ref}
        tabIndex={0}
        role="img"
        aria-label="Quarter-hour prices, battery charge and solar with the battery schedule. Use left and right arrow keys to inspect intervals, Escape to clear."
        onPointerMove={onMove}
        onPointerLeave={() => !pinned && setHover(null)}
        onClick={onClick as any}
        onKeyDown={onKey}
      >
        <svg width={width} height={HEIGHT} viewBox={`0 0 ${width} ${HEIGHT}`} aria-hidden="true">
          <defs>
            <linearGradient id="solar-fill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--solar)" stopOpacity="0.32" />
              <stop offset="100%" stopColor="var(--solar)" stopOpacity="0.06" />
            </linearGradient>
            <clipPath id="plot-clip"><rect x={PAD_L} y={PLOT_TOP - 2} width={m.plotW} height={PLOT_H + 4} /></clipPath>
          </defs>

          <rect x={PAD_L} y={PLOT_TOP} width={m.plotW} height={PLOT_H} className="plot-bg" />

          {nowX !== null && <rect x={PAD_L} y={PLOT_TOP} width={nowX - PAD_L} height={PLOT_H} className="elapsed" />}

          {/* Grid + axes */}
          {layers.price && m.priceTicks.map(v => (
            <g key={'pt' + v}>
              <line x1={PAD_L} x2={width - PAD_R} y1={m.py(v)} y2={m.py(v)} className="grid" />
              <text x={PAD_L - 8} y={m.py(v) + 4} className="tick price-tick" textAnchor="end">{Math.round(v)}</text>
            </g>
          ))}
          {!layers.price && [0, 25, 50, 75, 100].map(v => <line key={'g' + v} x1={PAD_L} x2={width - PAD_R} y1={m.by(v)} y2={m.by(v)} className="grid" />)}
          {layers.battery && [0, 25, 50, 75, 100].map(v => (
            <text key={'bt' + v} x={width - PAD_R + 8} y={m.by(v) + 4} className="tick soc-tick">{v}%</text>
          ))}
          {layers.price && <text x={PAD_L - 8} y={PLOT_TOP - 12} className="axis-title" textAnchor="end">€/MWh</text>}
          {layers.battery && <text x={width - PAD_R + 8} y={PLOT_TOP - 12} className="axis-title">SOC</text>}
          {layers.price && axis && axis.min < 0 && axis.max > 0 && <line x1={PAD_L} x2={width - PAD_R} y1={m.py(0)} y2={m.py(0)} className="zero" />}

          <g clipPath="url(#plot-clip)">
            {/* Solar (ground layer) */}
            {layers.solar && (
              <g>
                <path d={stepArea(m.solarSteps, m.bottom)} fill="url(#solar-fill)" />
                <path d={stepPath(m.solarSteps)} className="solar-forecast" />
                <path d={smoothPath(m.solarActual.map(r => [x(ms(r.timestamp)), m.sy(r.solar_kw), ms(r.timestamp)]), 1200000)} className="solar-measured" />
              </g>
            )}

            {/* Price */}
            {layers.price && axis && <motion.path key={'p' + view + basis} d={stepPath(m.steps)} className="price-line" {...draw} />}

            {/* Battery */}
            {layers.battery && (
              <g>
                {finite(s.reserve_percent) && <line x1={PAD_L} x2={width - PAD_R} y1={m.by(s.reserve_percent)} y2={m.by(s.reserve_percent)} className="reserve" />}
                <path d={smoothPath(m.toXY(m.soc.expectedPast))} className="soc-expected" />
                <path d={smoothPath(m.toXY(m.soc.forecast))} className="soc-forecast" />
                <motion.path key={'m' + view} d={smoothPath(m.toXY(m.soc.measured))} className="soc-measured" {...draw} />
                {m.soc.bridge.length > 0 && <path d={linePath(m.soc.bridge.map(p => [x(p.t), m.by(p.v)]))} className="soc-bridge" />}
                {m.soc.bridgeForecast.length > 0 && <path d={linePath(m.soc.bridgeForecast.map(p => [x(p.t), m.by(p.v)]))} className="soc-bridge" />}
              </g>
            )}
          </g>

          {layers.battery && finite(s.reserve_percent) && (
            <text x={width - PAD_R - 6} y={m.by(s.reserve_percent) - 5} className="lane-note" textAnchor="end">Reserve {fmt(s.reserve_percent, 0)}%</text>
          )}
          {layers.price && axis && m.low && m.high && [{ r: m.low, kind: 'Low' }, { r: m.high, kind: 'High' }].map(({ r, kind }) => {
            const t = ms(r.timestamp), cx = x(t + QUARTER / 2), cy = m.py(priceOf(r, basis)!)
            const above = kind === 'High' ? cy - 9 > PLOT_TOP + 12 : cy + 17 > m.bottom - 2
            return (
              <g key={kind} className="extreme">
                <circle cx={cx} cy={cy} r="3" />
                <text x={Math.min(Math.max(cx, PAD_L + 40), width - PAD_R - 40)} y={above ? cy - 8 : cy + 16} textAnchor="middle">{kind} {fmt(priceOf(r, basis), 0)}</text>
              </g>
            )
          })}
          {!rows.length && <text x={PAD_L + m.plotW / 2} y={PLOT_TOP + PLOT_H / 2} className="empty" textAnchor="middle">Prices for this day are not published yet</text>}

          {/* Schedule band: plan where it exists, inverter settings elsewhere. */}
          <text x={PAD_L - 8} y={BAND_TOP + 17} className="band-label" textAnchor="end">Schedule</text>
          <rect x={PAD_L} y={BAND_TOP} width={m.plotW} height={BAND_H} className="band-bg" />
          {m.track.map(seg => {
            const x0 = x(seg.t0), w = Math.max(0, x(seg.t1) - x0)
            return (
              <g key={'s' + seg.t0} className={'seg ' + seg.action}>
                <rect x={x0} y={BAND_TOP} width={w} height={BAND_H} />
                {w > 80 && <text x={x0 + w / 2} y={BAND_TOP + 17} textAnchor="middle">{actionShort(seg.action)}</text>}
              </g>
            )
          })}
          {!m.track.length && <text x={PAD_L + 10} y={BAND_TOP + 17} className="band-empty">Waiting for plan</text>}
          {nowX !== null && <rect x={PAD_L} y={BAND_TOP} width={nowX - PAD_L} height={BAND_H} className="elapsed band" />}

          {m.boundaries.map(b => <line key={b.key} x1={b.x} x2={b.x} y1={PLOT_TOP} y2={BAND_TOP + BAND_H} className="day-boundary" />)}
          {view === 'all' && m.keys.map(k => (
            <text key={'dl' + k} x={Math.max(PAD_L, x(dayStart(k))) + 6} y={PLOT_TOP + 16} className="day-label">{dayLabel(k, s.now)}</text>
          ))}

          {m.ticks.map(t => (
            <text key={'x' + t.x} x={Math.min(Math.max(t.x, PAD_L + 14), width - PAD_R - 14)} y={HEIGHT - 8} className="tick" textAnchor="middle">{t.label}</text>
          ))}

          {active && (
            <g className="inspect">
              <rect x={colX0} y={PLOT_TOP} width={Math.max(2, colX1 - colX0)} height={BAND_TOP + BAND_H - PLOT_TOP} className="inspect-col" />
              {layers.price && finite(priceOf(active, basis)) && <circle cx={(colX0 + colX1) / 2} cy={m.py(priceOf(active, basis)!)} r="4.5" className="dot price" />}
              {layers.battery && detail && finite(detail.actual) && <circle cx={colX0} cy={m.by(detail.actual)} r="4.5" className="dot measured" />}
              {layers.battery && detail && !finite(detail.actual) && finite(detail.expected) && <circle cx={colX0} cy={m.by(detail.expected)} r="4" className="dot forecast" />}
              {layers.solar && detail && finite(detail.solarForecast) && <circle cx={(colX0 + colX1) / 2} cy={m.sy(detail.solarForecast)} r="4" className="dot solar" />}
            </g>
          )}

          {nowX !== null && (
            <g className="now">
              <line x1={nowX} x2={nowX} y1={PLOT_TOP - 8} y2={BAND_TOP + BAND_H + 2} />
              <rect x={Math.min(nowX - 36, width - PAD_R - 72)} y={2} width="72" height="20" rx="4" />
              <text x={Math.min(nowX, width - PAD_R - 36)} y={16} textAnchor="middle">NOW {clock(m.now)}</text>
            </g>
          )}
        </svg>

        <AnimatePresence>
          {detail && hover && (
            <Readout key="readout" detail={detail} basis={basis} dayText={dayLabel(dayKey(detail.start), s.now)} left={hover.px} top={hover.py} width={width} pinned={pinned} />
          )}
        </AnimatePresence>
      </div>
    </>
  )
}
