// Truthful status derivation. Settings readback is not physical execution, and
// measurements are timestamped; every message keeps that distinction.
import type { DashboardState, Decision, Tone } from './types'
import { actionLabel, configuredAction, sameSchedule, scheduleFresh, windowText } from './schedule'
import { ageMinutes, clock, fmt, local, minuteOfDay } from './time'

export interface Connection {
  connected: boolean
  status: string
  detail: string
}

export function connectionFeedback(s: Partial<DashboardState>, simulated = false): Connection {
  const t = s.telemetry, link = s.device_connection, now = s.now ?? new Date()
  const contactAge = ageMinutes(now, link?.last_success_at)
  const measureAge = ageMinutes(now, t?.timestamp)
  const recent = contactAge >= 0 && contactAge <= 3
  const usable = !!t && measureAge >= 0 && measureAge <= 20
  const connected = recent && link?.status === 'connected'
  const api = simulated ? 'Simulated · cloud API' : 'Connected · cloud API'
  const status = link?.status === 'error' ? 'Connection needs attention'
    : connected ? (usable ? api : 'Connected · data delayed')
    : link ? 'Connection check overdue' : 'Checking connection'
  const detail = (link?.last_success_at ? 'API contact ' + local(link.last_success_at) + '. ' : '')
    + (t ? 'Inverter measurement ' + local(t.timestamp) + '. ' : '')
    + (link?.status === 'error' ? (link.message || 'Latest read failed.') : 'Read-only checks about every minute; dashboard heartbeat every 5 s.')
  return { connected: Boolean(connected && usable), status, detail }
}

const blockers = (s: DashboardState) => s.readiness.filter(x => !x.startsWith('Daily schedule updates wait'))

export interface Manager {
  state: string
  detail: string
  attention: boolean
}

export function managerFeedback(s: DashboardState): Manager {
  const publication = blockers(s)
  let state: string, detail: string
  if (!s.control.enabled) {
    state = s.control.restore_pending ? 'Turning off' : 'Off'
    detail = s.control.restore_pending ? 'Native restoration is still pending.' : 'Monitoring continues; automatic updates are off.'
  } else if (publication.length) {
    state = 'Blocked'; detail = 'Monitoring continues. Automatic schedule changes are not enabled.'
  } else if (s.health?.status === 'degraded') {
    state = 'Needs attention'; detail = s.health.message || 'The latest controller cycle did not finish.'
  } else if (s.device_schedule?.readback_pending || s.actuation?.status === 'pending') {
    state = 'Change unconfirmed'; detail = 'A complete settings readback is still pending.'
  } else if (s.actuation?.status === 'waiting') {
    state = 'Schedule retained'
    detail = s.actuation.next_write_at ? 'Next change available ' + local(s.actuation.next_write_at) + '.' : 'The current inverter settings remain in effect.'
  } else if (s.actuation?.status === 'accepted' && s.actuation.action === 'daily_schedule' && scheduleFresh(s.device_schedule, s.now) && sameSchedule(s.applied_schedule, s.device_schedule)) {
    state = 'Settings confirmed'; detail = 'Inverter readback ' + clock(s.device_schedule!.checked_at!)
  } else if (s.applied_schedule && scheduleFresh(s.device_schedule, s.now) && !sameSchedule(s.applied_schedule, s.device_schedule)) {
    state = 'Settings changed'; detail = 'The inverter differs from the last confirmed controller settings.'
  } else {
    state = s.readiness.length ? 'Waiting for prices' : 'Ready'
    detail = s.readiness.length ? 'The next schedule is prepared after day-ahead prices arrive.' : 'Monitoring is running; no new schedule has been confirmed.'
  }
  return { state, detail, attention: ['Blocked', 'Needs attention', 'Settings changed', 'Change unconfirmed'].includes(state) }
}

export interface Acceptance {
  state: string
  detail: string
  tone: Tone
}

export function acceptanceFeedback(s: DashboardState): Acceptance {
  const a = s.actuation, device = s.device_schedule
  const confirmed = scheduleFresh(device, s.now) && sameSchedule(s.applied_schedule, device)
  const blocked = blockers(s)
  if (!s.control.enabled) return {
    state: s.control.restore_pending ? 'Restoring native mode' : 'Automatic updates off',
    detail: s.control.restore_pending ? 'Waiting for inverter readback' : 'Monitoring only', tone: 'off',
  }
  if (blocked.length || s.health?.status === 'degraded') return { state: 'Needs attention', detail: blocked[0] || s.health?.message || 'Latest cycle incomplete', tone: 'attention' }
  if (device?.readback_pending || a?.status === 'pending') return { state: 'Change unconfirmed', detail: 'Waiting for a complete settings readback', tone: 'wait' }
  if (confirmed && a?.status === 'accepted') return { state: 'Settings confirmed', detail: 'Inverter readback · ' + clock(device!.checked_at!), tone: 'good' }
  if (s.applied_schedule && scheduleFresh(device, s.now) && !sameSchedule(s.applied_schedule, device)) return { state: 'Settings differ', detail: 'Inverter readback differs from the last confirmed schedule', tone: 'attention' }
  if (a?.status === 'waiting') return { state: 'Schedule retained', detail: 'Existing inverter settings remain in effect', tone: 'wait' }
  if (a?.status === 'blocked' || a?.status === 'error') return { state: 'Update blocked', detail: a.message || 'Check system status', tone: 'attention' }
  if (a?.status === 'accepted') return { state: 'Accepted by inverter', detail: 'Settings accepted · physical execution is not yet confirmed', tone: 'good' }
  return { state: 'Awaiting readback', detail: 'No schedule confirmed yet', tone: 'wait' }
}

export function planStale(s: DashboardState): boolean {
  if (!s.plan) return false
  return s.plan.valid_until ? new Date(s.now) >= new Date(s.plan.valid_until) : new Date(s.now).getTime() - new Date(s.plan.created_at).getTime() > 1800000
}

/** Operator notices shown in the andon banner, most severe first. */
export function notices(s: DashboardState): string[] {
  const historical = !s.plan && s.replay
  return [
    historical ? `Historical replay · ${s.replay!.day} · Hindsight benchmark; no live commands or measured savings.` : null,
    s.health?.status === 'degraded' ? 'Controller: ' + (s.health.message || 'Latest cycle incomplete.') : null,
    planStale(s) ? 'The displayed plan is stale and cannot be dispatched.' : null,
    ...(s.readiness.some(x => x.includes('6017'))
      ? ['Advanced scheduling is unavailable: AlphaESS has not granted this account access (6017). Monitoring and forecasts continue.']
      : s.readiness),
  ].filter((x): x is string => Boolean(x))
}

export interface Andon {
  tone: Tone
  headline: string
  detail: string
}

/** One overall board state: what an operator needs to know in a second. */
export function andon(s: DashboardState, connection: Connection, manager: Manager, acceptance: Acceptance, issues: string[]): Andon {
  if (manager.attention || acceptance.tone === 'attention') {
    return { tone: 'attention', headline: manager.attention ? manager.state : acceptance.state, detail: issues[0] || (manager.attention ? manager.detail : acceptance.detail) }
  }
  if (!connection.connected) return { tone: 'attention', headline: 'Connection needs attention', detail: connection.status + '. ' + issues.join(' ') }
  if (issues.length) return { tone: 'wait', headline: manager.state, detail: issues.join(' ') }
  if (!s.control.enabled) return { tone: 'off', headline: s.control.restore_pending ? 'Restoring native mode' : 'Monitoring only', detail: manager.detail }
  if (acceptance.tone === 'good') return { tone: 'good', headline: 'Running normally', detail: acceptance.state + ' · ' + acceptance.detail.replace(/^Inverter readback · /, 'readback ') }
  return { tone: 'wait', headline: manager.state, detail: manager.detail }
}

export interface BatteryActivity {
  mode: 'charging' | 'supplying' | 'idle' | 'unknown'
  kw: number | null
  text: string
}

export function batteryActivity(s: DashboardState): BatteryActivity {
  const t = s.telemetry
  const age = ageMinutes(s.now, t?.timestamp)
  if (!t || !(age >= 0 && age <= 20)) return { mode: 'unknown', kw: null, text: 'Waiting for fresh data' }
  const p = t.battery_kw
  if (p < -0.15) return { mode: 'charging', kw: -p, text: 'Charging ' + fmt(-p, 2) + ' kW' }
  if (p > 0.15) return { mode: 'supplying', kw: p, text: 'Supplying ' + fmt(p, 2) + ' kW' }
  return { mode: 'idle', kw: 0, text: 'Idle' }
}

export function scheduleNowText(s: DashboardState): string {
  const t = s.telemetry
  if (!scheduleFresh(s.device_schedule, s.now)) return 'Measured battery flow; settings are awaiting confirmation.'
  const current = configuredAction(s.device_schedule, minuteOfDay(s.now))
  return 'Measured ' + (t ? local(t.timestamp) : '—') + '. Schedule now: ' + actionLabel(current) + '. '
    + (current === 'hold' ? 'Solar can still charge the battery while grid charging is off.'
      : current === 'self_consume' ? 'House supply is allowed when needed; EV charging stays grid-only.'
        : 'Grid charging is allowed up to the target SOC.')
}

export function plannerMessage(s: DashboardState): string {
  const c = s.planner_comparison
  let message = 'Selected planner: ' + (s.standard_planner === 'emhass' ? 'EMHASS MPC' : 'Candidate planner') + '.'
  if (c && ['compared', 'planned', 'partial'].includes(c.status ?? '')) {
    message += ' EMHASS comparison ' + local(c.timestamp!) + ' · proposed ' + actionLabel(c.emhass?.next_action) + ' · ' + fmt(c.solve_seconds, 2) + ' s.'
    if (c.candidate_error) message += ' Older-planner comparison unavailable; EMHASS control continues. ' + c.candidate_error
  } else if (c?.status === 'unavailable') message += ' EMHASS shadow unavailable: ' + c.message
  if (s.load_profile?.inferred_ev_samples) message += ' Demand forecast excludes ' + s.load_profile.inferred_ev_samples + ' likely grid-only EV samples; measured site demand includes EV.'
  return message
}

export function healthMessage(s: DashboardState): string {
  if (s.health?.status === 'degraded') return s.health.message || 'Latest controller cycle incomplete.'
  if (s.actuation?.status === 'accepted' && s.actuation.action === 'daily_schedule' && scheduleFresh(s.device_schedule, s.now) && sameSchedule(s.applied_schedule, s.device_schedule)) {
    return 'Charge and household-supply settings match the inverter.'
  }
  return s.health?.message || s.actuation?.message || 'Controller checks every quarter hour.'
}

export function systemNotes(s: DashboardState): string[] {
  const p = s.plan || s.replay
  return [
    s.health?.status === 'degraded' ? s.health.message : null,
    ...s.readiness,
    s.actuation?.message,
    ...(p?.notes || []),
    s.actuation?.status === 'accepted' ? 'Readback confirms which settings the ESS stores. Power readings show what the battery was doing at the measurement time; they do not prove that every scheduled interval was followed. Cloud measurements may arrive several minutes late.' : null,
  ].filter((x): x is string => Boolean(x)).map(note => note.includes('physical execution remains unconfirmed')
    ? 'Settings confirmed by API readback. Timestamped power readings show actual battery activity; cloud data may arrive several minutes late.'
    : note)
}

export function inverterSummary(s: DashboardState): string {
  const snapshot = s.device_schedule, c = snapshot?.charge, d = snapshot?.discharge
  if (!c || !d) return 'No inverter settings are being claimed as installed until a successful read confirms them.'
  const charge = windowText(c, 'charge') || 'Off'
  const supply = d.ctrDis ? (windowText(d, 'discharge') || 'No window') : 'Native all-day supply'
  return 'Grid charge ' + charge + ' · House supply ' + supply + (snapshot!.source === 'pair_readback' ? ' · Confirmed after a controller request.' : ' · Read from existing inverter settings.')
}

export function readbackLabel(s: DashboardState): string {
  const snapshot = s.device_schedule
  if (!snapshot?.checked_at) return 'Inverter settings unavailable'
  if (snapshot.readback_pending) return 'Readback pending'
  if (scheduleFresh(snapshot, s.now)) return (s.health?.status === 'degraded' ? 'Last confirmed · ' : 'Confirmed on inverter · ') + clock(snapshot.checked_at)
  return 'Last known settings · ' + clock(snapshot.checked_at)
}

export function activePlan(s: DashboardState): { rows: Decision[]; historical: boolean } {
  const historical = !s.plan && !!s.replay
  const p = s.plan || s.replay
  return { rows: p?.outlook_decisions || p?.decisions || [], historical }
}
