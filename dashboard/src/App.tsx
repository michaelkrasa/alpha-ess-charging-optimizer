import { MotionConfig } from 'motion/react'
import { useEffect, useMemo, useState } from 'react'
import { isDemo, useDashboard } from './lib/api'
import type { Tone } from './lib/types'
import {
  acceptanceFeedback, activePlan, andon, batteryActivity, connectionFeedback, healthMessage,
  inverterSummary, managerFeedback, notices, plannerMessage, readbackLabel, scheduleNowText, systemNotes,
} from './lib/status'
import { tomorrowAvailable, type DayView, type PriceBasis } from './lib/series'
import { fmt, local } from './lib/time'
import { Segmented } from './components/bits'
import { AndonBanner, DaySummary, Header, KpiStrip, NowPanel, PlanWindows, SystemPanel } from './components/Panels'
import { Timeline } from './components/Timeline'

const TONE_COLOR: Record<Tone, string> = { good: '#24a148', wait: '#f1c21b', attention: '#da1e28', off: '#8d8d8d' }

/** Favicon mirrors the board: bolt on a tile with a status dot. */
export function faviconFor(tone: Tone): string {
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' fill='#161616'/><path d='M36 8 18 36h12l-4 20 20-30H34z' fill='#fff'/><circle cx='51' cy='13' r='10' fill='${TONE_COLOR[tone]}' stroke='#fff' stroke-width='4'/></svg>`
  return 'data:image/svg+xml,' + encodeURIComponent(svg)
}

export default function App() {
  const feed = useDashboard()
  const s = feed.data
  const simulated = isDemo()
  const [view, setView] = useState<DayView>('today')
  const [basis, setBasis] = useState<PriceBasis>('import')

  const derived = useMemo(() => {
    if (!s) return null
    const connection = connectionFeedback(s, simulated)
    const manager = managerFeedback(s)
    const acceptance = acceptanceFeedback(s)
    const issues = notices(s)
    const board = andon(s, connection, manager, acceptance, issues)
    const { rows: plan, historical } = activePlan(s)
    return { connection, manager, acceptance, issues, board, plan, historical, activity: batteryActivity(s), canTomorrow: tomorrowAvailable(s) }
  }, [s, simulated])

  // Fall back to today when tomorrow's prices are not (yet) published.
  useEffect(() => { if (derived && !derived.canTomorrow && view !== 'today') setView('today') }, [derived, view])

  const tone: Tone = feed.connectionError ? 'attention' : derived?.board.tone ?? 'wait'
  useEffect(() => {
    const link = document.getElementById('favicon') as HTMLLinkElement | null
    if (link) link.href = faviconFor(tone)
    const soc = s?.telemetry?.soc_percent
    document.title = (Number.isFinite(soc) ? fmt(soc, 0) + '% · ' : '') + (derived?.board.headline ?? 'Connecting') + ' · Household energy'
  }, [tone, s, derived])

  const showDetails = () => document.getElementById('system')?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  return (
    <MotionConfig reducedMotion="user">
      <main>
        <Header s={s} connection={derived?.connection ?? null} simulated={simulated} busy={feed.busy} onToggle={() => s && feed.setEnabled(!s.control.enabled)} />

        {s && derived ? (
          <>
            <AndonBanner board={derived.board} issues={derived.issues} connectionError={feed.connectionError} commandError={feed.commandError} onDetails={showDetails} />
            <KpiStrip s={s} activity={derived.activity} basis={basis} />

            <div className="main-grid">
              <section className="panel timeline-panel" aria-labelledby="tlTitle">
                <div className="panel-head timeline-head">
                  <div>
                    <h2 id="tlTitle">{derived.historical ? 'Replay · ' + s.replay!.day : 'Outlook'}</h2>
                    <p className="muted small">Price, battery and solar with the battery schedule · hover or click any quarter hour</p>
                  </div>
                  <div className="toolbar">
                    <Segmented id="basis" label="Price basis" value={basis} onChange={setBasis}
                      options={[{ value: 'import', label: 'My import' }, { value: 'spot', label: 'Market' }]} />
                    <Segmented id="day" label="Plan day" value={view} onChange={setView}
                      options={[
                        { value: 'today', label: 'Today' },
                        { value: 'tomorrow', label: 'Tomorrow', disabled: !derived.canTomorrow, title: derived.canTomorrow ? undefined : 'Available when tomorrow’s prices are published' },
                        { value: 'all', label: 'Both days', disabled: !derived.canTomorrow, title: derived.canTomorrow ? undefined : 'Available when tomorrow’s prices are published' },
                      ]} />
                  </div>
                </div>
                <Timeline s={s} plan={derived.plan} view={view} basis={basis} />
                <div className="legend">
                  <span><i className="key price" />{basis === 'spot' ? 'Market spot' : 'Your import'} €/MWh</span>
                  <span><i className="key measured" />Measured battery</span>
                  <span><i className="key forecast" />Forecast battery</span>
                  <span><i className="key forecast dashed" />Past expectation</span>
                  <span><i className="key solar" />Solar forecast</span>
                  <span><i className="key solar solid" />Measured solar</span>
                  <span className="legend-gap"><i className="sw charge" />Grid charge</span>
                  <span><i className="sw self_consume" />House supply</span>
                  <span><i className="sw solar" />Store solar</span>
                  <span><i className="sw hold" />Preserve</span>
                  <span className="legend-note">Lighter before Now = measured</span>
                </div>
              </section>
              <aside className="side">
                <NowPanel s={s} plan={derived.plan} acceptance={derived.acceptance} />
                <DaySummary s={s} basis={basis} view={view} />
              </aside>
            </div>

            <div className="lower-grid">
              <PlanWindows s={s} plan={derived.plan} view={view} />
              <SystemPanel
                id="system"
                attention={derived.board.tone === 'attention'}
                rows={[
                  { label: 'Connection', value: <><b id="monitorStatus">{derived.connection.status}</b><span className="muted" id="monitorDetail">{derived.connection.detail}</span></>, tone: derived.connection.connected ? 'good' : 'attention' },
                  { label: 'Controller', value: <><b id="managerStatus">{derived.manager.state}</b><span className="muted">{derived.manager.detail}</span></>, tone: derived.manager.attention ? 'attention' : s.control.enabled ? 'good' : 'off' },
                  { label: 'Health', value: <span id="healthMessage">{healthMessage(s)}</span> },
                  { label: 'Inverter settings', value: <><b id="scheduleReadback">{readbackLabel(s)}</b><span className="muted" id="inverterSummary">{inverterSummary(s)}</span></> },
                  { label: 'Battery flow', value: <span id="batteryActivityDetail">{scheduleNowText(s)}</span> },
                  { label: 'Planner', value: <span id="plannerComparison">{plannerMessage(s)}</span> },
                ]}
                notes={systemNotes(s)}
              />
            </div>
          </>
        ) : (
          <Loading error={feed.connectionError} />
        )}

        <footer>
          <span>Europe/Prague · Prices in EUR</span>
          <span id="releaseInfo">Version {s?.release?.version || 'unavailable'} · {s?.release?.deployed_at ? 'Deployed ' + local(s.release.deployed_at) : 'Deployment time unavailable'}</span>
          <span id="updated">{feed.refreshedAt ? 'Last refresh ' + feed.refreshedAt.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : 'Connecting…'}</span>
        </footer>
      </main>
    </MotionConfig>
  )
}

function Loading({ error }: { error: string }) {
  return (
    <div className="loading" role="status">
      <div className="skeleton andon-skel" />
      <div className="kpis">{[0, 1, 2, 3, 4].map(i => <div key={i} className="skeleton kpi-skel" />)}</div>
      <div className="skeleton timeline-skel" />
      <p id="connectionError">{error || 'Connecting to the controller…'}</p>
    </div>
  )
}
