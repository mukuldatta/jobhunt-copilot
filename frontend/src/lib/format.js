// Formatting shared across the four screens. Kept together so "2h", "3d ago"
// and "Bengaluru" read identically wherever they appear.

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

export const weekday = (d = new Date()) => WEEKDAYS[d.getDay()]

export function clockTime(value) {
  if (!value) return null
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

/** Compact age: "2h", "5h", "1d", "2d". Used in the Review list. */
export function age(value) {
  if (!value) return ''
  const then = new Date(value)
  if (Number.isNaN(then.getTime())) return ''
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
