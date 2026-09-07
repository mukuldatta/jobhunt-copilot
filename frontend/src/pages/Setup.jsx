import { useCallback, useEffect, useRef, useState } from 'react'
import { NavLink, useParams } from 'react-router-dom'
import { Check, Plus, X } from '@phosphor-icons/react'
import {
  getProfile,
  saveProfile,
  getPendingQuestions,
  answerQuestion,
  dismissQuestion,
  getResume,
  uploadResume,
  getAuthStatus,
  platformLogin,
  platformCheck,
  getSettings,
  saveSettings,
  errorMessage,
} from '../api'
import { useReducedMotion } from '../hooks/useMotion'
import { useToast } from '../components/Toast'
import { agoLabel } from '../lib/format'

const TABS = [
  { slug: 'you', label: 'You' },
  { slug: 'resume', label: 'Resume' },
  { slug: 'boards', label: 'Job boards' },
  { slug: 'rules', label: 'Agent rules' },
  { slug: 'answers', label: 'Saved answers' },
]

const PLATFORM_LABELS = { naukri: 'Naukri', linkedin: 'LinkedIn', indeed: 'Indeed' }

let fieldSeq = 0

function Field({ label, value, onChange, placeholder, type = 'text' }) {
  // Without htmlFor/id the label was decoration — clicking it did nothing.
  const id = useRef(`field-${fieldSeq++}`).current
  return (
    <div>
      <label htmlFor={id} className="mb-[5px] block text-xs+ text-neutral-600">
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value ?? ''}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="field-box"
      />
    </div>
  )
}

/** Checkboxes become chips: on = accent outline + check, off = faint outline. */
function Toggle({ label, on, onChange }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={!!on}
      onClick={() => onChange(!on)}
      className={`chip ${on ? 'chip-on' : 'chip-off'}`}
    >
      {on && <Check size={12} />}
      {label}
    </button>
  )
}

function SaveRow({ onSave, saving, savedAt, disabled }) {
  return (
    <div className="flex items-center gap-3">
      <button onClick={onSave} disabled={saving || disabled} className="btn btn-accent px-[18px] py-2">
        {saving ? 'Saving' : 'Save'}
      </button>
      {savedAt && <span className="text-sm text-neutral-600">{savedAt}</span>}
    </div>
  )
}

function Header({ title, blurb }) {
  return (
    <>
      <h1 className="text-3xl tracking-[-0.02em]">{title}</h1>
      <p className="mb-[26px] mt-1.5 max-w-blurb text-base text-neutral-500">{blurb}</p>
    </>
  )
}

const Divider = () => <div className="rule-fade mb-6" />

export default function Setup() {
  const { tab = 'you' } = useParams()
  const reduced = useReducedMotion()

  const [profile, setProfile] = useState(null)
  const [skillRows, setSkillRows] = useState([])
  const [rules, setRules] = useState(null)
  const [resume, setResume] = useState(null)
  const [platforms, setPlatforms] = useState([])
  const [questions, setQuestions] = useState([])
  const [drafts, setDrafts] = useState({})
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState(null)
  const [message, setMessage] = useState(null)
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [checking, setChecking] = useState(null)
  const [answersDirty, setAnswersDirty] = useState(false)

  const notify = useToast()

  // Sign-in happens in a browser window outside this app, so there is nothing
  // to await — we re-check twice while you are likely to be finishing. The
  // timers are tracked so leaving the page cancels them instead of calling
  // setState on an unmounted screen.
  const pending = useRef([])
  useEffect(() => () => pending.current.forEach(clearTimeout), [])

  const load = useCallback(async () => {
    const [p, s, r, a, q] = await Promise.allSettled([
      getProfile(),
      getSettings(),
      getResume(),
      getAuthStatus(),
      getPendingQuestions(),
    ])
    if (p.status === 'fulfilled') {
      setProfile(p.value.data)
      setSkillRows(Object.entries(p.value.data.skill_years || {}).map(([k, v]) => ({ k, v })))
    } else {
      setMessage('Could not load your profile — is the backend running?')
      setAnswersDirty(false)
    }
    if (s.status === 'fulfilled') setRules(s.value.data)
    if (r.status === 'fulfilled') setResume(r.value.data)
    if (a.status === 'fulfilled') setPlatforms(a.value.data.platforms || [])
    if (q.status === 'fulfilled') setQuestions(q.value.data.questions || [])
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const set = (field, value) => setProfile((prev) => ({ ...prev, [field]: value }))

  async function saveProfileTab() {
    setSaving(true)
    setMessage(null)
    try {
      const skill_years = {}
      skillRows.forEach(({ k, v }) => {
        if (k.trim()) skill_years[k.trim()] = v
      })
      await saveProfile({ ...profile, skill_years })
      setSavedAt('Saved just now')
      setAnswersDirty(false)
      notify.ok('Profile saved.')
    } catch (e) {
      notify.err(errorMessage(e, 'Save failed.'), { retry: saveProfileTab })
    } finally {
      setSaving(false)
    }
  }

  async function saveRules() {
    setSaving(true)
    setMessage(null)
    try {
      const res = await saveSettings(rules)
      setRules(res.data)
      setSavedAt('Saved just now')
      notify.ok('Agent rules saved.')
    } catch (e) {
      notify.err(errorMessage(e, 'Save failed.'), { retry: saveRules })
    } finally {
      setSaving(false)
    }
  }

  async function handleUpload(selected) {
    const target = selected || file
    if (!target) return
    setUploading(true)
    setMessage(null)
    try {
      const res = await uploadResume(target)
      notify.ok(`Resume uploaded — ${res.data.skills_found} skills found.`)
      const r = await getResume()
      setResume(r.data)
    } catch (e) {
      notify.err(errorMessage(e, 'Upload failed.'), { retry: () => handleUpload(target) })
    } finally {
      setUploading(false)
    }
  }

  async function handleCheck(platform) {
    setChecking(platform)
    setMessage(null)
    try {
      const res = await platformCheck(platform)
      setPlatforms((prev) =>
        prev.map((p) => (p.platform === platform ? { ...p, logged_in: res.data.logged_in } : p))
      )
    } catch (e) {
      notify.err(errorMessage(e, `Could not check ${platform}.`), {
        retry: () => handleCheck(platform),
      })
    } finally {
      setChecking(null)
    }
  }

  async function handleLogin(platform) {
    setMessage(null)
    try {
      const res = await platformLogin(platform)
      notify.ok(res.data.message)
      pending.current.push(setTimeout(load, 8000), setTimeout(load, 30000))
    } catch (e) {
      notify.err(errorMessage(e, `Could not start the ${platform} sign-in.`))
    }
  }

  // Only the two things an answer changes, rather than re-running all five
  // requests to reflect one row.
  const refreshAnswers = useCallback(async () => {
    const [q, p] = await Promise.allSettled([getPendingQuestions(), getProfile()])
    if (q.status === 'fulfilled') setQuestions(q.value.data.questions || [])
    if (p.status === 'fulfilled') setProfile(p.value.data)
  }, [])

  async function handleAnswer(question) {
    const answer = (drafts[question] || '').trim()
    if (!answer) return
    try {
      await answerQuestion(question, answer)
      setDrafts((d) => ({ ...d, [question]: '' }))
      await refreshAnswers()
    } catch (e) {
      notify.err(errorMessage(e, 'Could not save that answer.'), {
        retry: () => handleAnswer(question),
      })
    }
  }

  const savedAnswers = profile?.qa || []

  return (
    <div className="flex h-full">
      <div className="hidden w-setupIndex flex-none py-8 pl-8 md:block">
        <div className="section-label mb-3">Setup</div>
        <div className="flex flex-col gap-px">
          {TABS.map((t) => (
            <NavLink
              key={t.slug}
              to={`/setup/${t.slug}`}
              className={({ isActive }) =>
                `border-l-2 px-2.5 py-1.5 text-base transition-colors duration-180 ${
                  isActive
                    ? 'border-l-accent text-accent-400'
                    : 'border-l-transparent text-neutral-500 hover:text-text'
                }`
              }
            >
              {t.label}
              {t.slug === 'answers' && savedAnswers.length > 0 && (
                <span className="ml-1.5 text-neutral-600">{savedAnswers.length}</span>
              )}
            </NavLink>
          ))}
        </div>
      </div>

      <div
        key={tab}
        className={`min-w-0 flex-1 overflow-y-auto px-6 pb-10 pt-8 md:px-10 ${
          reduced ? '' : 'animate-tabIn'
        }`}
      >
        <div className="max-w-setup">
          {message && (
            <div className="mb-5 flex items-center justify-between rounded border border-line px-4 py-2.5 text-base text-accent-400">
              {message}
              <button
                onClick={() => setMessage(null)}
                className="text-xs+ text-neutral-600 hover:text-text"
              >
                Dismiss
              </button>
            </div>
          )}

          {tab === 'you' && profile && (
            <>
              <Header
                title="You"
                blurb="What the agent puts on application forms. It never invents an answer — anything it cannot ground here comes back to you on Today."
              />
              <div className="mb-7 grid grid-cols-1 gap-x-5 gap-y-4 sm:grid-cols-2">
                <Field label="Full name" value={profile.full_name} onChange={(v) => set('full_name', v)} />
                <Field label="Email" value={profile.email} onChange={(v) => set('email', v)} />
                <Field label="Phone" value={profile.phone} onChange={(v) => set('phone', v)} />
                <Field
                  label="Based in"
                  value={profile.current_city}
                  onChange={(v) => set('current_city', v)}
                />
              </div>

              <Divider />
              <div className="section-label mb-3.5">Availability &amp; money</div>
              <div className="mb-5 grid grid-cols-1 gap-x-5 gap-y-4 sm:grid-cols-3">
                <Field
                  label="Notice period (days)"
                  value={profile.notice_period_days}
                  onChange={(v) => set('notice_period_days', v)}
                />
                <Field
                  label="Earliest start"
                  value={profile.earliest_start}
                  onChange={(v) => set('earliest_start', v)}
                />
                <Field
                  label="Expected CTC"
                  value={profile.expected_ctc}
                  placeholder="blank — ask me"
                  onChange={(v) => set('expected_ctc', v)}
                />
              </div>
              <div className="mb-7 flex flex-wrap gap-2">
                <Toggle
                  label="Authorized to work"
                  on={!!profile.authorized_to_work}
                  onChange={(v) => set('authorized_to_work', v)}
                />
                <Toggle
                  label="Needs sponsorship"
                  on={!!profile.requires_sponsorship}
                  onChange={(v) => set('requires_sponsorship', v)}
                />
                <Toggle
                  label="Will relocate"
                  on={!!profile.willing_to_relocate}
                  onChange={(v) => set('willing_to_relocate', v)}
                />
                <Toggle
                  label="Onsite / hybrid ok"
                  on={!!profile.willing_onsite_hybrid}
                  onChange={(v) => set('willing_onsite_hybrid', v)}
                />
                <Toggle
                  label="Bachelor's"
                  on={!!profile.has_bachelors}
                  onChange={(v) => set('has_bachelors', v)}
                />
              </div>

              <Divider />
              <div className="mb-3.5 flex items-baseline justify-between">
                <div className="section-label">Years per skill</div>
                <button
                  onClick={() => setSkillRows((rs) => [...rs, { k: '', v: '' }])}
                  className="inline-flex items-center gap-1 text-sm text-accent hover:text-accent-300"
                >
                  <Plus size={11} /> Add
                </button>
              </div>
              <div className="mb-6 flex flex-wrap gap-2">
                {skillRows.map((row, i) => (
                  <span
                    key={i}
                    className="inline-flex items-baseline gap-1.5 rounded border border-line px-[11px] py-[5px] text-sm+ text-neutral-500"
                  >
                    <input
                      value={row.k}
                      placeholder="Skill"
                      onChange={(e) =>
                        setSkillRows((rs) =>
                          rs.map((r, j) => (j === i ? { ...r, k: e.target.value } : r))
                        )
                      }
                      className="w-24 bg-transparent outline-none placeholder:text-neutral-700"
                    />
                    <input
                      value={row.v}
                      placeholder="yrs"
                      onChange={(e) =>
                        setSkillRows((rs) =>
                          rs.map((r, j) => (j === i ? { ...r, v: e.target.value } : r))
                        )
                      }
                      className="w-8 bg-transparent text-text outline-none placeholder:text-neutral-700"
                    />
                    <button
                      onClick={() => setSkillRows((rs) => rs.filter((_, j) => j !== i))}
                      aria-label="Remove skill"
                      className="text-neutral-700 hover:text-text"
                    >
                      <X size={10} />
                    </button>
                  </span>
                ))}
              </div>

              <SaveRow onSave={saveProfileTab} saving={saving} savedAt={savedAt} />
            </>
          )}

          {tab === 'resume' && (
            <>
              <Header
                title="Resume"
                blurb="The single source the scorer and the tailor both read. The tailor only reframes what is already here — it never adds a skill you do not have."
              />
              <div className="mb-7 grid grid-cols-1 gap-x-5 gap-y-4 sm:grid-cols-2">
                <div>
                  <label className="mb-[5px] block text-xs+ text-neutral-600">Parse result</label>
                  <div className="field-box text-neutral-500">
                    {resume ? `${(resume.skills || []).length} skills found` : 'No resume uploaded'}
                  </div>
                </div>
                <div>
                  <label className="mb-[5px] block text-xs+ text-neutral-600">Uploaded</label>
                  <div className="field-box text-neutral-500">
                    {resume?.uploaded_at ? new Date(resume.uploaded_at).toLocaleDateString() : '—'}
                  </div>
                </div>
              </div>

              <label
                htmlFor="resume-upload"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault()
                  const dropped = e.dataTransfer.files?.[0]
                  if (dropped) {
                    setFile(dropped)
                    handleUpload(dropped)
                  }
                }}
                className="mb-6 block cursor-pointer rounded border border-dashed border-line px-6 py-8
                           text-center text-base text-neutral-600 transition-colors duration-180 hover:border-text/40"
              >
                <input
                  id="resume-upload"
                  type="file"
                  accept=".pdf"
                  className="hidden"
                  onChange={(e) => {
                    const picked = e.target.files[0]
                    setFile(picked)
                    handleUpload(picked)
                  }}
                />
                {uploading ? 'Uploading…' : file ? file.name : 'Drop a PDF here, or click to choose one'}
              </label>

              {resume?.skills?.length > 0 && (
                <>
                  <Divider />
                  <div className="section-label mb-3.5">Skills the parser found</div>
                  <div className="flex flex-wrap gap-2">
                    {resume.skills.map((skill) => (
                      <span
                        key={skill}
                        className="rounded border border-line px-[11px] py-[5px] text-sm+ text-neutral-500"
                      >
                        {skill}
                      </span>
                    ))}
                  </div>
                </>
              )}
            </>
          )}

          {tab === 'boards' && (
            <>
              <Header
                title="Job boards"
                blurb="Sign in once per session. A browser window opens on this machine — finish the login, including any 2FA or CAPTCHA, and it is saved and reused. Naukri has two: scraping and applying use separate browser profiles, so each is signed in on its own. Check asks the site; the age beside it is only when you last signed in."
              />
              <div className="flex flex-col gap-2">
                {platforms.length === 0 && (
                  <p className="text-base text-neutral-600">
                    No status yet — is the backend running?
                  </p>
                )}
                {platforms.map((p) => (
                  <div
                    key={p.platform}
                    className="flex items-center justify-between rounded border border-line px-4 py-3"
                  >
                    <div className="flex items-center gap-2.5">
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${
                          p.logged_in ? 'bg-accent' : 'bg-neutral-700'
                        }`}
                        style={
                          p.logged_in ? { boxShadow: '0 0 0 3px var(--accent-glow)' } : undefined
                        }
                      />
                      <span className="text-base">
                        {p.label || PLATFORM_LABELS[p.platform] || p.platform}
                      </span>
                      {/* "signed in" on its own was a claim about now, made from
                          a timestamp that could be weeks old and a cookie that
                          died days ago. The age is the honest version of it. */}
                      <span className="text-xs+ text-neutral-600">
                        {p.logged_in
                          ? `signed in ${agoLabel(p.logged_in_at)}`
                          : 'not signed in'}
                      </span>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleCheck(p.platform)}
                        disabled={checking === p.platform}
                        className="btn btn-neutral btn-sm"
                      >
                        {checking === p.platform ? 'Checking' : 'Check'}
                      </button>
                      <button
                        onClick={() => handleLogin(p.platform)}
                        className="btn btn-accent btn-sm"
                      >
                        {p.logged_in ? 'Re-login' : 'Login'}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}

          {tab === 'rules' && rules && (
            <>
              <Header
                title="Agent rules"
                blurb="The guardrails on an irreversible action. You cannot un-apply, so every one of these is a limit rather than a target."
              />
              <div className="mb-7 grid grid-cols-1 gap-x-5 gap-y-4 sm:grid-cols-2">
                <Field
                  label="Minimum score to apply"
                  type="number"
                  value={rules.min_score}
                  onChange={(v) => setRules((r) => ({ ...r, min_score: Number(v) }))}
                />
                <Field
                  label="Applications per day"
                  type="number"
                  value={rules.daily_cap}
                  onChange={(v) => setRules((r) => ({ ...r, daily_cap: Number(v) }))}
                />
                <Field
                  label="Applications per run"
                  type="number"
                  value={rules.per_run}
                  onChange={(v) => setRules((r) => ({ ...r, per_run: Number(v) }))}
                />
                <Field
                  label="Run interval (minutes)"
                  type="number"
                  value={rules.interval_minutes}
                  onChange={(v) => setRules((r) => ({ ...r, interval_minutes: Number(v) }))}
                />
              </div>
              <div className="mb-7 flex flex-wrap gap-2">
                <Toggle
                  label="Auto-apply enabled"
                  on={!!rules.auto_apply_enabled}
                  onChange={(v) => setRules((r) => ({ ...r, auto_apply_enabled: v }))}
                />
                <Toggle
                  label="Dry run only"
                  on={!!rules.dry_run}
                  onChange={(v) => setRules((r) => ({ ...r, dry_run: v }))}
                />
                <Toggle
                  label="Alert on high matches"
                  on={!!rules.alerts_enabled}
                  onChange={(v) => setRules((r) => ({ ...r, alerts_enabled: v }))}
                />
                <Toggle
                  label="SMS alerts"
                  on={!!rules.sms_alerts}
                  onChange={(v) => setRules((r) => ({ ...r, sms_alerts: v }))}
                />
              </div>
              <SaveRow onSave={saveRules} saving={saving} savedAt={savedAt} />
            </>
          )}

          {tab === 'answers' && (
            <>
              <Header
                title="Saved answers"
                blurb="Answers learned from real application forms, reused the next time the same question appears. Anything still unanswered is waiting for you on Today."
              />

              {questions.length > 0 && (
                <>
                  <div className="section-label mb-3.5">
                    Waiting on you — {questions.length}
                  </div>
                  <div className="mb-7 flex flex-col gap-2">
                    {questions.map((q) => (
                      <div key={q.question} className="rounded border border-line px-4 py-3">
                        <div className="mb-2 text-base">{q.question}</div>
                        <div className="flex gap-2">
                          <input
                            value={drafts[q.question] || ''}
                            placeholder="Your answer"
                            onChange={(e) =>
                              setDrafts((d) => ({ ...d, [q.question]: e.target.value }))
                            }
                            className="field-box flex-1"
                          />
                          <button
                            onClick={() => handleAnswer(q.question)}
                            className="btn btn-accent btn-sm"
                          >
                            Save
                          </button>
                          <button
                            onClick={() =>
                              dismissQuestion(q.question)
                                .then(refreshAnswers)
                                .catch((e) => notify.err(errorMessage(e, 'Could not dismiss that.')))
                            }
                            className="btn btn-neutral btn-sm"
                          >
                            Dismiss
                          </button>
                        </div>
                        {q.times_seen > 1 && (
                          <div className="mt-1.5 text-xs+ text-neutral-600">
                            seen {q.times_seen}×
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                  <Divider />
                </>
              )}

              <div className="section-label mb-3.5">Answered — {savedAnswers.length}</div>
              {savedAnswers.length === 0 ? (
                <p className="text-base text-neutral-600">
                  Nothing learned yet. The first application form will start filling this.
                </p>
              ) : (
                <div className="overflow-hidden rounded border border-line">
                  {savedAnswers.map((entry, i) => (
                    <div
                      key={i}
                      className="flex items-start gap-4 border-t border-line-soft px-4 py-2.5 first:border-t-0"
                    >
                      <div className="flex-1 text-base text-neutral-500">{entry.question}</div>
                      <input
                        value={entry.answer}
                        onChange={(e) => {
                          setAnswersDirty(true)
                          setProfile((prev) => ({
                            ...prev,
                            qa: prev.qa.map((x, j) =>
                              j === i ? { ...x, answer: e.target.value } : x
                            ),
                          }))
                        }}
                        className="field-box w-48 py-1 text-sm"
                      />
                      <button
                        onClick={() => {
                          setAnswersDirty(true)
                          setProfile((prev) => ({
                            ...prev,
                            qa: prev.qa.filter((_, j) => j !== i),
                          }))
                        }}
                        aria-label="Remove answer"
                        className="mt-1.5 text-neutral-700 hover:text-text"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {/* Also shown when the list is empty but dirty. Gating purely on
                  length meant deleting your last answer unmounted the Save
                  button, so that one deletion could never be persisted. */}
              {(savedAnswers.length > 0 || answersDirty) && (
                <div className="mt-6">
                  <SaveRow onSave={saveProfileTab} saving={saving} savedAt={savedAt} />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
