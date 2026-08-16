import { useCallback, useEffect, useRef, useState } from 'react'
import { getAgentLog } from '../api'
import { useAgent } from '../hooks/useAgent'
import { useReducedMotion } from '../hooks/useMotion'
import { logTime } from '../lib/format'

// Fast enough that a step appears while you are still wondering whether it
// happened, slow enough that a run driving a real browser is not also serving
// thirty requests a minute. Idle needs none of that: nothing appends to the
// buffer when no run is in flight, so we stop asking and pick up again on the
// first poll after one starts.
const LIVE_POLL_MS = 2000
const IDLE_POLL_MS = 15000
// The server holds 400 lines; there is no reason for the tab to hold more.
const MAX_LINES = 400

/**
 * Lines the agent wrote about itself, in one accent hue like everything else.
 *
 * The distinction worth drawing is between a step that happened and a step that
 * needs you: an unanswered question, a pause on a CAPTCHA, a halt. Those are
 * the lines you scan for, so they carry the accent; the rest is the trail that
 * explains them and stays quiet.
 */
function toneOf(msg) {
  if (/^\[\d+\/\d+\]/.test(msg)) return 'text-text' // "[3/5] Title @ Company"
  if (/^(——|halted|run refused)/.test(msg)) return 'text-neutral-600'
  if (/\[(\?|!|PAUSE|DEFER|TIMEOUT)\]|^skipped|could not|failed/i.test(msg)) return 'text-accent-400'
  return 'text-neutral-500'
}

export default function AgentLog() {
  const { running } = useAgent()
  const reduced = useReducedMotion()

  const [lines, setLines] = useState([])
  const [job, setJob] = useState('')
  const since = useRef(0)
  const box = useRef(null)
  // Following the tail is only helpful while you are AT the tail. Scrolling up
  // to read why something was deferred, and being yanked back down two seconds
  // later, makes the log unreadable exactly when it matters.
  const stuckToBottom = useRef(true)

  const poll = useCallback(async () => {
    try {
      const { data } = await getAgentLog(since.current)
      since.current = data.seq ?? since.current
      setJob(data.job || '')
      if (data.lines?.length) {
        setLines((prev) => [...prev, ...data.lines].slice(-MAX_LINES))
      }
    } catch {
      /* useAgent already owns the "backend unreachable" banner */
    }
  }, [])

  useEffect(() => {
    let timer = null

    // Same rule the agent strip follows: a hidden tab is not being read, and
    // this is the fastest poll in the app pointed at a backend that may be
    // driving a real browser session at the time. Coming back re-polls at once,
    // and `since` means that catch-up costs one request for the whole gap.
    const start = () => {
      stop()
      if (document.visibilityState === 'hidden') return
      poll()
      timer = setInterval(poll, running ? LIVE_POLL_MS : IDLE_POLL_MS)
    }
    const stop = () => {
      if (timer) clearInterval(timer)
      timer = null
    }

    start()
    document.addEventListener('visibilitychange', start)
    return () => {
      document.removeEventListener('visibilitychange', start)
      stop()
    }
  }, [poll, running])

  useEffect(() => {
    const el = box.current
    if (el && stuckToBottom.current) el.scrollTop = el.scrollHeight
  }, [lines])

  const onScroll = () => {
    const el = box.current
    if (!el) return
    stuckToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
  }

  return (
    <div className="mb-6 overflow-hidden rounded border border-line">
      <div className="flex items-center gap-2.5 px-[18px] py-2.5">
        <span className="section-label">Agent log</span>
        {running && (
          <span
            className={`h-1.5 w-1.5 rounded-full bg-accent ${reduced ? '' : 'animate-dotPulse'}`}
          />
        )}
        <span className="truncate text-xs+ text-neutral-600">
          {running ? job || 'starting up' : 'idle — showing the last run'}
        </span>
      </div>

      <div
        ref={box}
        onScroll={onScroll}
        className="max-h-56 overflow-y-auto border-t border-line-soft px-[18px] py-2.5"
      >
        {lines.length === 0 ? (
          <p className="py-2 text-base text-neutral-600">
            Nothing yet. This fills in as the agent works — which posting it is on, what it
            tailored, and anything it could not answer.
          </p>
        ) : (
          lines.map((l) => (
            <div key={l.seq} className="flex gap-3 py-[3px] text-xs+ leading-[1.55]">
              <span className="flex-none tabular-nums text-neutral-700">{logTime(l.at)}</span>
              {/* The server strips the indentation it printed for a terminal;
                  the hierarchy is re-drawn here as a hanging indent so a wrapped
                  line stays under its own text and not under the clock. */}
              <span className={`min-w-0 flex-1 whitespace-pre-wrap break-words ${toneOf(l.msg)}`}>
                {l.msg}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
