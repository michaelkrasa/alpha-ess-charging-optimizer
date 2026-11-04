import { describe, expect, it } from 'vitest'
import type { DashboardState, Decision } from './types'
import { connectionFeedback, acceptanceFeedback, andon, managerFeedback, notices } from './status'
import { configuredAction, deviceSegments, sameSchedule, scheduleFresh } from './schedule'
import { domain, inspect, priceAxis, priceOf, scheduleTrack, socSeries, visiblePrices } from './series'
import { smoothPath, stepPath } from './geometry'
import { atLocalMinute, dayStart, local, minuteOfDay } from './time'

describe('connection feedback distinguishes contact, measurement and failures', () => {
  const now = '2026-10-21T12:00:00Z'
  it('reports each state truthfully', () => {
    const s: any = { now, telemetry: { timestamp: '2026-10-21T11:57:00Z' }, device_connection: { status: 'connected', last_success_at: '2026-10-21T11:59:40Z' } }
    let r = connectionFeedback(s)
    expect(r.connected).toBe(true)
    expect(r.status).toBe('Connected · cloud API')
    expect(r.detail).toContain(local('2026-10-21T11:57:00Z'))
    s.device_connection.status = 'error'
    r = connectionFeedback(s)
    expect(r.connected).toBe(false); expect(r.status).toBe('Connection needs attention')
    s.device_connection = { status: 'connected', last_success_at: '2026-10-21T11:55:00Z' }
    expect(connectionFeedback(s).status).toBe('Connection check overdue')
    s.device_connection.last_success_at = '2026-10-21T11:59:40Z'; s.telemetry.timestamp = '2026-10-21T11:00:00Z'
    r = connectionFeedback(s)
    expect(r.connected).toBe(false); expect(r.status).toBe('Connected · data delayed')
    expect(connectionFeedback({ now }).status).toBe('Checking connection')
  })
})

describe('inverter schedule decoding', () => {
  const schedule = {
    charge: { gridCharge: 1, timeChaf1: '01:00', timeChae1: '03:00', timeChaf2: '00:00', timeChae2: '00:00' },
    discharge: { ctrDis: 1, timeDisf1: '17:00', timeDise1: '23:45', timeDisf2: '00:00', timeDise2: '00:00' },
    checked_at: '2026-10-21T11:55:00Z',
  }
  it('maps minutes to the configured action', () => {
    expect(configuredAction(schedule, 90)).toBe('charge')
    expect(configuredAction(schedule, 18 * 60)).toBe('self_consume')
    expect(configuredAction(schedule, 12 * 60)).toBe('hold')
    expect(configuredAction({ ...schedule, discharge: { ctrDis: 0 } }, 12 * 60)).toBe('self_consume')
    expect(configuredAction(null, 0)).toBe('unknown')
    expect(deviceSegments(schedule).map(s => [s.a, s.b, s.action])).toEqual([
      [0, 60, 'hold'], [60, 180, 'charge'], [180, 1020, 'hold'], [1020, 1425, 'self_consume'], [1425, 1440, 'hold'],
    ])
  })
  it('treats settings as confirmed only when fresh, complete and identical', () => {
    expect(scheduleFresh(schedule, '2026-10-21T12:00:00Z')).toBe(true)
    expect(scheduleFresh(schedule, '2026-10-21T12:30:00Z')).toBe(false)
    expect(scheduleFresh({ ...schedule, readback_pending: true }, '2026-10-21T12:00:00Z')).toBe(false)
    expect(sameSchedule(schedule, structuredClone(schedule))).toBe(true)
    expect(sameSchedule(schedule, { ...schedule, charge: { ...schedule.charge, timeChae1: '04:00' } })).toBe(false)
    expect(sameSchedule({ charge: {}, discharge: {} }, { charge: {}, discharge: {} })).toBe(false)
  })
})

describe('Prague wall-clock mapping', () => {
  it('handles DST days', () => {
    // 2026-10-25: clocks go back, day has 25 hours.
    expect(minuteOfDay(dayStart('2026-10-25'))).toBe(0)
    expect(atLocalMinute('2026-10-25', 1440) - dayStart('2026-10-25')).toBe(25 * 3600000)
    expect(minuteOfDay(atLocalMinute('2026-10-25', 17 * 60))).toBe(17 * 60)
    expect(minuteOfDay(atLocalMinute('2026-03-29', 12 * 60))).toBe(12 * 60)
  })
})

function priceState(): { s: DashboardState; plan: Decision[] } {
  const price_intervals = Array.from({ length: 96 }, (_, i) => ({
    timestamp: new Date(Date.UTC(2026, 9, 20, 22) + i * 900000).toISOString(),
    spot_eur_mwh: i === 32 ? 300 : i === 4 ? 200 : 100,
    import_eur_kwh: i === 32 ? 0.4 : 0.25,
  }))
  const plan = price_intervals.slice(49).map(r => ({ ...r, action: 'hold', soc_percent: 50, solar_kw: 2 }))
  const s = { now: '2026-10-21T10:00:00+00:00', price_intervals, control: { enabled: true }, readiness: [] } as unknown as DashboardState
  return { s, plan }
}

describe('price and battery series', () => {
  it('keeps the full day, holds prices per interval and maps linearly', () => {
    const { s, plan } = priceState()
    const rows = visiblePrices(s, plan, 'today')
    expect(rows).toHaveLength(96)
    expect(rows[32].spot_eur_mwh).toBe(300)
    expect(rows[0].soc_percent).toBeNull()
    expect(visiblePrices(s, plan, 'tomorrow')).toHaveLength(0)
    const { start, end } = domain(s, rows, 'today')
    expect(end - start).toBe(96 * 900000)
    const axis = priceAxis(rows.map(r => priceOf(r, 'spot')))!
    const y = (v: number) => (axis.max - v) / (axis.max - axis.min)
    expect(Math.abs(2 * y(200) - y(100) - y(300))).toBeLessThan(1e-9)
    expect(y(100) - y(300)).toBeGreaterThan(0.8)
    const negative = priceAxis([-30, 20])!; expect(negative.min).toBeLessThan(-30); expect(negative.max).toBeGreaterThan(20)
    const flat = priceAxis([100, 100])!; expect(flat.max).toBeGreaterThan(flat.min)
    expect(priceOf(rows[32], 'import')).toBeCloseTo(400)
  })

  it('draws price steps that never interpolate across an interval', () => {
    const d = stepPath([{ x0: 0, x1: 10, y: 5 }, { x0: 10, x1: 20, y: 8 }, { x0: 20, x1: 30, y: null }, { x0: 30, x1: 40, y: 2 }])
    expect(d).toBe('M0.00 5.00L10.00 5.00L10.00 8.00L20.00 8.00M30.00 2.00L40.00 2.00')
  })

  it('connects the latest measured SOC to Now without inventing activity', () => {
    const { s, plan } = priceState()
    s.telemetry = { timestamp: '2026-10-21T09:57:00+00:00', soc_percent: 52, load_kw: 1, solar_kw: 0, battery_kw: 0 }
    const rows = visiblePrices(s, plan, 'today')
    const { start, end } = domain(s, rows, 'today')
    const soc = socSeries(s, rows, [], start, end)
    const now = Date.parse(s.now)
    expect(soc.bridge.at(-1)).toEqual({ t: now, v: 52 })
    expect(soc.bridge[0].v).toBe(52)
    expect(soc.bridgeForecast[0]).toEqual({ t: now, v: 52 })
    expect(soc.forecast[0].t).toBeGreaterThan(now)
    expect(s.telemetry.soc_percent).toBe(52)
  })

  it('breaks smoothed curves across measurement gaps', () => {
    const d = smoothPath([[0, 0, 0], [10, 10, 600000], [20, 5, 600000 + 3600000]], 1200000)
    expect(d.match(/M/g)).toHaveLength(2)
  })

  it('inspects one interval with expected, measured and delta', () => {
    const { s, plan } = priceState()
    const t = plan[3].timestamp
    s.soc_tracking = { actual: [{ timestamp: t, actual_soc: 48 }], rows: [] }
    const rows = visiblePrices(s, plan, 'today')
    const d = inspect(s, plan, [], rows.find(r => r.timestamp === t)!)
    expect(d.expected).toBe(50)
    expect(d.actual).toBe(48)
    expect(d.delta).toBe(-2)
    expect(d.planAction).toBe('hold')
    expect(d.importPrice).toBeCloseTo(250)
  })
})

describe('andon state', () => {
  const base = (): DashboardState => ({
    now: '2026-10-21T12:00:00Z',
    control: { enabled: true },
    readiness: [],
    telemetry: { timestamp: '2026-10-21T11:58:00Z', soc_percent: 50, load_kw: 1, solar_kw: 0, battery_kw: 0 },
    device_connection: { status: 'connected', last_success_at: '2026-10-21T11:59:30Z' },
  })
  const board = (s: DashboardState) => andon(s, connectionFeedback(s), managerFeedback(s), acceptanceFeedback(s), notices(s))
  it('is OK only when control is confirmed and the link is live', () => {
    const schedule = { charge: { gridCharge: 0 }, discharge: { ctrDis: 0 }, checked_at: '2026-10-21T11:58:00Z' }
    const s = { ...base(), device_schedule: schedule, applied_schedule: schedule, actuation: { status: 'accepted', action: 'daily_schedule' } }
    expect(board(s).tone).toBe('good')
    expect(board({ ...s, device_connection: { status: 'error' } }).tone).toBe('attention')
    expect(board({ ...s, readiness: ['Prices unavailable'] }).tone).toBe('attention')
    expect(board({ ...s, control: { enabled: false } }).tone).toBe('off')
    expect(board({ ...s, health: { status: 'degraded', message: 'Cycle failed' } }).headline).toBe('Needs attention')
  })
})

describe('single outlook schedule', () => {
  it('uses the plan where it exists and inverter windows elsewhere', () => {
    const { s, plan } = priceState()
    s.device_schedule = {
      charge: { gridCharge: 1, timeChaf1: '02:00', timeChae1: '04:00', timeChaf2: '00:00', timeChae2: '00:00' },
      discharge: { ctrDis: 1, timeDisf1: '00:00', timeDise1: '00:00', timeDisf2: '00:00', timeDise2: '00:00' },
      checked_at: '2026-10-21T09:58:00Z',
    }
    const rows = visiblePrices(s, plan, 'today')
    const { start, end } = domain(s, rows, 'today')
    const track = scheduleTrack(s, plan, start, end)
    expect(track.map(x => [x.source, x.action, minuteOfDay(x.t0)])).toEqual([
      ['inverter', 'hold', 0], ['inverter', 'charge', 120], ['inverter', 'hold', 240], ['plan', 'hold', 12 * 60 + 15],
    ])
    const d = inspect(s, plan, [], rows.find(r => minuteOfDay(r.timestamp) === 150)!)
    expect(d.scheduleAction).toBe('charge'); expect(d.scheduleSource).toBe('inverter')
  })
})
