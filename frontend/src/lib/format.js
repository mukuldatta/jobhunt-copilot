// Formatting shared across the four screens. Kept together so "2h", "3d ago"
// and "Bengaluru" read identically wherever they appear.

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

export const weekday = (d = new Date()) => WEEKDAYS[d.getDay()]

/**
 * Parse a timestamp the backend produced.
 *
 * Everything server-side is datetime.utcnow(), which serialises with no zone
 * at all — and JavaScript reads a zoneless date-time as LOCAL. So every clock
 * in the app was showing UTC labelled as your time: a scrape at 06:37 IST read
 * "1:07 AM", five and a half hours in the past, which is exactly the sort of
 * wrongness you trust rather than notice.
 *
 * A few values do carry a zone (next_run_at comes from APScheduler with a real
 * offset), so the marker is only added when there is none to begin with.
 */
export function parseServerTime(value) {
  if (!value) return null
  const s = String(value)
  const zoned = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  const d = new Date(zoned ? s : `${s}Z`)
  return Number.isNaN(d.getTime()) ? null : d
}

export function clockTime(value) {
  const d = parseServerTime(value)
  if (!d) return null
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

/** Seconds matter in the agent log: consecutive steps share a minute. */
export function logTime(value) {
  const d = parseServerTime(value)
  if (!d) return ''
  return d.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

/** Compact age: "2h", "5h", "1d", "2d". Used in the Review list. */
export function age(value) {
  const then = parseServerTime(value)
  if (!then) return ''
  const mins = Math.max(0, Math.round((Date.now() - then.getTime()) / 60000))
  if (mins < 60) return `${mins}m`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h`
  return `${Math.round(hours / 24)}d`
}

/** The same span with a trailing word, for prose: "3d ago". */
export const agoLabel = (value) => (age(value) ? `${age(value)} ago` : 'recently')

/** "Bengaluru, India" -> "Bengaluru"; the row has no space for the country. */
export function shortLocation(location) {
  if (!location) return ''
  const first = location.split(',')[0].trim()
  return first || location
}

export const titleCase = (s) => (s ? s.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase()) : '')

/** A job's own status is worth a chip only once it has left "new". */
export const isApplied = (job) => job?.status === 'applied'

/**
 * Score colour is one accent hue at two weights — no red/amber/green. 75%+ of
 * the available points reads as accent; below that it steps back to a dimmer
 * pair, so a list of forty rows is one ranked column.
 */
export const scoreText = (score) => (score >= 75 ? 'text-accent-400' : 'text-neutral-500')
export const scoreFill = (ratio) => (ratio >= 0.75 ? 'var(--accent)' : 'var(--accent-600)')
