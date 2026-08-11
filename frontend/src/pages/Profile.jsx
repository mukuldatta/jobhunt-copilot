import { useState, useEffect } from 'react'
import { getProfile, saveProfile, getPendingQuestions, answerQuestion, dismissQuestion } from '../api'

const TEXT_FIELDS = [
  ['full_name', 'Full name'],
  ['email', 'Email'],
  ['phone', 'Phone'],
  ['current_city', 'Current city'],
  ['total_years_experience', 'Total years of experience'],
  ['notice_period_days', 'Notice period (days)'],
  ['earliest_start', 'Earliest start date'],
  ['current_ctc', 'Current CTC (blank = ask me)'],
  ['expected_ctc', 'Expected CTC (blank = ask me)'],
  ['highest_degree', 'Highest degree'],
]

const BOOL_FIELDS = [
  ['authorized_to_work', 'Authorized to work (no visa needed)'],
  ['requires_sponsorship', 'Requires visa sponsorship'],
  ['willing_to_relocate', 'Willing to relocate'],
  ['willing_onsite_hybrid', 'Willing to work onsite / hybrid'],
  ['has_bachelors', "Has a bachelor's degree"],
]

export default function Profile() {
  const [p, setP] = useState(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)
  const [pending, setPending] = useState([])
  const [drafts, setDrafts] = useState({})
  const [skillRows, setSkillRows] = useState([])

  async function load() {
    try {
      const [pr, pq] = await Promise.all([getProfile(), getPendingQuestions()])
      setP(pr.data)
      setSkillRows(Object.entries(pr.data.skill_years || {}).map(([k, v]) => ({ k, v })))
      setPending(pq.data.questions || [])
    } catch {
      setMsg('Could not load profile — is the backend running?')
    }
  }
  useEffect(() => { load() }, [])

  function set(field, value) { setP(prev => ({ ...prev, [field]: value })) }

  async function handleSave() {
    setSaving(true); setMsg(null)
    try {
      const skill_years = {}
      skillRows.forEach(({ k, v }) => { if (k.trim()) skill_years[k.trim()] = v })
      await saveProfile({ ...p, skill_years })
      setMsg('Profile saved.')
    } catch (e) {
      setMsg(e.response?.data?.detail || 'Save failed')
    } finally { setSaving(false) }
  }

  async function handleAnswer(q) {
    const a = (drafts[q] || '').trim()
    if (!a) return
    await answerQuestion(q, a)
    setDrafts(d => ({ ...d, [q]: '' }))
    load()
  }

  async function handleDismiss(q) { await dismissQuestion(q); load() }

  if (!p) return <div className="text-textSecondary">{msg || 'Loading…'}</div>

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-textPrimary mb-2">Application Profile</h1>
      <p className="text-textSecondary text-sm mb-8">
        Answers used to fill job-application forms. Filled once — the AI maps each form
        question to these values, and never invents an answer it doesn't have.
      </p>

      {/* Unanswered questions first — they block applications */}
      {pending.length > 0 && (
        <div className="bg-card border border-warning/40 rounded-lg p-6 mb-6">
          <h2 className="text-warning font-semibold mb-1">Questions needing your answer ({pending.length})</h2>
          <p className="text-textSecondary text-sm mb-4">
            Seen on real applications but not answerable from your profile. Answer once — reused forever.
          </p>
          <div className="space-y-3">
            {pending.map(q => (
              <div key={q.question} className="bg-bg border border-border rounded p-3">
                <div className="text-textPrimary text-sm mb-2">{q.question}</div>
                <div className="flex gap-2">
                  <input
                    value={drafts[q.question] || ''}
                    onChange={e => setDrafts(d => ({ ...d, [q.question]: e.target.value }))}
                    placeholder="Your answer"
                    className="flex-1 bg-card border border-border rounded px-3 py-1.5 text-sm text-textPrimary focus:border-accent outline-none"
                  />
                  <button onClick={() => handleAnswer(q.question)}
                    className="px-3 py-1.5 bg-accent text-bg text-sm font-medium rounded hover:bg-accent/90">Save</button>
                  <button onClick={() => handleDismiss(q.question)}
                    className="px-3 py-1.5 text-sm text-textSecondary hover:text-danger">Dismiss</button>
                </div>
                {q.times_seen > 1 && <div className="text-textSecondary text-xs mt-1">seen {q.times_seen}×</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="bg-card border border-border rounded-lg p-6 mb-6">
        <h2 className="text-textPrimary font-semibold mb-4">Details</h2>
        <div className="grid grid-cols-2 gap-4">
          {TEXT_FIELDS.map(([f, label]) => (
            <div key={f}>
              <label className="block text-textSecondary text-xs mb-1">{label}</label>
              <input
                value={p[f] ?? ''}
                onChange={e => set(f, e.target.value)}
                className="w-full bg-bg border border-border rounded px-3 py-2 text-sm text-textPrimary focus:border-accent outline-none"
              />
            </div>
          ))}
        </div>

        <div className="mt-6 space-y-2">
          {BOOL_FIELDS.map(([f, label]) => (
            <label key={f} className="flex items-center gap-2 text-sm text-textPrimary cursor-pointer">
              <input type="checkbox" checked={!!p[f]} onChange={e => set(f, e.target.checked)} className="accent-accent" />
              {label}
            </label>
          ))}
        </div>
      </div>

      <div className="bg-card border border-border rounded-lg p-6 mb-6">
        <h2 className="text-textPrimary font-semibold mb-1">Years per skill</h2>
        <p className="text-textSecondary text-sm mb-4">
          Used for "how many years of experience with X?" questions.
        </p>
        {skillRows.map((row, i) => (
          <div key={i} className="flex gap-2 mb-2">
            <input value={row.k} placeholder="Skill (e.g. Python)"
              onChange={e => setSkillRows(rs => rs.map((r, j) => j === i ? { ...r, k: e.target.value } : r))}
              className="flex-1 bg-bg border border-border rounded px-3 py-1.5 text-sm text-textPrimary focus:border-accent outline-none" />
            <input value={row.v} placeholder="Years"
              onChange={e => setSkillRows(rs => rs.map((r, j) => j === i ? { ...r, v: e.target.value } : r))}
              className="w-24 bg-bg border border-border rounded px-3 py-1.5 text-sm text-textPrimary focus:border-accent outline-none" />
            <button onClick={() => setSkillRows(rs => rs.filter((_, j) => j !== i))}
              className="px-2 text-textSecondary hover:text-danger">×</button>
          </div>
        ))}
        <button onClick={() => setSkillRows(rs => [...rs, { k: '', v: '' }])}
          className="text-accent text-sm hover:underline">+ Add skill</button>
      </div>

      <div className="bg-card border border-border rounded-lg p-6 mb-6">
        <h2 className="text-textPrimary font-semibold mb-1">Notes for the AI</h2>
        <p className="text-textSecondary text-sm mb-3">Extra context to use when answering unusual questions.</p>
        <textarea value={p.notes ?? ''} onChange={e => set('notes', e.target.value)} rows={3}
          className="w-full bg-bg border border-border rounded px-3 py-2 text-sm text-textPrimary focus:border-accent outline-none" />
      </div>

      {(p.qa || []).length > 0 && (
        <div className="bg-card border border-border rounded-lg p-6 mb-6">
          <h2 className="text-textPrimary font-semibold mb-3">Saved answers ({p.qa.length})</h2>
          <div className="space-y-2">
            {p.qa.map((e, i) => (
              <div key={i} className="flex gap-2 items-start text-sm">
                <div className="flex-1 text-textSecondary">{e.question}</div>
                <input value={e.answer}
                  onChange={ev => setP(prev => ({ ...prev, qa: prev.qa.map((x, j) => j === i ? { ...x, answer: ev.target.value } : x) }))}
                  className="w-40 bg-bg border border-border rounded px-2 py-1 text-textPrimary focus:border-accent outline-none" />
                <button onClick={() => setP(prev => ({ ...prev, qa: prev.qa.filter((_, j) => j !== i) }))}
                  className="px-2 text-textSecondary hover:text-danger">×</button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center gap-3">
        <button onClick={handleSave} disabled={saving}
          className="px-5 py-2 bg-accent text-bg text-sm font-medium rounded hover:bg-accent/90 disabled:opacity-50">
          {saving ? 'Saving…' : 'Save profile'}
        </button>
        {msg && <span className="text-sm text-textSecondary">{msg}</span>}
      </div>
    </div>
  )
}
