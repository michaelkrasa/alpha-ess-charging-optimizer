// Decode AlphaESS charge/discharge window settings exactly as the inverter applies them.
import type { Action, Decision, Schedule } from './types'
import { ageMinutes, dayKey, minuteClock, minuteOfDay, type Stamp } from './time'

export const ACTION_LABEL: Record<string, string> = {
  charge: 'Grid charge',
  hold: 'Preserve battery',
  solar: 'Store solar',
  self_consume: 'Supply the house',
  unknown: 'Unknown',
}

export const ACTION_SHORT: Record<string, string> = {
  charge: 'Grid charge',
  hold: 'Preserve',
  solar: 'Store solar',
  self_consume: 'House supply',
  unknown: 'Unknown',
}

export const actionLabel = (a?: string | null) => (a ? ACTION_LABEL[a] ?? a : 'Unknown')
export const actionShort = (a?: string | null) => (a ? ACTION_SHORT[a] ?? a : 'Unknown')

type Kind = 'charge' | 'discharge'

export function windows(config: Record<string, unknown> | undefined, kind: Kind): [number, number][] {
  if (!config) return []
  const prefix = kind === 'charge' ? 'timeCha' : 'timeDis'
  const flag = kind === 'charge' ? 'gridCharge' : 'ctrDis'
  if (!config[flag]) return []
  return [1, 2]
    .map(j => [config[prefix + 'f' + j], config[prefix + 'e' + j]] as [unknown, unknown])
    .filter((pair): pair is [string, string] => typeof pair[0] === 'string' && typeof pair[1] === 'string' && !!pair[0] && !!pair[1] && pair[0] !== pair[1])
    .map(([a, b]) => [Number(a.slice(0, 2)) * 60 + Number(a.slice(3)), Number(b.slice(0, 2)) * 60 + Number(b.slice(3))] as [number, number])
    .sort((a, b) => a[0] - b[0])
}

export function configuredAction(schedule: Schedule | null | undefined, minute: number): Action {
  if (!schedule?.charge || !schedule?.discharge) return 'unknown'
  if (windows(schedule.charge, 'charge').some(([a, b]) => a <= minute && minute < b)) return 'charge'
  return !schedule.discharge.ctrDis || windows(schedule.discharge, 'discharge').some(([a, b]) => a <= minute && minute < b)
    ? 'self_consume'
    : 'hold'
}

export function scheduleFresh(schedule: Schedule | null | undefined, now: Stamp): boolean {
  if (!schedule || schedule.readback_pending) return false
  const age = ageMinutes(now, schedule.checked_at)
  return age >= 0 && age <= 20
}

export function sameSchedule(a?: Schedule | null, b?: Schedule | null): boolean {
  if (!a || !b) return false
  return (['charge', 'discharge'] as const).every(k => {
    const fields = Object.keys(a[k] || {})
    return fields.length > 0 && fields.every(f => (a[k] as Record<string, unknown>)[f] === (b[k] as Record<string, unknown> | undefined)?.[f])
  })
}

export interface Segment {
  a: number // minute of day, inclusive
  b: number // minute of day, exclusive
  action: Action
}

export function deviceSegments(schedule: Schedule | null | undefined): Segment[] {
  if (!schedule?.charge || !schedule?.discharge) return []
  const bounds = [...new Set([0, 1440, ...windows(schedule.charge, 'charge').flat(), ...windows(schedule.discharge, 'discharge').flat()])].sort((a, b) => a - b)
  return bounds.slice(0, -1).map((a, i) => ({ a, b: bounds[i + 1], action: configuredAction(schedule, a) }))
}

export function planSegments(plan: Decision[], key: string): Segment[] {
  const parts = plan
    .filter(r => dayKey(r.timestamp) === key)
    .map(r => ({ a: minuteOfDay(r.timestamp), b: Math.min(1440, minuteOfDay(r.timestamp) + 15), action: r.action }))
  return parts.reduce<Segment[]>((out, next) => {
    const last = out.at(-1)
    if (last && last.b === next.a && last.action === next.action) last.b = next.b
    else out.push({ ...next })
    return out
  }, [])
}

export function windowText(config: Record<string, unknown> | undefined, kind: Kind): string {
  return windows(config, kind).map(([a, b]) => minuteClock(a) + '–' + minuteClock(b)).join(', ')
}
