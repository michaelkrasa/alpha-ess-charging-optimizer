import { useCallback, useEffect, useRef, useState } from 'react'
import type { DashboardState } from './types'

interface Transport {
  (path: string, init?: RequestInit): Promise<{ ok: boolean; status?: number; json(): Promise<any> }>
}

declare global {
  interface Window {
    /** Set only by scripts/build-demo.py: synthetic state, no network. */
    __ENERGY_DEMO__?: { fetch: Transport; label?: string }
  }
}

export const isDemo = () => typeof window !== 'undefined' && !!window.__ENERGY_DEMO__
const transport: Transport = (path, init) => (window.__ENERGY_DEMO__ ? window.__ENERGY_DEMO__.fetch(path, init) : window.fetch(path, init))

export const HEARTBEAT_MS = 5000

export interface Feed {
  data: DashboardState | null
  connectionError: string
  commandError: string
  refreshedAt: Date | null
  busy: boolean
  refresh: () => Promise<void>
  setEnabled: (enabled: boolean) => Promise<void>
}

/** Poll /api/state every 5 s; a failed heartbeat keeps the last-known values visible. */
export function useDashboard(): Feed {
  const [data, setData] = useState<DashboardState | null>(null)
  const [connectionError, setConnectionError] = useState('')
  const [commandError, setCommandError] = useState('')
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null)
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)

  const refresh = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    const abort = new AbortController(), deadline = setTimeout(() => abort.abort(), 4000)
    try {
      const response = await transport('/api/state', { cache: 'no-store', signal: abort.signal })
      if (!response.ok) throw Error(response.status === 401 ? 'Sign-in expired. Reload to sign in.' : 'Dashboard state unavailable.')
      setData(await response.json())
      setRefreshedAt(new Date())
      setConnectionError('')
    } catch (error) {
      const e = error as Error
      setConnectionError(e.name === 'AbortError' ? 'Dashboard heartbeat timed out. Retrying…' : e.message)
    } finally {
      clearTimeout(deadline)
      inFlight.current = false
    }
  }, [])

  const setEnabled = useCallback(async (enabled: boolean) => {
    setBusy(true); setCommandError('')
    try {
      const response = await transport('/api/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Control-Request': 'dashboard' },
        body: JSON.stringify({ enabled }),
      })
      const result = await response.json()
      if (!response.ok) throw Error(result.detail || 'Command failed')
      await refresh()
    } catch (error) {
      setCommandError((error as Error).message)
    } finally {
      setBusy(false)
    }
  }, [refresh])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, HEARTBEAT_MS)
    return () => clearInterval(timer)
  }, [refresh])

  return { data, connectionError, commandError, refreshedAt, busy, refresh, setEnabled }
}
