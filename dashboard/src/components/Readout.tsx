import { motion } from 'motion/react'
import type { Inspection, PriceBasis } from '../lib/series'
import { actionLabel } from '../lib/schedule'
import { clock, finite, fmt } from '../lib/time'

const CARD_W = 312

/** Large, high-contrast numbers for the hovered quarter hour. */
export function Readout({ detail: d, basis, dayText, left, top, width, pinned }: {
  detail: Inspection; basis: PriceBasis; dayText: string; left: number; top: number; width: number; pinned: boolean
}) {
  const flip = left + 22 + CARD_W > width
  const x = flip ? Math.max(8, left - 22 - CARD_W) : left + 22
  const y = Math.max(8, Math.min(top - 40, 150))
  const primary = basis === 'spot' ? d.spot : d.importPrice
  const secondary = basis === 'spot' ? d.importPrice : d.spot
  const delta = d.delta

  return (
    <motion.div
      id="tooltip"
      className="readout"
      role="status"
      aria-live="polite"
      initial={{ opacity: 0, scale: 0.96, y: 4 }}
      animate={{ opacity: 1, scale: 1, y: 0, left: x, top: y }}
      exit={{ opacity: 0, scale: 0.97, transition: { duration: 0.12 } }}
      transition={{ type: 'spring', stiffness: 600, damping: 42, mass: 0.6 }}
      style={{ width: CARD_W }}
    >
      <header>
        <div>
          <div className="readout-time">{clock(d.start)}<span>–{clock(d.end) === '00:00' ? '24:00' : clock(d.end)}</span></div>
          <div className="readout-day">{dayText}</div>
        </div>
        <div className="readout-chips">
          {d.tariff && <span className="chip" title={d.tariffVerified === false ? 'Tariff times unverified; VT assumed' : 'Distribution tariff'}>{d.tariff}{d.tariffVerified === false ? '?' : ''}</span>}
          <span className={'chip ' + (d.elapsed ? 'past' : 'future')}>{d.elapsed ? 'Elapsed' : 'Upcoming'}</span>
        </div>
      </header>

      <section className="readout-price">
        <span className="readout-label"><i className="key price" />{basis === 'spot' ? 'Market spot' : 'Import price'}</span>
        <div className="readout-big">{fmt(primary, 1)}<small>€/MWh</small></div>
        <div className="readout-sub">
          {basis === 'spot' ? 'Your import' : 'Spot'} {fmt(secondary, 1)}
          {finite(primary) && <> · {fmt(primary / 10, 2)} c/kWh</>}
        </div>
      </section>

      <div className="readout-section">Battery at {clock(d.start)}</div>
      <section className="readout-grid">
        <div>
          <span className="readout-label"><i className="key forecast" />Expected</span>
          <strong>{finite(d.expected) ? <>{fmt(d.expected, 1)}<small>%</small></> : '—'}</strong>
        </div>
        <div>
          <span className="readout-label"><i className="key measured" />Measured</span>
          <strong>{finite(d.actual) ? <>{fmt(d.actual, 1)}<small>%</small></> : '—'}</strong>
        </div>
        <div>
          <span className="readout-label">Difference</span>
          <strong className={finite(delta) ? (Math.abs(delta) >= 5 ? 'warn' : '') : 'muted'}>{finite(delta) ? <>{(delta >= 0 ? '+' : '') + fmt(delta, 1)}<small> pp</small></> : '—'}</strong>
        </div>
      </section>
      <div className="readout-section">Solar & demand · kW</div>
      <section className="readout-grid">
        <div>
          <span className="readout-label"><i className="key solar" />Forecast</span>
          <strong>{finite(d.solarForecast) ? fmt(d.solarForecast, 2) : '—'}</strong>
        </div>
        <div>
          <span className="readout-label"><i className="key solar solid" />Measured</span>
          <strong>{finite(d.solarMeasured) ? '≈' + fmt(d.solarMeasured, 2) : '—'}</strong>
        </div>
        <div>
          <span className="readout-label">Demand</span>
          <strong>{finite(d.demandMeasured) ? '≈' + fmt(d.demandMeasured, 2) : '—'}</strong>
        </div>
      </section>

      <section className="readout-actions">
        <span className="readout-label">Schedule</span>
        <div className="readout-schedule">
          <span className={'action-pill ' + (d.scheduleAction ?? 'none')}>{d.scheduleAction ? actionLabel(d.scheduleAction) : 'No schedule'}</span>
          {d.plan && (finite(d.plan.grid_charge_kw) && d.plan.grid_charge_kw > 0.01
            ? <span className="readout-sub">grid {fmt(d.plan.grid_charge_kw, 2)} kW</span>
            : finite(d.plan.battery_load_kw) && d.plan.battery_load_kw > 0.01 ? <span className="readout-sub">supply {fmt(d.plan.battery_load_kw, 2)} kW</span> : null)}
        </div>
        {d.scheduleSource === 'inverter'
            ? <div className="readout-sub">From inverter settings</div>
            : d.scheduleSource === 'plan' && !d.inverterConfirmed ? <div className="readout-sub">Inverter settings not yet confirmed</div> : null}
      </section>
      <footer>{pinned ? 'Pinned · click again or Esc to release · ← → to step' : 'Click to pin · ← → to step'}</footer>
    </motion.div>
  )
}
