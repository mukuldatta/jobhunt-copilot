import { useState, useEffect, useMemo, useCallback } from 'react'
import { X } from '@phosphor-icons/react'
import { tailorResume, generateCoverLetter, downloadTailoredPdf, getResume } from '../api'
import { wordDiff, changeSummary } from '../lib/diff'
import { useModal, backdropProps } from '../hooks/useModal'

export default function ResumeModal({ job, onClose, initialTab = 'resume' }) {
  const [tab, setTab] = useState(initialTab)
  const [resumeText, setResumeText] = useState('')
  const [coverText, setCoverText] = useState('')
  const [loading, setLoading] = useState(false)
  const [copied, setCopied] = useState(false)
  const [originalText, setOriginalText] = useState('')
  const [originalError, setOriginalError] = useState('')

  const dialogRef = useModal(onClose)

  const loadResume = useCallback(async () => {
    if (resumeText) return
    setLoading(true)
    try {
      const res = await tailorResume(job.job_id)
      setResumeText(res.data.tailored_resume)
    } catch (e) {
      setResumeText(e.userMessage || 'Could not generate a tailored resume.')
    } finally {
      setLoading(false)
    }
  }, [job.job_id, resumeText])

  const loadCoverLetter = useCallback(async () => {
    if (coverText) return
    setLoading(true)
    try {
      const res = await generateCoverLetter(job.job_id)
      setCoverText(res.data.cover_letter)
    } catch (e) {
      setCoverText(e.userMessage || 'Could not generate a cover letter.')
    } finally {
      setLoading(false)
    }
  }, [job.job_id, coverText])

  useEffect(() => {
    if (initialTab === 'cover') loadCoverLetter()
    else loadResume()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // The stored resume is only needed to diff against, so it is fetched when
  // the Changes tab is first opened rather than on every mount.
  useEffect(() => {
    if (tab !== 'changes' || originalText || originalError) return
    let cancelled = false
    getResume()
      .then((r) => !cancelled && setOriginalText(r.data?.parsed_text || ''))
      .catch((e) => !cancelled && setOriginalError(e.userMessage || 'Could not load your stored resume.'))
    return () => {
      cancelled = true
    }
  }, [tab, originalText, originalError])

  function handleTabChange(t) {
    setTab(t)
    if (t === 'cover') loadCoverLetter()
    else loadResume()
  }

  // wordDiff builds a full LCS table — O(n·m) and several megabytes for a
  // real resume. Without the memo it reran on every unrelated render,
  // including the two-second "Copied" toggle below.
  const diff = useMemo(
    () => (tab === 'changes' && resumeText && originalText ? wordDiff(originalText, resumeText) : null),
    [tab, resumeText, originalText]
  )
  const summary = useMemo(() => (diff ? changeSummary(diff) : null), [diff])

  // The Changes tab is a view OF the tailored resume, so it copies and
  // downloads that. Copy used to fall through to the cover letter here.
  const copyText = tab === 'cover' ? coverText : resumeText
  const showsResume = tab !== 'cover'

  function copy() {
    navigator.clipboard.writeText(copyText)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      {...backdropProps(onClose)}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="resume-modal-title"
        tabIndex={-1}
        className="flex max-h-[80vh] w-full max-w-2xl flex-col rounded border border-line bg-bg animate-viewIn focus:outline-none"
      >
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <div className="min-w-0">
            <h2 id="resume-modal-title" className="truncate text-lg">{job.title}</h2>
            <p className="text-xs+ text-neutral-600">{job.company}</p>
          </div>
          <button onClick={onClose} aria-label="Close" className="btn btn-neutral btn-icon">
            <X size={14} />
          </button>
        </div>

        <div className="flex gap-1.5 px-6 pt-4">
          {[
            ['resume', 'Tailored resume'],
            ['changes', 'Changes'],
            ['cover', 'Cover letter'],
          ].map(([key, label]) => (
            <button
              key={key}
              onClick={() => handleTabChange(key)}
              aria-pressed={tab === key}
              className={`chip ${tab === key ? 'chip-on' : 'chip-off'}`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-auto p-6">
          {loading ? (
            <div className="flex flex-col gap-2">
              {Array.from({ length: 10 }).map((_, i) => (
                <div key={i} className="skeleton h-3" style={{ width: `${60 + ((i * 13) % 40)}%` }} />
              ))}
            </div>
          ) : tab === 'changes' ? (
            !diff ? (
              <p className="text-base text-neutral-600">
                {originalError || 'No stored resume to compare against — upload one in Setup.'}
              </p>
            ) : (
              <>
                <p className="mb-3 text-xs+ text-neutral-600">
                  <span className="text-accent-400">{summary.added} added</span> ·{' '}
                  <span className="text-neutral-500">{summary.removed} removed</span> ·{' '}
                  {summary.same} unchanged. Validation proves no invented skills or
                  quantities — it cannot judge overstated framing, so read the additions.
                </p>
                <pre className="whitespace-pre-wrap font-mono text-sm leading-relaxed text-neutral-600">
                  {diff.map((part, i) =>
                    part.type === 'add' ? (
                      <span key={i} className="rounded-sm bg-accent/25 text-text">
                        {part.text}
                      </span>
                    ) : part.type === 'del' ? (
                      <span key={i} className="text-neutral-700 line-through">
                        {part.text}
                      </span>
                    ) : (
                      <span key={i}>{part.text}</span>
                    )
                  )}
                </pre>
              </>
            )
          ) : (
            <pre className="whitespace-pre-wrap font-mono text-sm leading-relaxed text-text">
              {tab === 'resume' ? resumeText : coverText}
            </pre>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-6 py-4">
          {showsResume && (
            <button onClick={() => downloadTailoredPdf(job.job_id)} className="btn btn-neutral">
              Download PDF
            </button>
          )}
          <button onClick={copy} disabled={!copyText} className="btn btn-accent">
            {copied ? 'Copied' : `Copy ${showsResume ? 'resume' : 'letter'}`}
          </button>
        </div>
      </div>
    </div>
  )
}
