import { useState, useEffect } from 'react'
import { X } from '@phosphor-icons/react'
import { tailorResume, generateCoverLetter, downloadTailoredPdf } from '../api'

export default function ResumeModal({ job, onClose, initialTab = 'resume' }) {
  const [tab, setTab] = useState(initialTab)
  const [resumeText, setResumeText] = useState('')
  const [coverText, setCoverText] = useState('')
  const [loading, setLoading] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (initialTab === 'cover') loadCoverLetter()
    else loadResume()
  }, [])

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  async function loadResume() {
    if (resumeText) return
    setLoading(true)
    try {
      const res = await tailorResume(job.job_id)
      setResumeText(res.data.tailored_resume)
    } catch {
      setResumeText('Could not generate a tailored resume.')
    } finally {
      setLoading(false)
    }
  }

  async function loadCoverLetter() {
    if (coverText) return
    setLoading(true)
    try {
      const res = await generateCoverLetter(job.job_id)
      setCoverText(res.data.cover_letter)
    } catch {
      setCoverText('Could not generate a cover letter.')
    } finally {
      setLoading(false)
    }
  }

  function handleTabChange(t) {
    setTab(t)
    if (t === 'cover') loadCoverLetter()
    else loadResume()
  }

  function copy() {
    navigator.clipboard.writeText(tab === 'resume' ? resumeText : coverText)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="flex max-h-[80vh] w-full max-w-2xl flex-col rounded border border-line bg-bg animate-viewIn">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-lg">{job.title}</h2>
            <p className="text-xs+ text-neutral-600">{job.company}</p>
          </div>
          <button onClick={onClose} aria-label="Close" className="btn btn-neutral btn-icon">
            <X size={14} />
          </button>
        </div>

        <div className="flex gap-1.5 px-6 pt-4">
          {[
            ['resume', 'Tailored resume'],
            ['cover', 'Cover letter'],
          ].map(([key, label]) => (
            <button
              key={key}
              onClick={() => handleTabChange(key)}
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
          ) : (
            <pre className="whitespace-pre-wrap font-mono text-sm leading-relaxed text-text">
              {tab === 'resume' ? resumeText : coverText}
            </pre>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-6 py-4">
          {tab === 'resume' && (
            <button onClick={() => downloadTailoredPdf(job.job_id)} className="btn btn-neutral">
              Download PDF
            </button>
          )}
          <button onClick={copy} className="btn btn-accent">
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>
    </div>
  )
}
