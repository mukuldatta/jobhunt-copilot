import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { getApplications, updateApplicationStatus, errorMessage } from '../api'
import { useToast } from '../components/Toast'
import { useReducedMotion } from '../hooks/useMotion'
import { agoLabel, scoreText, titleCase } from '../lib/format'

// Seven backend statuses, five columns. "Closed" gathers everything that is
// over or has gone quiet, so the four live stages stay readable.
const COLUMNS = [
  { key: 'applied', label: 'Applied', statuses: ['applied'] },
  { key: 'recruiter_screen', label: 'Recruiter screen', statuses: ['recruiter_screen'] },
  { key: 'technical', label: 'Technical', statuses: ['technical'] },
  { key: 'final_round', label: 'Final round', statuses: ['final_round', 'offer'] },
  { key: 'rejected', label: 'Closed', statuses: ['rejected', 'saved'] },
]

const ALL_STATUSES = [
  'saved',
  'applied',
  'recruiter_screen',
  'technical',
  'final_round',
  'offer',
  'rejected',
]

const VISIBLE_PER_COLUMN = 6

function Card({ app, index, columnIndex, reduced, onDragStart }) {
  const live = app.status === 'final_round' || app.status === 'offer'
  const closed = app.status === 'rejected' || app.status === 'saved'

  return (
    <div
      draggable
      onDragStart={(e) => onDragStart(e, app)}
      style={
        reduced
          ? undefined
          : { animationDelay: `${columnIndex * 50 + Math.min(index, 6) * 40}ms` }
      }
      className={`cursor-grab rounded border px-3 py-[11px] transition-[transform,border-color]
                  duration-180 ease-soft hover:-translate-y-0.5 hover:border-text/40 active:cursor-grabbing
                  ${reduced ? '' : 'animate-cardIn'}
                  ${live ? 'border-accent bg-accent/[0.08]' : 'border-line'}
                  ${closed ? 'opacity-50' : ''}`}
    >
      {app.url ? (
        <a href={app.url} target="_blank" rel="noreferrer" className="text-sm+ leading-[1.35] text-text">
          {app.title || app.job_id}
        </a>
      ) : (
        <div className="text-sm+ leading-[1.35]">{app.title || app.job_id}</div>
      )}
      <div className="mt-[3px] text-xs text-neutral-600">
        {app.company || 'unknown company'}
        {app.status === 'rejected' && ' · rejected'}
      </div>
      <div className="mt-2 flex items-center justify-between">
        <span className="text-2xs text-neutral-600">{agoLabel(app.applied_at)}</span>
        {app.match_score != null && (
          <span className={`text-2xs ${scoreText(app.match_score)}`}>{app.match_score}</span>
        )}
      </div>
    </div>
  )
}

export default function Pipeline() {
  const reduced = useReducedMotion()
  const [apps, setApps] = useState([])
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState('board')
  const [error, setError] = useState(null)
  const [dragOver, setDragOver] = useState(null)

  const notify = useToast()

  // silent: a background refresh must not blank the board it is refreshing.
  const load = useCallback((silent = false) => {
    if (!silent) setLoading(true)
    return getApplications({ limit: 200 })
      .then((r) => {
        setApps(r.data.applications || [])
        setError(null)
      })
      .catch((e) => setError(errorMessage(e, 'Could not load applications.')))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // The agent applies to jobs while this page sits open, and nothing here ever
  // refetched — the board silently diverged from what had actually happened.
  // Refreshing on focus catches up without polling.
  useEffect(() => {
    const onFocus = () => document.visibilityState === 'visible' && load(true)
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onFocus)
    }
  }, [load])

  const grouped = useMemo(() => {
    const out = Object.fromEntries(COLUMNS.map((c) => [c.key, []]))
    apps.forEach((app) => {
      const col = COLUMNS.find((c) => c.statuses.includes(app.status)) || COLUMNS[4]
      out[col.key].push(app)
    })
    return out
  }, [apps])

  const live = apps.filter((a) =>
    ['recruiter_screen', 'technical', 'final_round', 'offer'].includes(a.status)
  ).length
  const replied = apps.filter((a) => a.status !== 'applied' && a.status !== 'saved').length
  const replyRate = apps.length ? Math.round((replied / apps.length) * 100) : 0

  // Optimistic move on drop; roll back if the PATCH fails.
  async function changeStatus(app, status) {
    if (app.status === status) return
    const previous = app.status
    setApps((prev) => prev.map((a) => (a.id === app.id ? { ...a, status } : a)))
    try {
      await updateApplicationStatus(app.id, { status })
    } catch (e) {
      setApps((prev) => prev.map((a) => (a.id === app.id ? { ...a, status: previous } : a)))
      notify.err(errorMessage(e, 'Could not update that application.'), {
        retry: () => changeStatus(app, status),
      })
    }
  }

  function onDragStart(e, app) {
    e.dataTransfer.setData('text/plain', app.id)
    e.dataTransfer.effectAllowed = 'move'
  }

  function onDrop(e, column) {
    e.preventDefault()
    setDragOver(null)
    const id = e.dataTransfer.getData('text/plain')
    const app = apps.find((a) => a.id === id)
    if (app) changeStatus(app, column.statuses[0])
  }

  return (
    <div className={`h-full overflow-y-auto px-8 pb-10 pt-7 ${reduced ? '' : 'animate-viewIn'}`}>
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl tracking-[-0.02em]">Pipeline</h1>
          <p className="mt-1 text-base text-neutral-500">
            {apps.length} application{apps.length === 1 ? '' : 's'} · {live} live conversation
            {live === 1 ? '' : 's'} · {replyRate}% reply rate
          </p>
        </div>
        <div className="flex flex-none gap-1.5">
          {['board', 'table'].map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`rounded border px-[11px] py-[5px] text-sm transition-colors duration-180 ${
                view === v ? 'border-accent text-accent-400' : 'border-line text-neutral-500'
              }`}
            >
              {titleCase(v)}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="mb-5 flex items-center justify-between rounded border border-line px-4 py-2.5 text-base text-accent-400">
          {error}
          <button onClick={() => setError(null)} className="text-xs+ text-neutral-600 hover:text-text">
            Dismiss
          </button>
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-2 items-start gap-3.5 lg:grid-cols-5">
          {COLUMNS.map((c) => (
            <div key={c.key}>
              <div className="skeleton mb-2.5 h-3 w-24" />
              <div className="flex flex-col gap-2">
                <div className="skeleton h-16" />
                <div className="skeleton h-16" />
              </div>
            </div>
          ))}
        </div>
      ) : apps.length === 0 ? (
        <div className="py-16 text-center text-neutral-600">
          <p className="text-lg text-text">No applications yet</p>
          <p className="mt-2 text-base">
            Apply to a job from <Link to="/review" className="text-accent">Review</Link> and it lands here.
          </p>
        </div>
      ) : view === 'board' ? (
        <div className="grid grid-cols-2 items-start gap-3.5 lg:grid-cols-5">
          {COLUMNS.map((column, columnIndex) => {
            const rows = grouped[column.key]
            const shown = rows.slice(0, VISIBLE_PER_COLUMN)
            const rest = rows.length - shown.length
            return (
              <div
                key={column.key}
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragOver(column.key)
                }}
                onDragLeave={() => setDragOver((k) => (k === column.key ? null : k))}
                onDrop={(e) => onDrop(e, column)}
                className={`rounded transition-colors duration-180 ${
                  dragOver === column.key ? 'bg-accent/[0.06]' : ''
                }`}
              >
                <div className="flex items-baseline gap-1.5 px-0.5 pb-2.5">
                  <span className="text-sm font-medium">{column.label}</span>
                  <span className="text-xs text-neutral-600">{rows.length}</span>
                </div>
                <div className="flex flex-col gap-2">
                  {shown.map((app, i) => (
                    <Card
                      key={app.id}
                      app={app}
                      index={i}
                      columnIndex={columnIndex}
                      reduced={reduced}
                      onDragStart={onDragStart}
                    />
                  ))}
                  {rest > 0 && (
                    <div className="rounded border border-dashed border-line px-3 py-2.5 text-center text-xs+ text-neutral-600">
                      {rest} more
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="overflow-hidden rounded border border-line">
          <table className="w-full">
            <thead>
              <tr className="border-b border-line text-left">
                {['Job', 'Company', 'Applied', 'Score', 'Status'].map((h) => (
                  <th key={h} className="section-label px-4 py-2.5 font-normal">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {apps.map((app) => (
                <tr
                  key={app.id}
                  className="border-b border-line-soft transition-colors duration-180 last:border-0 hover:bg-text/[0.04]"
                >
                  <td className="px-4 py-2.5 text-base">{app.title || app.job_id}</td>
                  <td className="px-4 py-2.5 text-base text-neutral-500">{app.company || '—'}</td>
                  <td className="px-4 py-2.5 text-xs+ text-neutral-600">{agoLabel(app.applied_at)}</td>
                  <td className={`px-4 py-2.5 text-base ${scoreText(app.match_score ?? 0)}`}>
                    {app.match_score ?? '—'}
                  </td>
                  <td className="px-4 py-2.5">
                    <select
                      value={app.status}
                      onChange={(e) => changeStatus(app, e.target.value)}
                      className="field-box max-w-[180px] cursor-pointer py-1 text-sm"
                    >
                      {ALL_STATUSES.map((s) => (
                        <option key={s} value={s} className="bg-bg">
                          {titleCase(s)}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
