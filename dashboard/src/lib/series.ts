// Time-aligned series for the timeline. Prices hold for their whole quarter
// hour; plan SOC is the projected charge at the END of its interval; measured
// values are never interpolated into intervals that have not happened.
import type { Action, DashboardState, Decision, PriceInterval, Telemetry } from './types'
import { configuredAction, scheduleFresh } from './schedule'
import { QUARTER, dayKey, dayStart, finite, minuteOfDay, ms, targetDays } from './time'

export type DayView = 'today' | 'tomorrow' | 'all'
export type PriceBasis = 'import' | 'spot'

export interface Row extends PriceInterval {
  soc_percent: number | null
  solar_kw: number | null
}

export function selectedKeys(now: string, view: DayView): string[] {
  const d = targetDays(now)
  return view === 'all' ? [d.today, d.tomorrow] : [d[view]]
}

export function visiblePrices(s: DashboardState, plan: Decision[], view: DayView): Row[] {
  const keys = selectedKeys(s.now, view)
  const projections = new Map(plan.map(r => [r.timestamp, r]))
  const published: PriceInterval[] = s.price_intervals?.length ? s.price_intervals : plan
  return published
    .filter(r => keys.includes(dayKey(r.timestamp)))
    .map(r => ({ ...r, soc_percent: projections.get(r.timestamp)?.soc_percent ?? null, solar_kw: projections.get(r.timestamp)?.solar_kw ?? null }))
}

export function tomorrowAvailable(s: DashboardState): boolean {
  const { tomorrow } = targetDays(s.now)
  return (s.price_intervals || []).some(r => dayKey(r.timestamp) === tomorrow)
}

export const priceOf = (r: PriceInterval, basis: PriceBasis): number | null =>
  basis === 'spot' ? (finite(r.spot_eur_mwh) ? r.spot_eur_mwh : null) : finite(r.import_eur_kwh) ? r.import_eur_kwh * 1000 : null

export function priceAxis(values: (number | null)[]): { min: number; max: number } | null {
  const v = values.filter(finite)
  if (!v.length) return null
  const low = Math.min(...v), high = Math.max(...v)
  const span = Math.max(high - low, Math.abs(high) * 0.05, 1), pad = span * 0.08
  return { min: low - pad, max: high + pad }
}

export function domain(s: DashboardState, rows: Row[], view: DayView): { start: number; end: number } {
  if (rows.length) return { start: ms(rows[0].timestamp), end: ms(rows.at(-1)!.timestamp) + QUARTER }
  const keys = selectedKeys(s.now, view)
  const start = dayStart(keys[0])
  const next = new Date(keys.at(-1)! + 'T12:00:00Z'); next.setUTCDate(next.getUTCDate() + 1)
  return { start, end: dayStart(next.toISOString().slice(0, 10)) }
}

export function observedRows(s: DashboardState): Telemetry[] {
  const historical = !s.plan && s.replay
  if (historical) return s.replay!.observed
  return s.energy_day?.rows || (s.history || []).filter(r => r.telemetry).map(r => r.telemetry!)
}

export interface Point { t: number; v: number }

export function latestSocReading(s: DashboardState, observed: Telemetry[]): Point | null {
  const now = ms(s.now)
  const candidates: Point[] = [
    ...(s.soc_tracking?.actual || []).map(r => ({ t: ms(r.timestamp), v: r.actual_soc })),
    ...observed.map(r => ({ t: ms(r.timestamp), v: r.soc_percent })),
  ]
  if (s.telemetry) candidates.push({ t: ms(s.telemetry.timestamp), v: s.telemetry.soc_percent })
  return candidates.filter(r => finite(r.t) && r.t <= now && finite(r.v) && r.v >= 0 && r.v <= 100).sort((a, b) => a.t - b.t).at(-1) || null
}

export interface SocSeries {
  measured: Point[]
  expectedPast: Point[]
  forecast: Point[]
  /** Dotted estimate: latest reading held flat to Now, then to the first forecast point. */
  bridge: Point[]
  bridgeForecast: Point[]
}

export function socSeries(s: DashboardState, rows: Row[], observed: Telemetry[], start: number, end: number): SocSeries {
  const now = ms(s.now)
  const forecast = rows.filter(r => finite(r.soc_percent)).map(r => ({ t: ms(r.timestamp) + QUARTER, v: r.soc_percent! }))
  const expectedPast = (s.soc_tracking?.rows || [])
    .map(r => ({ t: ms(r.timestamp), v: r.expected_soc }))
    .filter((r): r is Point => r.t >= start && r.t <= end && r.t <= now && finite(r.v))
  const measured = (s.soc_tracking?.actual || [])
    .map(r => ({ t: ms(r.timestamp), v: r.actual_soc }))
    .filter(r => r.t >= start && r.t <= end && r.t <= now && finite(r.v))
  const anchor = latestSocReading(s, observed)
  if (anchor && anchor.t >= start && anchor.t <= end && (!measured.length || anchor.t > measured.at(-1)!.t)) measured.push(anchor)
  let bridge: Point[] = [], bridgeForecast: Point[] = []
  if (anchor && now >= start && now < end) {
    if (anchor.t < now) bridge = [{ t: Math.max(start, anchor.t), v: anchor.v }, { t: now, v: anchor.v }]
    const future = forecast.find(p => p.t > now)
    if (future) bridgeForecast = [{ t: now, v: anchor.v }, future]
  }
  return { measured, expectedPast, forecast: forecast.filter(p => p.t >= now || now < start), bridge, bridgeForecast }
}

export function nearest(observed: Telemetry[], t: number, key: 'solar_kw' | 'load_kw' | 'soc_percent', maxGap = 300000): number | null {
  let best: number | null = null, gap = maxGap + 1
  for (const r of observed) {
    const distance = Math.abs(ms(r.timestamp) - t)
    if (finite(r[key]) && distance < gap) { best = r[key]; gap = distance }
  }
  return best
}

export interface Inspection {
  start: number
  end: number
  spot: number | null
  importPrice: number | null
  tariff?: string
  tariffVerified?: boolean
  solarForecast: number | null
  solarMeasured: number | null
  demandMeasured: number | null
  expected: number | null
  actual: number | null
  delta: number | null
  plan: Decision | null
  planAction: Action | null
  inverterAction: Action | null
  inverterConfirmed: boolean
  /** The single schedule shown in the outlook for this quarter. */
  scheduleAction: Action | null
  scheduleSource: TrackSource | null
  elapsed: boolean
}

/** Every number shown when an operator hovers one quarter-hour interval. */
export function inspect(s: DashboardState, plan: Decision[], observed: Telemetry[], row: Row): Inspection {
  const t = ms(row.timestamp)
  const tracking = s.soc_tracking
  const actual = tracking?.actual?.find(r => ms(r.timestamp) === t)?.actual_soc ?? null
  const issued = tracking?.rows?.find(r => ms(r.timestamp) === t)?.expected_soc ?? null
  const projected = plan.find(r => ms(r.timestamp) + QUARTER === t)?.soc_percent ?? null
  const expected = finite(issued) ? issued : finite(projected) ? projected : null
  const decision = plan.find(r => r.timestamp === row.timestamp) ?? null
  const confirmed = scheduleFresh(s.device_schedule, s.now)
  const inverterAction = s.device_schedule?.charge && s.device_schedule?.discharge ? configuredAction(s.device_schedule, minuteOfDay(t)) : null
  const elapsed = t + QUARTER <= ms(s.now)
  const measured = elapsed ? measuredAction(s, observed, t) : null
  return {
    start: t,
    end: t + QUARTER,
    spot: finite(row.spot_eur_mwh) ? row.spot_eur_mwh : null,
    importPrice: finite(row.import_eur_kwh) ? row.import_eur_kwh * 1000 : null,
    tariff: row.tariff,
    tariffVerified: row.tariff_verified,
    solarForecast: finite(row.solar_kw) ? row.solar_kw : null,
    solarMeasured: nearest(observed, t, 'solar_kw'),
    demandMeasured: nearest(observed, t, 'load_kw'),
    expected,
    actual: finite(actual) ? actual : null,
    delta: finite(expected) && finite(actual) ? actual - expected : null,
    plan: decision,
    planAction: decision?.action ?? null,
    inverterAction,
    inverterConfirmed: confirmed,
    scheduleAction: elapsed ? measured : decision?.action ?? inverterAction,
    scheduleSource: elapsed ? (measured ? 'measured' : null) : decision ? 'plan' : inverterAction ? 'inverter' : null,
    elapsed,
  }
}

export interface DayStats { min: Row; max: Row; mean: number; current: Row | null; rank: number | null }

/** Today's import-price statistics; null if any interval lacks a billed price. */
export function priceStats(s: DashboardState, basis: PriceBasis): DayStats | null {
  const key = dayKey(s.now)
  const rows = (s.price_intervals || []).filter(r => dayKey(r.timestamp) === key) as Row[]
  if (!rows.length || rows.some(r => !finite(priceOf(r, basis)))) return null
  const values = rows.map(r => priceOf(r, basis)!)
  const now = ms(s.now)
  const current = rows.find(r => ms(r.timestamp) <= now && now < ms(r.timestamp) + QUARTER) ?? null
  const rank = current ? values.filter(v => v < priceOf(current, basis)!).length / values.length : null
  return {
    min: rows.reduce((a, b) => (priceOf(a, basis)! <= priceOf(b, basis)! ? a : b)),
    max: rows.reduce((a, b) => (priceOf(a, basis)! >= priceOf(b, basis)! ? a : b)),
    mean: values.reduce((a, b) => a + b, 0) / values.length,
    current,
    rank,
  }
}

export interface PlanWindow { start: number; end: number; action: Action; rows: Decision[] }

/** Collapse quarter-hour plan rows into contiguous action windows. */
export function planWindows(plan: Decision[], keys: string[]): PlanWindow[] {
  const out: PlanWindow[] = []
  for (const r of plan) {
    if (!keys.includes(dayKey(r.timestamp))) continue
    const t = ms(r.timestamp), last = out.at(-1)
    if (last && last.end === t && last.action === r.action) { last.end = t + QUARTER; last.rows.push(r) }
    else out.push({ start: t, end: t + QUARTER, action: r.action, rows: [r] })
  }
  return out
}

export type TrackSource = 'measured' | 'plan' | 'inverter'
export interface TrackSegment { t0: number; t1: number; action: Action; source: TrackSource }

/** Smallest SOC change per quarter that counts as battery activity (≈0.35 kW on 17 kWh). */
const ACTIVITY_PP = 0.5

/**
 * What the battery actually did in an elapsed quarter, from the measured SOC at
 * its two boundaries: charging (grid or solar, by measured solar power),
 * supplying the house, or preserved. Null when either boundary is unmeasured.
 */
export function measuredAction(s: DashboardState, observed: Telemetry[], t: number): Action | null {
  const soc = new Map((s.soc_tracking?.actual || []).map(r => [ms(r.timestamp), r.actual_soc]))
  const a = soc.get(t), b = soc.get(t + QUARTER)
  if (!finite(a) || !finite(b)) return null
  const change = b - a
  if (change <= -ACTIVITY_PP) return 'self_consume'
  if (change < ACTIVITY_PP) return 'hold'
  const capacity = s.telemetry?.capacity_kwh
  const solar = observed.filter(r => { const u = ms(r.timestamp); return u >= t && u <= t + QUARTER && finite(r.solar_kw) })
  const solarKw = solar.length ? solar.reduce((sum, r) => sum + r.solar_kw, 0) / solar.length : 0
  const chargeKw = finite(capacity) && capacity > 0 ? (change / 100) * capacity * 4 : null
  return chargeKw !== null ? (solarKw >= 0.7 * chargeKw ? 'solar' : 'charge') : solarKw >= 0.3 ? 'solar' : 'charge'
}

/**
 * One schedule for the outlook. Elapsed quarters show what the battery
 * measurably did; from the current quarter on, the plan where it exists and
 * the inverter's (daily-repeating) windows elsewhere. Plan and inverter quarters
 * with the same action form one segment.
 */
export function scheduleTrack(s: DashboardState, plan: Decision[], start: number, end: number, observed: Telemetry[] = []): TrackSegment[] {
  const byTime = new Map(plan.map(r => [ms(r.timestamp), r.action]))
  const device = s.device_schedule?.charge && s.device_schedule?.discharge ? s.device_schedule : null
  const now = ms(s.now)
  const segments: TrackSegment[] = []
  for (let t = start; t < end; t += QUARTER) {
    const elapsed = t + QUARTER <= now
    const planned = byTime.get(t)
    const action = elapsed ? measuredAction(s, observed, t) : planned ?? (device ? configuredAction(device, minuteOfDay(t)) : null)
    if (!action) continue
    const source: TrackSource = elapsed ? 'measured' : planned ? 'plan' : 'inverter'
    const last = segments.at(-1)
    if (last && last.t1 === t && last.action === action && (last.source === 'measured') === (source === 'measured')) last.t1 = t + QUARTER
    else segments.push({ t0: t, t1: t + QUARTER, action, source })
  }
  return segments
}
