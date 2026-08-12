import { useCallback, useEffect, useState } from 'react'
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
} from '../api'
import { useAgent } from '../hooks/useAgent'
import { useReducedMotion, useReveal, stagger } from '../hooks/useMotion'
import { clockTime, scoreFill, scoreText, shortLocation, weekday } from '../lib/format'

const PLATFORM_LABELS = { naukri: 'Naukri', linkedin: 'LinkedIn', indeed: 'Indeed', dice: 'Dice' }

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
  const [newToday, setNewToday] = useState(null)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)

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
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // How many arrived since this time yesterday — the "n new jobs" in the header.
  useEffect(() => {
    const since = new Date(Date.now() - 24 * 3600 * 1000)
    getJobs({ limit: 200, sort_by: 'date_desc' })
      .then((r) =>
        setNewToday(
          (r.data.jobs || []).filter((j) => new Date(j.scraped_at || j.posted_at) >= since).length
        )
      )
      .catch(() => setNewToday(null))
  }, [])

  async function handleScrape() {
    setBusy('scrape')
    setError(null)
    try {
      await triggerScrape({ max_jobs: 50 })
      await refreshAgent()
    } catch (e) {
      setError(e.response?.data?.detail || 'Could not start a scrape.')
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
      setError(e.response?.data?.detail || 'Could not start the agent.')
    } finally {
      setBusy(null)
    }
  }

  async function handleLogin(platform) {
    try {
      await platformLogin(platform)
      setTimeout(load, 8000)
      setTimeout(load, 30000)
    } catch (e) {
      setError(e.response?.data?.detail || `Could not start the ${platform} sign-in.`)
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
      title: `${PLATFORM_LABELS[p.platform] || p.platform} session expired`,
      detail: 'a browser window opens — sign in once',
      action: 'Sign in',
      onAction: () => handleLogin(p.platform),
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
      onAction: () => setError('Bring the open browser window to the front and clear the challenge.'),
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
            {stats && ` · ${stats.low_match + stats.medium_match + stats.high_match} scored`}
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
            style={running ? { background: 'rgba(145,132,217,.12)' } : undefined}
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
        <div
          className="relative mb-6 overflow-hidden rounded border border-line px-[18px] py-4"
          style={{ background: 'linear-gradient(180deg,rgba(35,37,50,.9),rgba(22,24,38,.9))' }}
        >
          {running && !reduced && (
            <div
              className="absolute left-0 top-0 h-px w-[30%] animate-sweep"
              style={{ background: 'linear-gradient(90deg,transparent,#9184d9,transparent)' }}
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
        {topJobs.length === 0 ? (
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
