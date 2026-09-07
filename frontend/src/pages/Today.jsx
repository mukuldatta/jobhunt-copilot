import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  HandWaving,
  Question,
  SignIn,
  WarningCircle,
  ArrowsClockwise,
  Play,
  CircleNotch,
  ArrowRight,
} from '@phosphor-icons/react'
import {
  getJobs,
  getPendingQuestions,
  getAuthStatus,
  triggerScrape,
  runAutoApply,
  platformLogin,
  errorMessage,
} from '../api'
import AgentLog from '../components/AgentLog'
import { useToast } from '../components/Toast'
import { useAgent } from '../hooks/useAgent'
import { useReducedMotion, useReveal, stagger } from '../hooks/useMotion'
import { clockTime, scoreFill, scoreText, shortLocation, weekday } from '../lib/format'

const PLATFORM_LABELS = { naukri: 'Naukri', linkedin: 'LinkedIn', indeed: 'Indeed' }

function StatCell({ label, value, accent }) {
  return (
    <div className="flex-1 border-l border-line px-[18px] py-3.5 first:border-l-0">
      <div className="section-label">{label}</div>
      <div className={`mt-0.5 text-2xl ${accent ? 'text-accent-400' : ''}`}>{value ?? '—'}</div>
    </div>
  )
}

/** One thing only you can do. Hovering nudges the row rather than filling it. */
function NeedsRow({ Icon, title, detail, action, accent, onAction }) {
  return (
    <div
      className="group flex items-center gap-3 border-t border-line-soft py-2.5
                 pl-0 transition-[padding-left] duration-200 ease-linear hover:pl-1.5"
    >
      <Icon size={15} className="w-4 flex-none text-neutral-500" />
      <div className="min-w-0 flex-1">
        <div className="text-base+">{title}</div>
        {detail && <div className="truncate text-xs+ text-neutral-600">{detail}</div>}
      </div>
      <button onClick={onAction} className={`btn btn-sm ${accent ? 'btn-accent' : 'btn-neutral'}`}>
        {action}
      </button>
    </div>
  )
}

export default function Today() {
  const navigate = useNavigate()
  const { agent, stats, running, offline, refreshAgent, refreshStats } = useAgent()
  const reduced = useReducedMotion()
  const revealRef = useReveal(reduced)

  const [questions, setQuestions] = useState([])
  const [platforms, setPlatforms] = useState([])
  const [topJobs, setTopJobs] = useState([])
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const notify = useToast()

  // Sign-in finishes in a browser window outside this app, so we re-check
  // twice while that is likely happening. Tracked so navigating away cancels
  // them rather than calling setState on an unmounted screen.
  const pending = useRef([])
  useEffect(() => () => pending.current.forEach(clearTimeout), [])

  const load = useCallback(async () => {
    const [q, a, j] = await Promise.allSettled([
      getPendingQuestions(),
      getAuthStatus(),
      getJobs({ limit: 3, status: 'new', sort_by: 'score_desc' }),
    ])
    if (q.status === 'fulfilled') setQuestions(q.value.data.questions || [])
    if (a.status === 'fulfilled') setPlatforms(a.value.data.platforms || [])
    if (j.status === 'fulfilled') setTopJobs(j.value.data.jobs || [])
    if (q.status === 'rejected' && a.status === 'rejected' && j.status === 'rejected') {
      setError('Could not reach the backend — is it running on port 8000?')
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // The header's "n new jobs" is now one indexed count from /stats. It used to
  // fetch 200 job documents and filter them here to arrive at one integer.
  const newToday = stats?.new_last_24h ?? null

  async function handleScrape() {
    setBusy('scrape')
    setError(null)
    try {
      await triggerScrape({ max_jobs: 50 })
      await refreshAgent()
      // The scrape is what fills this list; without reloading, the screen that
      // prompted the action was the one place that never showed its result.
      await load()
      refreshStats()
    } catch (e) {
      notify.err(errorMessage(e, 'Could not start a scrape.'), { retry: handleScrape })
    } finally {
      setBusy(null)
    }
  }

  async function handleRun() {
    setBusy('run')
    setError(null)
    try {
      await runAutoApply({ force: true })
      await refreshAgent()
      refreshStats()
    } catch (e) {
      notify.err(errorMessage(e, 'Could not start the agent.'), { retry: handleRun })
    } finally {
      setBusy(null)
    }
  }

  async function handleLogin(platform, label) {
    try {
      await platformLogin(platform)
      notify.ok(
        `Sign in to ${label || PLATFORM_LABELS[platform] || platform} in the browser window that just opened.`
      )
      pending.current.push(setTimeout(load, 8000), setTimeout(load, 30000))
    } catch (e) {
      notify.err(errorMessage(e, `Could not start the ${platform} sign-in.`))
    }
  }

  const loggedOut = platforms.filter((p) => !p.logged_in)
  const paused = agent.human_required

  const needs = []
  if (questions.length > 0) {
    needs.push({
      key: 'questions',
      Icon: Question,
      title: `${questions.length} screening question${questions.length > 1 ? 's' : ''} the agent could not answer`,
      detail: `"${questions[0].question}"${
        questions[0].times_seen > 1 ? ` · seen ${questions[0].times_seen}×` : ''
      }`,
      action: 'Answer',
      accent: true,
      onAction: () => navigate('/setup/answers'),
    })
  }
  loggedOut.forEach((p) => {
    needs.push({
      key: `auth-${p.platform}`,
      Icon: SignIn,
      title: `${p.label || PLATFORM_LABELS[p.platform] || p.platform} session expired`,
      detail: 'a browser window opens — sign in once',
      action: 'Sign in',
      onAction: () => handleLogin(p.platform, p.label),
    })
  })
  if (paused) {
    needs.push({
      key: 'captcha',
      Icon: WarningCircle,
      title: '1 application paused on a CAPTCHA',
      detail: `${paused.job_title || paused.reason} · waiting ${Math.round(
        (paused.waiting_seconds || 0) / 60
      )}m`,
      action: 'Open window',
      // Information, not a failure — it went through the error banner before.
      onAction: () =>
        notify.ok('Bring the open browser window to the front and clear the challenge.'),
    })
  }

  const lastScrape = clockTime(stats?.last_scraped)

  return (
    <div className={`h-full overflow-y-auto px-8 pb-10 pt-7 ${reduced ? '' : 'animate-viewIn'}`}>
      <div className="mb-[22px] flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl tracking-[-0.02em]">{weekday()}</h1>
          <p className="mt-1 text-base text-neutral-500">
            {lastScrape ? `Last scrape ${lastScrape}` : 'No scrape yet'}
            {newToday != null && ` · ${newToday} new jobs`}
            {/* Each band is guarded: one missing field rendered "NaN scored". */}
            {stats &&
              ` · ${(stats.low_match || 0) + (stats.medium_match || 0) + (stats.high_match || 0)} scored`}
          </p>
        </div>
        <div className="flex flex-none gap-2">
          <button onClick={handleScrape} disabled={busy === 'scrape'} className="btn btn-neutral">
            <ArrowsClockwise
              size={14}
              className={busy === 'scrape' && !reduced ? 'animate-spin360' : ''}
            />
            Scrape
          </button>
          <button
            onClick={handleRun}
            disabled={busy === 'run' || running}
            className="btn btn-accent"
            style={running ? { background: 'var(--accent-wash)' } : undefined}
          >
            {running || busy === 'run' ? (
              <CircleNotch size={14} className={reduced ? '' : 'animate-spin360'} />
            ) : (
              <Play size={14} />
            )}
            {running ? 'Running' : 'Run agent'}
          </button>
        </div>
      </div>

      {(error || offline) && (
        <div className="mb-5 flex items-center justify-between rounded border border-line px-4 py-2.5 text-base text-accent-400">
          {error || 'Backend unreachable — showing the last state it reported.'}
          <button onClick={() => setError(null)} className="text-xs+ text-neutral-600 hover:text-text">
            Dismiss
          </button>
        </div>
      )}

      {needs.length > 0 && (
        // No fill: bg is the only background in Nocturne, and elevation is an
        // edge. This panel carried the app's one gradient card, in two colours
        // that are not in the palette. The accent edge does the emphasis.
        <div className="relative mb-6 overflow-hidden rounded border border-accent/40 px-[18px] py-4">
          {running && !reduced && (
            <div
              className="absolute left-0 top-0 h-px w-[30%] animate-sweep"
              style={{ background: 'linear-gradient(90deg,transparent,var(--accent),transparent)' }}
            />
          )}
          <div className="mb-3 flex flex-wrap items-center gap-2.5">
            <HandWaving size={15} className="text-accent-400" />
            <span className="text-base font-medium">
              Needs you — {needs.length} thing{needs.length > 1 ? 's' : ''}
            </span>
            <span className="text-xs+ text-neutral-600">nothing applies until these clear</span>
          </div>
          <div className="flex flex-col">
            {needs.map((n) => (
              <NeedsRow key={n.key} {...n} />
            ))}
          </div>
        </div>
      )}

      {/* Above the last-run summary deliberately: while a run is in flight this
          is the only thing on the screen that is changing, and afterwards it is
          the detail behind the counts below it. */}
      <AgentLog />

      {agent.last_run && (
        <div className="mb-6 rounded border border-line px-[18px] py-3.5">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="section-label">Last run</span>
            <span className="text-xs+ text-neutral-600">
              {clockTime(agent.last_run.finished_at) || 'recently'}
            </span>
            <span className="ml-auto flex flex-wrap gap-3 text-base">
              {Object.entries(agent.last_run.results || {}).length === 0 ? (
                <span className="text-neutral-600">nothing to apply to</span>
              ) : (
                Object.entries(agent.last_run.results).map(([outcome, n]) => (
                  <span key={outcome} className="text-neutral-500">
                    <span className={outcome === 'applied' ? 'text-accent-400' : 'text-text'}>
                      {n}
                    </span>{' '}
                    {outcome.replace(/_/g, ' ')}
                  </span>
                ))
              )}
            </span>
          </div>
          {(agent.last_run.log || []).some((l) => l.result === 'halted' || l.result === 'deferred') && (
            <p className="mt-2 text-xs+ text-neutral-600">
              {agent.last_run.log.find((l) => l.result === 'halted' || l.result === 'deferred')?.msg}
            </p>
          )}
        </div>
      )}

      <div className="mb-6 flex overflow-hidden rounded border border-line">
        <StatCell label="Tracked" value={stats?.total_jobs} />
        <StatCell label="Worth reviewing" value={stats?.high_match} accent />
        <StatCell label="Applied" value={stats?.applied} />
        <StatCell label="In conversation" value={stats?.interviews} />
      </div>

      <div className="mb-2.5 flex items-baseline justify-between">
        <h2 className="text-lg">New since yesterday</h2>
        {stats?.high_match > 0 && (
          <Link to="/review?min_score=70" className="text-sm text-accent hover:text-accent-300">
            Review all {stats.high_match} →
          </Link>
        )}
      </div>

      <div className="overflow-hidden rounded border border-line">
        {/* Pending is not the same as empty. topJobs starts [], so the first
            paint used to assert "nothing to review" before the request had
            even returned — the wrong message, not just a missing one. */}
        {loading ? (
          <div className="flex flex-col gap-3 px-4 py-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="skeleton h-3.5" style={{ width: `${55 + ((i * 17) % 35)}%` }} />
            ))}
          </div>
        ) : topJobs.length === 0 ? (
          <p className="px-4 py-6 text-base text-neutral-600">
            Nothing new to review. Run a scrape to fetch more.
          </p>
        ) : (
          topJobs.map((job, i) => {
            const score = job.match_score ?? 0
            return (
              <Link
                key={job.job_id}
                to={`/review/${job.job_id}`}
                ref={revealRef}
                style={stagger(i, { reduced })}
                className={`flex items-center gap-3.5 border-t border-line-soft px-4 py-3
                            transition-colors duration-180 first:border-t-0 hover:bg-text/[0.04] ${
                              reduced ? '' : 'animate-rowIn'
                            }`}
              >
                <div className={`w-[34px] flex-none text-md font-medium ${scoreText(score)}`}>
                  {job.match_score ?? '—'}
                </div>
                <div className="h-[3px] w-[52px] flex-none overflow-hidden rounded-bar bg-neutral-900">
                  <div
                    className="h-full"
                    style={{ width: `${score}%`, background: scoreFill(score / 100) }}
                  />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-base+">{job.title}</div>
                  <div className="truncate text-xs+ text-neutral-600">
                    {[job.company, shortLocation(job.location), job.source]
                      .filter(Boolean)
                      .join(' · ')}
                  </div>
                </div>
                {job.sponsorship_status && (
                  <span className="hidden flex-none rounded border border-line px-2 py-0.5 text-xs text-neutral-500 sm:inline">
                    {job.sponsorship_status === 'strong' || job.sponsorship_status === 'moderate'
                      ? 'sponsors'
                      : job.sponsorship_status}
                  </span>
                )}
                <ArrowRight size={14} className="flex-none text-neutral-600" />
              </Link>
            )
          })
        )}
      </div>
    </div>
  )
}
