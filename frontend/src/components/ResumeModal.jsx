import { useState, useEffect } from 'react'
import { tailorResume, generateCoverLetter } from '../api'

export default function ResumeModal({ job, onClose }) {
  const [tab, setTab] = useState('resume')
  const [resumeText, setResumeText] = useState('')
  const [coverText, setCoverText] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    loadResume()
  }, [])

  async function loadResume() {
    setLoading(true)
    try {
      const res = await tailorResume(job.job_id)
      setResumeText(res.data.tailored_resume)
    } catch (e) {
      setResumeText('Error generating tailored resume.')
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
    } catch (e) {
      setCoverText('Error generating cover letter.')
    } finally {
      setLoading(false)
    }
  }

  function handleTabChange(t) {
    setTab(t)
    if (t === 'cover') loadCoverLetter()
  }

  function copyToClipboard(text) {
    navigator.clipboard.writeText(text)
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-card border border-border rounded-xl w-full max-w-2xl max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <div>
            <h2 className="text-textPrimary font-semibold">{job.title}</h2>
            <p className="text-textSecondary text-sm">{job.company}</p>
          </div>
          <button onClick={onClose} className="text-textSecondary hover:text-textPrimary text-xl">&times;</button>
        </div>

        <div className="flex gap-1 px-6 pt-4">
          {['resume', 'cover'].map(t => (
            <button key={t} onClick={() => handleTabChange(t)}
              className={`px-4 py-1.5 rounded text-sm font-medium transition-colors ${
                tab === t ? 'bg-accent/10 text-accent' : 'text-textSecondary hover:text-textPrimary'
              }`}>
              {t === 'resume' ? 'Tailored Resume' : 'Cover Letter'}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-auto p-6">
          {loading ? (
            <div className="text-textSecondary text-center py-8">Generating with AI...</div>
          ) : (
            <pre className="text-textPrimary text-xs whitespace-pre-wrap font-mono leading-relaxed">
              {tab === 'resume' ? resumeText : coverText}
            </pre>
          )}
        </div>

        <div className="px-6 py-4 border-t border-border flex justify-end gap-2">
          <button
            onClick={() => copyToClipboard(tab === 'resume' ? resumeText : coverText)}
            className="px-4 py-2 text-sm bg-accent/10 text-accent rounded hover:bg-accent/20 transition-colors">
            Copy
          </button>
          <button onClick={onClose}
            className="px-4 py-2 text-sm bg-border text-textSecondary rounded hover:text-textPrimary transition-colors">
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
