import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { getAgentState, getStats, getPlatforms } from '../api'

const AgentContext = createContext(null)

// The shell's status line drives three animations, so the run state is polled
// on its own short interval; the counts beside the nav change far more slowly.
const STATE_POLL_MS = 5000
const STATS_POLL_MS = 60000
// A backend that is down does not recover faster for being asked twelve times
// a minute. Back off to this while offline, and drop back to normal on the
// first success.
const OFFLINE_POLL_MS = 30000

export function AgentProvider({ children }) {
  const [agent, setAgent] = useState({
    state: 'idle',
    phase: null,
    next_run_at: null,
    applied_today: 0,
    daily_cap: 20,
    human_required: null,
  })
  const [stats, setStats] = useState(null)
  const [offline, setOffline] = useState(false)
  // Which boards the agent will submit to. Policy lives on the server; the UI
  // reads it so the two cannot drift.
  const [applyDisabled, setApplyDisabled] = useState({})

  // setInterval fires on a fixed clock regardless of whether the previous
  // request finished, so a slow backend used to stack overlapping polls.
  const inFlight = useRef(false)

  const refreshAgent = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      const res = await getAgentState()
      setAgent(res.data)
      setOffline(false)
    } catch {
      setOffline(true)
    } finally {
      inFlight.current = false
    }
  }, [])

  const refreshStats = useCallback(async () => {
    try {
      const res = await getStats()
      setStats(res.data)
    } catch {
      /* the agent poll already reports the backend being unreachable */
    }
  }, [])

  useEffect(() => {
    // Fetched once — this changes only when the code does.
    getPlatforms()
      .then((r) => setApplyDisabled(r.data.apply_disabled || {}))
      .catch(() => {})
  }, [])

  useEffect(() => {
    let stateTimer = null
    let statsTimer = null

    // A hidden tab has nothing to render, so polling it is pure load on a
    // backend that may be driving a real browser session at the time.
    const hidden = () => document.visibilityState === 'hidden'

    const start = () => {
      stop()
      if (hidden()) return
      refreshAgent()
      refreshStats()
      stateTimer = setInterval(refreshAgent, offline ? OFFLINE_POLL_MS : STATE_POLL_MS)
      statsTimer = setInterval(refreshStats, offline ? OFFLINE_POLL_MS : STATS_POLL_MS)
    }

    const stop = () => {
      if (stateTimer) clearInterval(stateTimer)
      if (statsTimer) clearInterval(statsTimer)
      stateTimer = statsTimer = null
    }

    start()
    // Re-entering the tab should show current state immediately, not after a
    // full interval of staleness.
    document.addEventListener('visibilitychange', start)
    return () => {
      document.removeEventListener('visibilitychange', start)
      stop()
    }
  }, [refreshAgent, refreshStats, offline])

  const running = agent.state === 'running' || agent.state === 'paused'

  return (
    <AgentContext.Provider
      value={{ agent, stats, running, offline, applyDisabled, refreshAgent, refreshStats }}
    >
      {children}
    </AgentContext.Provider>
  )
}

export function useAgent() {
  const ctx = useContext(AgentContext)
  if (!ctx) throw new Error('useAgent must be used inside <AgentProvider>')
  return ctx
}
