// Shape of GET /api/state (src/manager/web.py). Fields are optional where the
// server may legitimately return null before the first controller cycle.

export type Action = 'charge' | 'hold' | 'solar' | 'self_consume' | (string & {})

export interface Telemetry {
  timestamp: string
  soc_percent: number
  capacity_kwh?: number
  load_kw: number
  solar_kw: number
  grid_kw?: number
  battery_kw: number
  inverter?: string
}

export interface PriceInterval {
  timestamp: string
  spot_eur_mwh: number | null
  import_eur_kwh: number | null
  export_eur_kwh?: number | null
  tariff?: 'VT' | 'NT'
  tariff_verified?: boolean
  elapsed?: boolean
}

export interface Decision extends PriceInterval {
  action: Action
  grid_charge_kw?: number
  battery_load_kw?: number
  solar_charge_kw?: number
  soc_percent?: number
  load_kw?: number
  solar_kw?: number
  reason?: string
}

export interface ChargeConfig {
  gridCharge?: boolean | number
  [field: string]: unknown
}

export interface DischargeConfig {
  ctrDis?: boolean | number
  [field: string]: unknown
}

export interface Schedule {
  charge?: ChargeConfig
  discharge?: DischargeConfig
  checked_at?: string
  source?: string
  readback_pending?: boolean
}

export interface Plan {
  created_at: string
  valid_until?: string
  mode?: string
  planner?: string
  charge?: ChargeConfig
  discharge?: DischargeConfig
  decisions?: Decision[]
  outlook_decisions?: Decision[]
  notes?: string[]
}

export interface Replay {
  day: string
  observed: Telemetry[]
  decisions?: Decision[]
  outlook_decisions?: Decision[]
  notes?: string[]
}

export interface SavingsDay {
  saved_eur: number
  cost_eur: number
  cost_without_battery_eur: number
  quarters: number
  final?: boolean
}

export interface SocRow {
  timestamp: string
  expected_soc: number | null
  expected_solar_kw?: number | null
  actual_soc: number | null
  delta_pp: number | null
}

export interface DashboardState {
  now: string
  release?: { version?: string; deployed_at?: string | null }
  price_intervals?: PriceInterval[]
  plan_insights?: { day: string; text: string; basis?: string }[]
  control: { enabled: boolean; restore_pending?: boolean; revision?: number }
  health?: { status?: string; message?: string; timestamp?: string } | null
  telemetry?: Telemetry | null
  device_connection?: {
    status?: string
    last_success_at?: string
    message?: string
    transport?: string
  } | null
  energy_day?: { start?: string; end?: string; rows?: Telemetry[]; ticks?: { timestamp: string; label: string }[] } | null
  daily_energy?: {
    day?: string
    checked_at?: string
    solar_generated_kwh?: number
    battery_charged_kwh?: number
  } | null
  savings?: {
    since?: string
    total_eur: number
    days_counted: number
    today?: SavingsDay | null
    yesterday?: SavingsDay | null
  } | null
  soc_tracking?: {
    actual?: { timestamp: string; actual_soc: number }[]
    rows?: SocRow[]
    latest?: SocRow & { delta_pp: number } | null
  } | null
  plan?: Plan | null
  replay?: Replay | null
  actuation?: {
    status?: string
    action?: string
    message?: string
    next_write_at?: string
    timestamp?: string
  } | null
  device_schedule?: Schedule | null
  applied_schedule?: Schedule | null
  planner_comparison?: {
    status?: string
    timestamp?: string
    message?: string
    solve_seconds?: number
    candidate_error?: string
    emhass?: { next_action?: Action }
  } | null
  standard_planner?: string
  load_profile?: { inferred_ev_samples?: number } | null
  readiness: string[]
  reserve_percent?: number
  control_mode?: string
  control_check_minutes?: number | null
  write_interval_seconds?: number
  daily_write?: { attempted_at?: string } | null
  history?: { telemetry?: Telemetry }[]
}

export type Tone = 'good' | 'wait' | 'attention' | 'off'
