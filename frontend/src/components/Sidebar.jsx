import { NavLink } from 'react-router-dom'
import {
  Crosshair,
  SunHorizon,
  ListMagnifyingGlass,
  FlowArrow,
  SlidersHorizontal,
} from '@phosphor-icons/react'
import { useAgent } from '../hooks/useAgent'
import { useReducedMotion } from '../hooks/useMotion'
import { clockTime } from '../lib/format'

const NAV = [
  { to: '/', label: 'Today', Icon: SunHorizon, end: true },
  { to: '/review', label: 'Review', Icon: ListMagnifyingGlass, count: 'review' },
  { to: '/pipeline', label: 'Pipeline', Icon: FlowArrow, count: 'pipeline' },
  { to: '/setup', label: 'Setup', Icon: SlidersHorizontal },
]

function statusLine(agent) {
  if (agent.state === 'paused') return 'Agent paused · waiting on you'
  if (agent.state === 'running') return `Agent running${agent.phase ? ` · ${agent.phase}` : ''}`
  const next = clockTime(agent.next_run_at)
  return next ? `Agent idle · next run ${next}` : 'Agent idle'
}

/**
 * The persistent 212px column. Below 900px it narrows to an icon rail — the
 * alternative shell explored as #1a in the design.
 */
export default function Sidebar() {
  const { agent, stats, running } = useAgent()
  const reduced = useReducedMotion()

  const counts = {
    review: stats?.high_match,
    pipeline: stats?.applied,
  }
  const cap = agent.daily_cap || 1
  const capPct = Math.min(100, Math.round(((agent.applied_today || 0) / cap) * 100))

  return (
    <aside
      className="w-rail lg:w-sidebar flex-none border-r border-line flex flex-col
                 px-2 pt-5 pb-3 lg:px-3"
    >
      <div className="flex items-center gap-2 px-2 mb-[26px]">
        <span className="w-[22px] h-[22px] rounded-md border border-accent flex items-center justify-center text-accent flex-none">
          <Crosshair size={13} />
        </span>
        <span className="hidden lg:inline text-md font-medium tracking-[-0.01em]">JobHunt</span>
      </div>

      <nav className="flex flex-col gap-0.5">
        {NAV.map(({ to, label, Icon, end, count }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            title={label}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded px-2.5 py-2 text-base+ transition-[background-color,color] duration-180 ease-linear ${
                isActive
                  ? 'bg-accent/[0.12] text-accent-400'
                  : 'text-neutral-500 hover:bg-text/[0.05]'
              }`
            }
          >
            {({ isActive }) => (
              <>
                <Icon size={16} className="flex-none" />
                <span className="hidden lg:inline">{label}</span>
                {counts[count] != null && (
                  <span
                    className={`hidden lg:inline ml-auto text-xs ${
                      isActive ? 'text-accent-400' : 'text-neutral-600'
                    }`}
                  >
                    {counts[count]}
                  </span>
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto border-t border-line pt-3 px-2.5 pb-1">
        <div className="flex items-center gap-2 text-xs+ text-neutral-500 mb-2">
          <span
            className={`w-1.5 h-1.5 rounded-full bg-accent flex-none ${
              running && !reduced ? 'animate-dotPulse' : ''
            }`}
            style={running && !reduced ? undefined : { boxShadow: '0 0 0 3px rgba(145,132,217,.18)' }}
          />
          <span className="hidden lg:inline">{statusLine(agent)}</span>
        </div>
        <div className="hidden lg:block text-xs+ text-neutral-600 leading-[1.5]">
          Applied {agent.applied_today ?? 0} of {agent.daily_cap ?? 0} today
          <div className="h-[3px] bg-neutral-900 rounded-bar mt-1.5 overflow-hidden">
            <div
              className="h-full bg-accent transition-[width] duration-800 ease-soft"
              style={{ width: `${capPct}%` }}
            />
          </div>
        </div>
      </div>
    </aside>
  )
}
