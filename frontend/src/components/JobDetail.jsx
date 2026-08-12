import { useEffect, useState } from 'react'
import {
  PaperPlaneTilt,
  ChatCircle,
  Archive,
  ArrowSquareOut,
  CheckCircle,
  CircleDashed,
  CircleNotch,
  Check,
  X,
} from '@phosphor-icons/react'
import ResumeModal from './ResumeModal'
import OutreachModal from './OutreachModal'
import { autoApply, setJobStatus, markApplied } from '../api'
import { useAgent } from '../hooks/useAgent'
import { useReducedMotion } from '../hooks/useMotion'
import { agoLabel, scoreFill } from '../lib/format'

// The scoring rubric, and the maximum each category can contribute.
const CATEGORIES = [
  ['Skills match', 'skills_score', 40],
  ['Experience', 'experience_score', 30],
  ['Domain relevance', 'domain_score', 20],
  ['Location', 'location_score', 10],
]

// How the agent submits, per source. Named here because "what it will do"
// should be legible before it runs, not after.
const APPLY_MODE = {
  linkedin: 'Easy Apply on LinkedIn — pauses if a CAPTCHA appears',
  naukri: 'Naukri application form — pauses if a CAPTCHA appears',
  indeed: 'Indeed application form — pauses if a CAPTCHA appears',
  dice: 'Dice application form — pauses if a CAPTCHA appears',
}

function Section({ label, children }) {
  return (
    <div>
      <div className="section-label mb-2.5">{label}</div>
      {children}
    </div>
  )
}

export default function JobDetail({ job, onStatusChange, onClose, overlay }) {
  const [resumeOpen, setResumeOpen] = useState(false)
  const [resumeTab, setResumeTab] = useState('resume')
  const [outreachOpen, setOutreachOpen] = useState(false)
  const [applying, setApplying] = useState(false)
  const [marking, setMarking] = useState(false)
  const [notice, setNotice] = useState(null)
  // Score bars grow from zero when a job opens, so the shape of a match is
  // something you watch resolve rather than something already on screen.
  const [barsIn, setBarsIn] = useState(false)
  const reduced = useReducedMotion()

  useEffect(() => {
    setNotice(null)
    setBarsIn(reduced)
    if (reduced) return undefined
    const t = requestAnimationFrame(() => setBarsIn(true))
    return () => cancelAnimationFrame(t)
  }, [job.job_id, reduced])

  const { applyDisabled } = useAgent()
  const breakdown = job.score_breakdown || {}
  const gaps = (job.gap_analysis || []).map((g) => g.replace(/^missing:\s*/i, ''))
  const applied = job.status === 'applied'

  // Two independent reasons the agent can't submit this one: the board refuses
  // automated sessions, or this specific posting hands off to the employer.
  const boardReason = applyDisabled[job.source]
  const isExternal = job.apply_type === 'external'
  const canAutoApply = !boardReason && !isExternal
  const handoffReason =
    boardReason ||
    (isExternal ? 'This posting applies on the employer’s own site.' : null)
  const questionCount = job.screening_questions_answered

  async function handleApply() {
    setApplying(true)
    setNotice('Starting — a browser window may open on this machine.')
    try {
      await autoApply(job.job_id)
      setNotice('Applying in the background. Anything it cannot answer comes back to you on Today.')
      onStatusChange?.(job.job_id, 'applied')
    } catch (e) {
      setNotice(e.response?.data?.detail || 'Could not start the apply run.')
    } finally {
      setApplying(false)
    }
  }

  async function handleMarkApplied() {
    setMarking(true)
    try {
      await markApplied(job.job_id)
      onStatusChange?.(job.job_id, 'applied')
      setNotice('Recorded — it will show up in Pipeline.')
    } catch (e) {
      setNotice(e.response?.data?.detail || 'Could not record that application.')
    } finally {
      setMarking(false)
    }
  }

  async function handleSkip() {
    try {
      await setJobStatus(job.job_id, 'skipped')
      onStatusChange?.(job.job_id, 'skipped')
    } catch (e) {
      setNotice(e.response?.data?.detail || 'Could not skip this job.')
    }
  }

  return (
    <div
      key={job.job_id}
      className={`flex h-full min-w-0 flex-1 flex-col overflow-y-auto ${
        reduced ? '' : 'animate-panelIn'
      }`}
    >
      <div className="border-b border-line px-7 pb-[18px] pt-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-xl tracking-[-0.015em]">{job.title}</h2>
            <p className="mt-[5px] text-base text-neutral-500">
              {[job.company, job.location].filter(Boolean).join(' · ')} · posted{' '}
              {agoLabel(job.posted_at || job.scraped_at)} via {job.source}
            </p>
          </div>
          <div className="flex flex-none items-start gap-3">
            <div className="text-right">
              <div className="text-4xl text-accent-400">{job.match_score ?? '—'}</div>
              <div className="text-xs uppercase tracking-[0.06em] text-neutral-600">match</div>
            </div>
            {overlay && (
              <button
                onClick={onClose}
                aria-label="Close job"
                className="btn btn-neutral btn-icon"
              >
                <X size={14} />
              </button>
            )}
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {/* The agent can only submit on some boards, and only for postings
              that don't hand off to the employer's own site. Offering "Apply
              with tailored resume" where it cannot run is a promise the panel
              can't keep — so the primary action becomes opening the posting,
              and Mark applied records what you did there. */}
          {canAutoApply ? (
            <button
              data-apply-button
              onClick={handleApply}
              disabled={applying || applied}
              className="btn btn-accent"
            >
              {applying ? (
                <CircleNotch size={14} className={reduced ? '' : 'animate-spin360'} />
              ) : (
                <PaperPlaneTilt size={14} />
              )}
              {applied ? 'Applied' : 'Apply with tailored resume'}
            </button>
          ) : (
            <a
              href={job.url}
              target="_blank"
              rel="noreferrer"
              className="btn btn-accent"
            >
              <ArrowSquareOut size={14} />
              Open &amp; apply
            </a>
          )}
          <button
            onClick={handleMarkApplied}
            disabled={marking || applied}
            className={`btn ${canAutoApply ? 'btn-neutral' : 'btn-neutral'}`}
            title="Record an application you submitted yourself"
          >
            <Check size={14} />
            {applied ? 'Applied' : marking ? 'Recording' : 'Mark applied'}
          </button>
          <button onClick={() => setOutreachOpen(true)} className="btn btn-neutral">
            <ChatCircle size={14} />
            Outreach
          </button>
          <button
            data-skip-button
            onClick={handleSkip}
            disabled={job.status === 'skipped'}
            className="btn btn-neutral"
          >
            <Archive size={14} />
            {job.status === 'skipped' ? 'Skipped' : 'Skip'}
          </button>
          <a
            href={job.url}
            target="_blank"
            rel="noreferrer"
            title="Open the original posting"
            className="btn btn-neutral btn-icon ml-auto"
          >
            <ArrowSquareOut size={14} />
          </a>
        </div>

        {handoffReason && !applied && (
          <p className="mt-3 text-xs+ text-neutral-600">
            {handoffReason} The tailored resume and cover letter below are still yours to use —
            apply in the tab, then Mark applied.
          </p>
        )}
        {notice && <p className="mt-3 text-xs+ text-neutral-600">{notice}</p>}
      </div>

      <div className="flex flex-col gap-[22px] px-7 py-[22px]">
        <Section label={`Why ${job.match_score ?? '—'}`}>
          <div className="flex flex-col gap-2">
            {CATEGORIES.map(([label, key, max]) => {
              const points = breakdown[key] ?? 0
              const ratio = max ? points / max : 0
              return (
                <div key={key} className="flex items-center gap-3">
                  <span className="w-[140px] flex-none text-sm+ text-neutral-500">{label}</span>
                  <div className="h-1 flex-1 overflow-hidden rounded-bar bg-neutral-900">
                    <div
                      className="h-full transition-[width] duration-550 ease-soft"
                      style={{
                        width: barsIn ? `${Math.min(100, ratio * 100)}%` : '0%',
                        background: scoreFill(ratio),
                      }}
                    />
                  </div>
                  <span className="w-11 text-right text-sm text-neutral-500">
                    {points}/{max}
                  </span>
                </div>
              )
            })}
          </div>
          {job.skills_matched?.length > 0 && (
            <p className="mt-2.5 text-xs+ leading-relaxed text-neutral-600">
              Skills the posting names that your resume evidences:{' '}
              <span className="text-neutral-500">{job.skills_matched.join(', ')}</span>
            </p>
          )}
        </Section>

        {gaps.length > 0 && (
          <Section label="Gaps against your resume">
            <div className="flex flex-wrap gap-1.5">
              {gaps.map((gap, i) => (
                <span
                  key={i}
                  className="rounded border border-line px-2.5 py-[3px] text-sm text-neutral-500"
                >
                  {gap}
                </span>
              ))}
            </div>
          </Section>
        )}

        <Section label="What the agent will do">
          <div className="overflow-hidden rounded border border-line">
            <div className="flex items-center gap-2.5 px-3.5 py-2.5 text-sm+ text-neutral-500">
              <CheckCircle size={14} className="flex-none text-accent" />
              Tailor resume — validated, no fabricated skills
              <button
                onClick={() => {
                  setResumeTab('resume')
                  setResumeOpen(true)
                }}
                className="ml-auto text-xs+ text-accent hover:text-accent-300"
              >
                Preview
              </button>
            </div>
            <div className="flex items-center gap-2.5 border-t border-line-soft px-3.5 py-2.5 text-sm+ text-neutral-500">
              <CheckCircle size={14} className="flex-none text-accent" />
              Write cover letter
              <button
                onClick={() => {
                  setResumeTab('cover')
                  setResumeOpen(true)
                }}
                className="ml-auto text-xs+ text-accent hover:text-accent-300"
              >
                Preview
              </button>
            </div>
            <div className="flex items-center gap-2.5 border-t border-line-soft px-3.5 py-2.5 text-sm+ text-neutral-500">
              <CheckCircle size={14} className="flex-none text-accent" />
              {questionCount != null
                ? `Answer ${questionCount} screening questions from your profile`
                : 'Answer screening questions from your profile'}
            </div>
            <div className="flex items-center gap-2.5 border-t border-line-soft px-3.5 py-2.5 text-sm+ text-neutral-600">
              <CircleDashed size={14} className="flex-none" />
              {APPLY_MODE[job.source] || 'External site — handed back to you'}
            </div>
          </div>
        </Section>

        {job.description && (
          <Section label="Description">
            <p className="whitespace-pre-line text-base leading-[1.6] text-neutral-500">
              {job.description}
            </p>
          </Section>
        )}
      </div>

      {resumeOpen && (
        <ResumeModal job={job} initialTab={resumeTab} onClose={() => setResumeOpen(false)} />
      )}
      {outreachOpen && <OutreachModal job={job} onClose={() => setOutreachOpen(false)} />}
    </div>
  )
}
