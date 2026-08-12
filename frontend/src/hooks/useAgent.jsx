import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { getAgentState, getStats, getPlatforms } from '../api'

const AgentContext = createContext(null)

// The shell's status line drives three animations, so the run state is polled
// on its own short interval; the counts beside the nav change far more slowly.
const STATE_POLL_MS = 5000
const STATS_POLL_MS = 60000

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

  const refreshAgent = useCallback(async () => {
    try {
      const res = await getAgentState()
      setAgent(res.data)
      setOffline(false)
    } catch {
      setOffline(true)
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
    refreshAgent()
    refreshStats()
    const a = setInterval(refreshAgent, STATE_POLL_MS)
    const s = setInterval(refreshStats, STATS_POLL_MS)
    return () => {
      clearInterval(a)
      clearInterval(s)
    }
  }, [refreshAgent, refreshStats])

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
