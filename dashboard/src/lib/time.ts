// All household times are shown in Europe/Prague regardless of browser zone.
export const ZONE = 'Europe/Prague'
export const QUARTER = 15 * 60 * 1000

const dayFormat = new Intl.DateTimeFormat('en-CA', { timeZone: ZONE, year: 'numeric', month: '2-digit', day: '2-digit' })
const clockFormat = new Intl.DateTimeFormat('en-GB', { timeZone: ZONE, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const secondsFormat = new Intl.DateTimeFormat('en-GB', { timeZone: ZONE, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
const localFormat = new Intl.DateTimeFormat('en-GB', { timeZone: ZONE, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })

export type Stamp = string | number | Date

export const ms = (t: Stamp) => new Date(t).getTime()
export const dayKey = (t: Stamp) => dayFormat.format(new Date(t))
export const clock = (t: Stamp) => clockFormat.format(new Date(t))
export const clockSeconds = (t: Stamp) => secondsFormat.format(new Date(t))
export const local = (t: Stamp) => localFormat.format(new Date(t))

export function minuteOfDay(t: Stamp): number {
  const parts = clockFormat.formatToParts(new Date(t))
  return Number(parts.find(p => p.type === 'hour')!.value) * 60 + Number(parts.find(p => p.type === 'minute')!.value)
}

export function minuteClock(m: number): string {
  if (m === 1440) return '24:00'
  return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0')
}

export function targetDays(now: Stamp) {
  const today = dayKey(now)
  const d = new Date(today + 'T12:00:00Z')
  d.setUTCDate(d.getUTCDate() + 1)
  return { today, tomorrow: dayKey(d) }
}

export function dayLabel(key: string, now: Stamp): string {
  const dates = targetDays(now)
  const relative = key === dates.today ? 'Today' : key === dates.tomorrow ? 'Tomorrow' : null
  const date = new Date(key + 'T12:00:00Z').toLocaleDateString('en-GB', { timeZone: ZONE, weekday: 'short', day: 'numeric', month: 'short' })
  return relative ? `${relative} · ${date}` : date
}

/** UTC instant of local midnight starting a Prague calendar day (DST-safe). */
export function dayStart(key: string): number {
  // Prague is UTC+1 or UTC+2; probe both offsets and keep the one that is local midnight.
  for (const offset of [2, 1]) {
    const candidate = Date.parse(key + 'T00:00:00Z') - offset * 3600000
    if (dayKey(candidate) === key && minuteOfDay(candidate) === 0) return candidate
  }
  return Date.parse(key + 'T00:00:00Z')
}

export function ageMinutes(now: Stamp, stamp?: Stamp | null): number {
  if (!stamp) return Infinity
  const value = (ms(now) - ms(stamp)) / 60000
  return Number.isFinite(value) ? value : Infinity
}

export const fmt = (x: number | null | undefined, digits = 1) =>
  typeof x === 'number' && Number.isFinite(x) ? x.toFixed(digits) : '—'

export const finite = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)

/** UTC instant of a local wall-clock minute on a Prague day (correct across DST changes). */
export function atLocalMinute(key: string, minute: number): number {
  if (minute >= 1440) {
    const next = new Date(key + 'T12:00:00Z'); next.setUTCDate(next.getUTCDate() + 1)
    return dayStart(next.toISOString().slice(0, 10))
  }
  let t = dayStart(key) + minute * 60000
  let drift = minuteOfDay(t) - minute
  if (drift > 720) drift -= 1440
  if (drift < -720) drift += 1440
  return t - drift * 60000
}
