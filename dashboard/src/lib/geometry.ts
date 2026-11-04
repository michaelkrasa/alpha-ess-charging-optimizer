// Pure SVG path builders (unit-tested; no DOM).

export type XY = [x: number, y: number, t: number]

/** Step path: each price holds flat until the next quarter-hour boundary. */
export function stepPath(points: { x0: number; x1: number; y: number | null }[]): string {
  let d = '', open = false
  for (const p of points) {
    if (p.y === null) { open = false; continue }
    d += (open ? 'L' : 'M') + p.x0.toFixed(2) + ' ' + p.y.toFixed(2) + 'L' + p.x1.toFixed(2) + ' ' + p.y.toFixed(2)
    open = true
  }
  return d
}

/** Closed area under a step path down to `base`, one polygon per contiguous run. */
export function stepArea(points: { x0: number; x1: number; y: number | null }[], base: number): string {
  let d = '', run: { x0: number; x1: number; y: number }[] = []
  const flush = () => {
    if (!run.length) return
    d += 'M' + run[0].x0.toFixed(2) + ' ' + base.toFixed(2)
    for (const p of run) d += 'L' + p.x0.toFixed(2) + ' ' + p.y.toFixed(2) + 'L' + p.x1.toFixed(2) + ' ' + p.y.toFixed(2)
    d += 'L' + run.at(-1)!.x1.toFixed(2) + ' ' + base.toFixed(2) + 'Z'
    run = []
  }
  for (const p of points) {
    if (p.y === null) { flush(); continue }
    run.push(p as { x0: number; x1: number; y: number })
  }
  flush()
  return d
}

/**
 * Monotone cubic (Fritsch–Carlson) through measured points so a curve never
 * overshoots past readings; breaks the line across gaps longer than `maxGap` ms.
 */
export function smoothPath(points: XY[], maxGap = 1200000): string {
  if (!points.length) return ''
  const f = (n: number) => n.toFixed(2)
  let d = 'M' + f(points[0][0]) + ' ' + f(points[0][1])
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i], dx = b[0] - a[0]
    if (dx <= 0 || b[2] - a[2] > maxGap) { d += 'M' + f(b[0]) + ' ' + f(b[1]); continue }
    const slope = (b[1] - a[1]) / dx
    const prev = points[i - 2], next = points[i + 1]
    const before = i > 1 && prev && a[0] - prev[0] > 0 ? (a[1] - prev[1]) / (a[0] - prev[0]) : slope
    const after = next && next[0] - b[0] > 0 ? (next[1] - b[1]) / (next[0] - b[0]) : slope
    let m0 = before * slope <= 0 ? 0 : (2 * before * slope) / (before + slope)
    let m1 = after * slope <= 0 ? 0 : (2 * after * slope) / (after + slope)
    if (slope === 0) m0 = m1 = 0
    else {
      const length = Math.hypot(m0 / slope, m1 / slope)
      if (length > 3) { m0 *= 3 / length; m1 *= 3 / length }
    }
    d += 'C' + f(a[0] + dx / 3) + ' ' + f(a[1] + (m0 * dx) / 3) + ' ' + f(b[0] - dx / 3) + ' ' + f(b[1] - (m1 * dx) / 3) + ' ' + f(b[0]) + ' ' + f(b[1])
  }
  return d
}

export function linePath(points: [number, number][]): string {
  return points.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(2) + ' ' + p[1].toFixed(2)).join('')
}

/** Round-number ticks for a value axis. */
export function niceTicks(min: number, max: number, count = 4): number[] {
  const span = max - min
  if (!(span > 0)) return [min]
  const raw = span / count, mag = 10 ** Math.floor(Math.log10(raw)), norm = raw / mag
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag
  const out: number[] = []
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6)
  return out
}
