// Render the real app against the synthetic demo state (scripts/build-demo.py)
// through the demo transport: no network, real derivations and components.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import App from './App'

const ROOT = resolve(__dirname, '../..')
let demo: any

beforeAll(() => {
  demo = JSON.parse(execFileSync('python3', [resolve(ROOT, 'scripts/build-demo.py'), '--state'], { cwd: ROOT, encoding: 'utf8' }))
})
afterEach(() => { cleanup(); delete window.__ENERGY_DEMO__ })

function serve(state: any, options: { fail?: () => boolean } = {}) {
  const calls: string[] = []
  window.__ENERGY_DEMO__ = {
    async fetch(path, init) {
      calls.push(path)
      if (options.fail?.()) throw Object.assign(new Error('timeout'), { name: 'AbortError' })
      if (path === '/api/control') state.control.enabled = JSON.parse(String(init!.body)).enabled
      return { ok: true, json: async () => structuredClone(state) }
    },
  }
  return calls
}

const flush = () => act(async () => { await new Promise(r => setTimeout(r, 0)) })

describe('dashboard app', () => {
  it('renders the board, day controls and interval inspection from demo state', async () => {
    const state = structuredClone(demo)
    serve(state)
    render(<App />)
    await flush()
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Running normally')
    expect(document.getElementById('acceptanceState')!.textContent).toBe('Settings confirmed')
    expect(document.getElementById('controlDescription')!.textContent).toBe('Checks every 5 min')
    expect(document.getElementById('solarToday')!.textContent).toContain('kWh generated today')
    expect(document.getElementById('batteryChargedToday')!.textContent).toContain('solar + grid')
    expect(document.getElementById('solarUnit')!.textContent).toBe('W')
    expect(document.getElementById('connection')!.textContent).toContain('Simulated ESS')
    expect(document.title).toMatch(/^\d+% · Running normally/)

    // Keyboard inspection of a quarter hour shows the large readout.
    const timeline = document.querySelector('.timeline') as HTMLElement
    fireEvent.keyDown(timeline, { key: 'Home' })
    for (let i = 0; i < 40; i++) fireEvent.keyDown(timeline, { key: 'ArrowRight' })
    const readout = document.getElementById('tooltip')!
    expect(readout.textContent).toContain('10:00')
    expect(readout.textContent).toContain('Expected')
    expect(readout.textContent).toContain('Measured')
    expect(readout.textContent).toContain('Difference')
    fireEvent.keyDown(timeline, { key: 'Escape' })
    await flush()

    // Tomorrow becomes selectable once prices for it are published.
    const tomorrow = document.querySelector('[data-day="tomorrow"]') as HTMLButtonElement
    expect(tomorrow.disabled).toBe(false)
    fireEvent.click(tomorrow)
    expect(document.getElementById('planTitle')!.textContent).toBe('Tomorrow’s plan')
  })

  it('keeps unavailable days visible but disabled', async () => {
    const state = structuredClone(demo)
    state.price_intervals = state.price_intervals.slice(0, 96)
    serve(state)
    render(<App />)
    await flush()
    const tomorrow = document.querySelector('[data-day="tomorrow"]') as HTMLButtonElement
    expect(tomorrow).not.toBeNull()
    expect(tomorrow.disabled).toBe(true)
    fireEvent.click(tomorrow)
    expect(document.getElementById('planTitle')!.textContent).toBe('Today’s plan')
  })

  it('toggles automatic control through the control endpoint', async () => {
    const state = structuredClone(demo)
    const calls = serve(state)
    render(<App />)
    await flush()
    await act(async () => { fireEvent.click(document.getElementById('toggle')!) })
    await flush()
    expect(calls).toContain('/api/control')
    expect(state.control.enabled).toBe(false)
    expect(document.getElementById('acceptanceState')!.textContent).toBe('Automatic updates off')
    expect(document.getElementById('toggle')!.getAttribute('aria-checked')).toBe('false')
  })

  it('reports a heartbeat timeout and clears it on reconnect', async () => {
    const state = structuredClone(demo)
    let fail = true
    serve(state, { fail: () => fail })
    render(<App />)
    await flush()
    expect(document.getElementById('connectionError')!.textContent).toMatch(/timed out/)
    fail = false
    await act(async () => { await new Promise(r => setTimeout(r, 5200)) })
    expect(document.getElementById('connectionError')).toBeNull()
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Running normally')
  }, 10000)
})
