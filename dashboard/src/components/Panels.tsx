import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useState, type ReactNode } from 'react'
import type { DashboardState, Decision, Tone } from '../lib/types'
import type { Andon, Acceptance, BatteryActivity, Connection } from '../lib/status'
import { actionLabel, actionShort, configuredAction, deviceSegments, scheduleFresh } from '../lib/schedule'
import { planWindows, priceOf, priceStats, selectedKeys, type DayView, type PriceBasis } from '../lib/series'
import { QUARTER, ZONE, atLocalMinute, clock, clockSeconds, dayKey, dayLabel, finite, fmt, local, minuteOfDay, ms, targetDays } from '../lib/time'
import { Beacon, CountUp, SpotlightCard, Switch } from './bits'

/* ───────────────────────── Header ───────────────────────── */

export function LogoMark({ size = 30 }: { size?: number }) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#161616" />
      <path d="M36 8 18 36h12l-4 20 20-30H34z" fill="#fff" />
    </svg>
  )
}

function LiveClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t) }, [])
  return <span className="live-clock">{clockSeconds(now)}</span>
}

export function Header({ s, connection, simulated, busy, onToggle }: {
  s: DashboardState | null; connection: Connection | null; simulated: boolean; busy: boolean; onToggle: () => void
}) {
  const on = !!s?.control.enabled
  const date = s ? new Date(s.now).toLocaleDateString('en-GB', { timeZone: ZONE, weekday: 'long', day: 'numeric', month: 'long' }) : '—'
  const linkTone: Tone = connection?.connected ? 'good' : 'attention'
  return (
    <header className="topbar">
      <div className="brand">
        <LogoMark />
        <div>
          <div className="brand-name">Household Energy</div>
          <div className="brand-sub">{date} · <LiveClock /> Prague</div>
        </div>
      </div>
      <div className="topbar-right">
        {s?.savings && (
          <div className="saved-pill" id="savedTotal" title={'Measured savings against the same demand and solar without a battery, since ' + sinceLabel(s.savings.since)}>
            <span>Saved</span><b>{euro(s.savings.total_eur)}</b><span>since {sinceLabel(s.savings.since)}</span>
          </div>
        )}
        <div className="link-pill" id="connection" role="status" title={connection?.detail}>
          <Beacon tone={s ? linkTone : 'wait'} size={8} />
          <span>{!s ? 'Connecting…' : connection?.connected ? (simulated ? 'Simulated ESS' : 'ESS connected') : connection?.status}</span>
        </div>
        <div className="auto-tile" data-on={on}>
          <div>
            <div className="auto-title">Automatic control</div>
            <div className="auto-sub" id="controlDescription">{!s ? 'Checking…' : on ? (s.control_check_minutes ? 'Checks every ' + s.control_check_minutes + ' min' : 'Active') : 'Monitoring only'}</div>
          </div>
          <Switch on={on} disabled={!s || busy} onToggle={onToggle} label="Automatic battery management" />
        </div>
      </div>
    </header>
  )
}

/* ───────────────────────── Andon banner ───────────────────────── */

const TONE_WORD: Record<Tone, string> = { good: 'OK', wait: 'WATCH', attention: 'ACT', off: 'IDLE' }

export function AndonBanner({ board, issues, connectionError, commandError, onDetails }: {
  board: Andon; issues: string[]; connectionError: string; commandError: string; onDetails: () => void
}) {
  const tone: Tone = connectionError || commandError ? 'attention' : board.tone
  return (
    <motion.section className="andon" data-tone={tone} layout aria-label="Overall system state">
      <div className="andon-lamp">
        <Beacon tone={tone} size={14} />
        <span>{TONE_WORD[tone]}</span>
      </div>
      <div className="andon-copy">
        <AnimatePresence mode="wait" initial={false}>
          <motion.h1 key={board.headline} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.25 }}>
            {connectionError ? 'Dashboard disconnected' : board.headline}
          </motion.h1>
        </AnimatePresence>
        <p id="notice" role="alert">{connectionError || commandError || board.detail}</p>
        {issues.length > 1 && <ul className="andon-issues">{issues.slice(1).map(x => <li key={x}>{x}</li>)}</ul>}
      </div>
      <button type="button" className="ghost" onClick={onDetails}>System details →</button>
    </motion.section>
  )
}

/* ───────────────────────── KPI tiles ───────────────────────── */

function Tile({ label, tone, icon, children, foot }: { label: string; tone: string; icon: ReactNode; children: ReactNode; foot?: ReactNode }) {
  return (
    <SpotlightCard className="kpi" tone={tone as any} as="article" aria-label={label}>
      <div className="kpi-head"><span>{label}</span>{icon}</div>
      <div className="kpi-body">{children}</div>
      {foot && <div className="kpi-foot">{foot}</div>}
    </SpotlightCard>
  )
}

function Ring({ value }: { value: number | null }) {
  const r = 26, c = 2 * Math.PI * r, v = finite(value) ? Math.max(0, Math.min(100, value)) : 0
  return (
    <svg className="ring" width="64" height="64" viewBox="0 0 64 64" aria-hidden="true">
      <circle cx="32" cy="32" r={r} className="ring-track" />
      <motion.circle cx="32" cy="32" r={r} className="ring-value" strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - v / 100) }} transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1] }} />
    </svg>
  )
}

const SunIcon = () => (
  <svg className="kpi-icon spin" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="6" fill="currentColor" /><path d="M16 2v4m0 20v4M2 16h4m20 0h4M6.1 6.1l2.8 2.8m14.2 14.2 2.8 2.8M6.1 25.9l2.8-2.8M23.1 8.9l2.8-2.8" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" /></svg>
)
const HomeIcon = () => (
  <svg className="kpi-icon" viewBox="0 0 32 32" aria-hidden="true"><path d="M5 15 16 6l11 9v11a1 1 0 0 1-1 1h-6v-7h-8v7H6a1 1 0 0 1-1-1z" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round" /></svg>
)
const GridIcon = () => (
  <svg className="kpi-icon" viewBox="0 0 32 32" aria-hidden="true"><path d="M16 3 9 29m7-26 7 26M11 12h10M9.5 20h13M6 8h20M16 3v26" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
)
const TagIcon = () => (
  <svg className="kpi-icon" viewBox="0 0 32 32" aria-hidden="true"><path d="M4 16V5h11l13 13-11 11z" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round" /><circle cx="10" cy="11" r="2" fill="currentColor" /></svg>
)

export function KpiStrip({ s, activity, basis }: { s: DashboardState; activity: BatteryActivity; basis: PriceBasis }) {
  const t = s.telemetry
  const solarW = t && finite(t.solar_kw) && t.solar_kw < 1
  const totals = s.daily_energy, todayTotals = totals?.day === dayKey(s.now)
  const stored = t && finite(t.capacity_kwh) ? (t.capacity_kwh * t.soc_percent) / 100 : null
  const grid = t?.grid_kw
  const stats = priceStats(s, basis)
  const current = stats?.current ?? (s.price_intervals || []).find(r => ms(r.timestamp) <= ms(s.now) && ms(s.now) < ms(r.timestamp) + QUARTER) ?? null
  const price = current ? priceOf(current, basis) : null
  const rank = stats?.rank
  const rankText = !finite(rank) ? null : rank <= 0.25 ? 'Among cheapest today' : rank >= 0.75 ? 'Among priciest today' : 'Mid-range today'
  const lo = stats ? priceOf(stats.min, basis)! : null, hi = stats ? priceOf(stats.max, basis)! : null
  const marker = finite(price) && finite(lo) && finite(hi) && hi > lo ? (price - lo) / (hi - lo) : null

  return (
    <section className="kpis" aria-label="Current energy readings">
      <Tile label="Battery" tone="battery" icon={<Ring value={t?.soc_percent ?? null} />}
        foot={<>
          <span id="batteryActivity" className={'flow ' + activity.mode}>{activity.mode === 'charging' ? '▼ ' : activity.mode === 'supplying' ? '▲ ' : ''}{activity.text}</span>
          <span className="muted">{finite(stored) ? fmt(stored, 1) + ' / ' + fmt(t!.capacity_kwh, 1) + ' kWh' : ''}</span>
        </>}>
        <span className="kpi-value"><CountUp value={t?.soc_percent} digits={0} /><small>%</small></span>
        <span className="kpi-meta" id="batteryCaption">{t ? 'Reserve ' + fmt(s.reserve_percent, 0) + '% · measured ' + clock(t.timestamp) : 'Waiting for a reading'}</span>
      </Tile>

      <Tile label="Solar" tone="solar" icon={<SunIcon />}
        foot={<span id="solarToday">{todayTotals && finite(totals!.solar_generated_kwh) ? fmt(totals!.solar_generated_kwh, 1) + ' kWh generated today' + (totals!.checked_at && (ms(s.now) - ms(totals!.checked_at)) / 60000 > 30 ? ' · as of ' + clock(totals!.checked_at) : '') : 'Today’s generation unavailable'}</span>}>
        <span className="kpi-value"><CountUp value={t ? (solarW ? t.solar_kw * 1000 : t.solar_kw) : null} digits={solarW ? 0 : 2} /><small id="solarUnit">{solarW ? 'W' : 'kW'}</small></span>
        <span className="kpi-meta" id="batteryChargedToday" title="Total battery charging from all sources; this is not all solar energy.">{todayTotals && finite(totals!.battery_charged_kwh) ? fmt(totals!.battery_charged_kwh, 1) + ' kWh into battery · solar + grid' : 'Measured PV output'}</span>
      </Tile>

      <Tile label="House demand" tone="house" icon={<HomeIcon />} foot={<span className="muted">Measured site demand, incl. EV</span>}>
        <span className="kpi-value"><CountUp value={t?.load_kw} digits={2} /><small>kW</small></span>
        <span className="kpi-meta">{t ? 'as of ' + clock(t.timestamp) : 'Waiting for a reading'}</span>
      </Tile>

      <Tile label="Grid" tone="grid" icon={<GridIcon />} foot={<span className="muted">Positive = import from grid</span>}>
        <span className="kpi-value"><CountUp value={finite(grid) ? Math.abs(grid) : null} digits={2} /><small>kW</small></span>
        <span className={'kpi-meta flow ' + (finite(grid) ? (grid > 0.05 ? 'importing' : grid < -0.05 ? 'exporting' : 'idle') : '')}>
          {finite(grid) ? (grid > 0.05 ? 'Importing' : grid < -0.05 ? 'Exporting' : 'Balanced') : 'Not reported'}
        </span>
      </Tile>

      <Tile label={basis === 'spot' ? 'Market spot now' : 'Import price now'} tone="price" icon={<TagIcon />}
        foot={finite(marker) ? (
          <div className="price-scale" title={'Today range ' + fmt(lo, 0) + '–' + fmt(hi, 0) + ' €/MWh'}>
            <span>{fmt(lo, 0)}</span>
            <div className="price-track"><motion.i animate={{ left: (marker * 100).toFixed(1) + '%' }} transition={{ type: 'spring', stiffness: 120, damping: 20 }} /></div>
            <span>{fmt(hi, 0)}</span>
          </div>
        ) : <span className="muted">Today’s range unavailable</span>}>
        <span className="kpi-value" id="price"><CountUp value={price} digits={0} /><small>€/MWh</small></span>
        <span className="kpi-meta">{rankText ?? (basis === 'spot' ? 'Excludes fees & VAT' : 'Fees & VAT included')}</span>
      </Tile>
    </section>
  )
}

/* ───────────────────────── Right now ───────────────────────── */

function duration(msValue: number) {
  const m = Math.max(0, Math.round(msValue / 60000)), h = Math.floor(m / 60)
  return h ? `${h} h ${String(m % 60).padStart(2, '0')} min` : `${m} min`
}

export function NowPanel({ s, plan, acceptance }: { s: DashboardState; plan: Decision[]; acceptance: Acceptance }) {
  const fresh = scheduleFresh(s.device_schedule, s.now)
  const minute = minuteOfDay(s.now), key = dayKey(s.now), now = ms(s.now)
  const segs = deviceSegments(s.device_schedule)
  const index = segs.findIndex(x => x.a <= minute && minute < x.b)
  const currentSeg = segs[index]
  const current = s.device_schedule?.charge ? configuredAction(s.device_schedule, minute) : null
  // Next change: first following window with a different action; the daily
  // inverter schedule repeats, so wrap into tomorrow.
  let next: (typeof segs)[number] | undefined, nextAt: number | null = null
  for (let k = 1; index >= 0 && k <= segs.length; k++) {
    const j = index + k, seg = segs[j % segs.length]
    if (seg.action !== current) {
      next = seg
      nextAt = atLocalMinute(j >= segs.length ? targetDays(s.now).tomorrow : key, seg.a)
      break
    }
  }
  const first = plan[0]

  return (
    <SpotlightCard className="panel now-panel" as="article" aria-labelledby="nowTitle">
      <div className="panel-head">
        <h2 id="nowTitle">Right now</h2>
        <span className={'status-chip'} data-tone={acceptance.tone} id="acceptance">
          <Beacon tone={acceptance.tone} size={7} />
          <span id="acceptanceState">{acceptance.state}</span>
        </span>
      </div>
      <div className="now-action" data-action={current ?? 'unknown'}>
        <span className="now-label">{fresh ? 'Inverter is set to' : 'Last known setting'}</span>
        <AnimatePresence mode="wait" initial={false}>
          <motion.strong key={current ?? 'x'} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 8 }}>
            {current ? actionLabel(current) : 'Unknown'}
          </motion.strong>
        </AnimatePresence>
        {currentSeg && <span className="now-window">{fmtWindow(currentSeg.a, currentSeg.b)}{fresh ? '' : ' · unconfirmed'}</span>}
      </div>
      <dl className="now-facts">
        <div>
          <dt>Next change</dt>
          <dd>{next && nextAt ? <><b className={'dot-' + next.action}>{actionShort(next.action)}</b> at {clock(nextAt)} <span className="muted">· in {duration(nextAt - now)}</span></> : '—'}</dd>
        </div>
        <div>
          <dt>Next interval plan</dt>
          <dd id="action">{first ? <><b className={'dot-' + first.action}>{actionShort(first.action)}</b> from {clock(first.timestamp)}</> : 'Waiting for data'}</dd>
        </div>
        <div>
          <dt>Readback</dt>
          <dd id="acceptanceDetail">{acceptance.detail}</dd>
        </div>
      </dl>
      {first?.reason && <p className="now-reason" id="reason">{first.reason}</p>}
    </SpotlightCard>
  )
}

const fmtWindow = (a: number, b: number) => {
  const c = (m: number) => (m === 1440 ? '24:00' : String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'))
  return c(a) + '–' + c(b)
}

/* ───────────────────────── Price summary ───────────────────────── */

/* ───────────────────────── Savings ───────────────────────── */

export const euro = (v: number | null | undefined) =>
  typeof v === 'number' && Number.isFinite(v) ? (v < 0 ? '−' : '') + '€' + Math.abs(v).toFixed(2) : '—'

export const sinceLabel = (day?: string) =>
  day ? new Date(day + 'T12:00:00Z').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : '—'

function SavedValue({ value }: { value: number | null | undefined }) {
  const ok = typeof value === 'number' && Number.isFinite(value)
  return (
    <strong className={ok ? (value! < 0 ? 'bad' : 'good') : undefined}>
      {ok && value! < 0 ? '−€' : '€'}<CountUp value={ok ? Math.abs(value!) : null} digits={2} />
    </strong>
  )
}

export function SavingsPanel({ s }: { s: DashboardState }) {
  const v = s.savings
  const partial = (d?: { quarters: number } | null) => (d && d.quarters < 92 ? d.quarters + ' of 96 quarters measured' : null)
  return (
    <SpotlightCard className="panel savings-panel" as="article" aria-labelledby="savTitle">
      <div className="panel-head"><h2 id="savTitle">Battery savings</h2><span className="muted small">vs. no battery</span></div>
      {v ? (
        <>
          <div className="stat-trio" id="savings">
            <div><span>Yesterday</span><SavedValue value={v.yesterday?.saved_eur} /><em>{v.yesterday ? partial(v.yesterday) ?? 'full day' : 'not counted'}</em></div>
            <div><span>Today so far</span><SavedValue value={v.today?.saved_eur} /><em>{v.today ? v.today.quarters + ' quarters' : '—'}</em></div>
            <div><span>Total</span><SavedValue value={v.total_eur} /><em>since {sinceLabel(v.since)}</em></div>
          </div>
          <p className="muted small savings-note">
            Measured grid cost against the same house demand and solar with no battery, at your billed import and export prices.
            Battery losses count; standing charges are excluded.
          </p>
        </>
      ) : <p className="muted small">Counting starts with the first measured quarter hour.</p>}
    </SpotlightCard>
  )
}

export function DaySummary({ s, basis, view }: { s: DashboardState; basis: PriceBasis; view: DayView }) {
  const stats = priceStats(s, basis)
  const keys = selectedKeys(s.now, view)
  const insights = (s.plan_insights || []).filter(r => keys.includes(r.day))
  return (
    <SpotlightCard className="panel summary-panel" as="article" aria-labelledby="sumTitle">
      <div className="panel-head"><h2 id="sumTitle">Today’s prices</h2><span className="muted small">{basis === 'spot' ? 'Market spot' : 'Import incl. fees & VAT'}</span></div>
      {stats ? (
        <div className="stat-trio" id="priceStats">
          <div><span>Low</span><strong className="good"><CountUp value={priceOf(stats.min, basis)} digits={0} /></strong><em>{clock(stats.min.timestamp)}</em></div>
          <div><span>Mean</span><strong><CountUp value={stats.mean} digits={0} /></strong><em>€/MWh</em></div>
          <div><span>High</span><strong className="bad"><CountUp value={priceOf(stats.max, basis)} digits={0} /></strong><em>{clock(stats.max.timestamp)}</em></div>
        </div>
      ) : <p className="muted" id="priceStats">Today’s price statistics unavailable</p>}
      <div className="insights" id="planInsight">
        {insights.map(row => (
          <div key={row.day}><strong>{dayLabel(row.day, s.now)}</strong><p>{row.text}</p></div>
        ))}
        {!insights.length && <p className="muted">Plan insight appears once a plan covers this day.</p>}
      </div>
      {s.soc_tracking?.latest && (
        <p className="soc-caption" id="socDeviation">
          SOC tracking {clock(s.soc_tracking.latest.timestamp)} · expected {fmt(s.soc_tracking.latest.expected_soc, 1)}% · measured {fmt(s.soc_tracking.latest.actual_soc, 1)}% ·{' '}
          <b>Δ {(s.soc_tracking.latest.delta_pp >= 0 ? '+' : '') + fmt(s.soc_tracking.latest.delta_pp, 1)} pp</b>
        </p>
      )}
    </SpotlightCard>
  )
}

/* ───────────────────────── Plan windows ───────────────────────── */

export function PlanWindows({ s, plan, view }: { s: DashboardState; plan: Decision[]; view: DayView }) {
  const [all, setAll] = useState(false)
  const keys = selectedKeys(s.now, view)
  const windows = planWindows(plan, keys)
  const fresh = scheduleFresh(s.device_schedule, s.now)
  const rows = plan.filter(r => keys.includes(dayKey(r.timestamp)))
  return (
    <SpotlightCard className="panel plan-panel" as="article" aria-labelledby="planTitle">
      <div className="panel-head">
        <h2 id="planTitle">{view === 'tomorrow' ? 'Tomorrow’s plan' : view === 'all' ? 'Two-day plan' : 'Today’s plan'}</h2>
        <span className="muted small" id="source">{!s.plan && s.replay ? 'Historical replay' : s.plan || s.replay ? (s.control_mode === 'legacy_daily' ? 'Rolling proposal' : 'Forecast') : 'No plan yet'}</span>
      </div>
      {!windows.length && <p className="muted empty-plan">{view === 'tomorrow' ? 'Tomorrow’s plan appears when day-ahead prices are published, usually around 15:00 Prague time.' : 'No plan available for this day yet.'}</p>}
      <ol className="windows">
        {windows.map((w, i) => {
          const prices = w.rows.map(r => r.import_eur_kwh).filter(finite).map(v => v * 1000)
          const avg = prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : null
          const grid = w.rows.reduce((a, r) => a + Math.max(0, r.grid_charge_kw || 0) * 0.25, 0)
          const supply = w.rows.reduce((a, r) => a + Math.max(0, r.battery_load_kw || 0) * 0.25, 0)
          // Start SOC: previous window's projected end, or the latest reading for a window under way.
          const socFrom = i > 0 ? windows[i - 1].rows.at(-1)?.soc_percent
            : w.start <= ms(s.now) + QUARTER ? s.telemetry?.soc_percent : null
          const socTo = w.rows.at(-1)?.soc_percent
          const device = fresh ? configuredAction(s.device_schedule, minuteOfDay(w.start)) : null
          return (
            <motion.li key={w.start} className={'window ' + w.action} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i * 0.03, 0.4) }}>
              <span className="window-bar" />
              <div className="window-time">
                <strong>{clock(w.start)}–{clock(w.end) === '00:00' ? '24:00' : clock(w.end)}</strong>
                <span>{view === 'all' ? dayLabel(dayKey(w.start), s.now).split(' · ')[0] + ' · ' : ''}{duration(w.end - w.start)}</span>
              </div>
              <div className="window-action">{actionLabel(w.action)}</div>
              <div className="window-num"><span>avg import</span><strong>{fmt(avg, 0)}</strong></div>
              <div className="window-num"><span>{grid > 0.05 ? 'grid in' : 'supplied'}</span><strong>{fmt(grid > 0.05 ? grid : supply, 1)}<small> kWh</small></strong></div>
              <div className="window-num"><span>battery</span><strong>{finite(socFrom) ? fmt(socFrom, 0) + '→' : ''}{fmt(socTo, 0)}%</strong></div>
              <div className={'window-check ' + (device === w.action ? 'ok' : '')} title={fresh ? 'Setting on inverter at window start' : 'Inverter settings not confirmed'}>
                {fresh ? (device === w.action ? '✓ on inverter' : actionShort(device)) : 'unconfirmed'}
              </div>
            </motion.li>
          )
        })}
      </ol>
      {rows.length > 0 && (
        <details className="intervals" onToggle={e => setAll((e.target as HTMLDetailsElement).open)}>
          <summary>All {rows.length} quarter-hour intervals</summary>
          {all && (
            <div className="scroll">
              <table>
                <thead><tr><th>Time</th><th>Action</th><th>Grid charge</th><th>House supply</th><th>Battery after</th><th>Import €/MWh</th><th>On inverter</th></tr></thead>
                <tbody id="planRows">
                  {rows.map(r => {
                    const device = fresh ? configuredAction(s.device_schedule, minuteOfDay(r.timestamp)) : null
                    return (
                      <tr key={r.timestamp}>
                        <td>{view === 'all' ? local(r.timestamp) : clock(r.timestamp)}</td>
                        <td><span className={'action-pill ' + r.action}>{actionShort(r.action)}</span></td>
                        <td>{fmt(r.grid_charge_kw, 2)} kW</td>
                        <td>{fmt(r.battery_load_kw, 2)} kW</td>
                        <td>{fmt(r.soc_percent, 0)}%</td>
                        <td>{fmt(finite(r.import_eur_kwh) ? r.import_eur_kwh * 1000 : null, 1)}</td>
                        <td>{fresh ? (device === r.action ? '✓ ' : '') + actionLabel(device) : 'Not confirmed'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </details>
      )}
    </SpotlightCard>
  )
}

/* ───────────────────────── System status ───────────────────────── */

export function SystemPanel({ id, rows, notes, attention }: { id: string; rows: { label: string; value: ReactNode; tone?: Tone }[]; notes: string[]; attention: boolean }) {
  return (
    <SpotlightCard className="panel system-panel" as="section" aria-labelledby="sysTitle" tone={attention ? 'attention' : undefined}>
      <div className="panel-head" id={id}><h2 id="sysTitle">System status</h2></div>
      <dl className="sys-rows">
        {rows.map(r => (
          <div key={r.label}>
            <dt>{r.tone && <Beacon tone={r.tone} size={7} />}{r.label}</dt>
            <dd>{r.value}</dd>
          </div>
        ))}
      </dl>
      {notes.length > 0 && (
        <details className="notes" id="details">
          <summary>Controller notes ({notes.length})</summary>
          {notes.map(n => <p key={n}>{n}</p>)}
        </details>
      )}
    </SpotlightCard>
  )
}
