import { describe, it, expect } from 'vitest'
import { parseServerTime, clockTime, logTime, age, shortLocation, scoreText } from './format'

// The backend serialises datetime.utcnow(), which has no zone marker at all.
// JavaScript reads a zoneless date-time as local, so every clock in the app was
// showing UTC wearing your timezone's label — wrong by the offset, and wrong in
// a way that looks perfectly plausible.
describe('parseServerTime', () => {
  it('reads a zoneless server timestamp as UTC', () => {
    expect(parseServerTime('2026-08-15T01:07:03.556000').toISOString()).toBe(
      '2026-08-15T01:07:03.556Z'
    )
  })

  it('leaves an explicit Z alone', () => {
    expect(parseServerTime('2026-08-15T01:07:03Z').toISOString()).toBe('2026-08-15T01:07:03.000Z')
  })

  it('respects an offset that is already there', () => {
    // next_run_at comes from APScheduler carrying a real offset; appending Z to
    // that would move it by the offset a second time.
    expect(parseServerTime('2026-08-16T13:37:14+05:30').toISOString()).toBe(
      '2026-08-16T08:07:14.000Z'
    )
  })

  it('returns null for nothing and for nonsense', () => {
    expect(parseServerTime(null)).toBeNull()
    expect(parseServerTime('')).toBeNull()
    expect(parseServerTime('not a date')).toBeNull()
  })
})

describe('clockTime', () => {
  it('formats a zoneless timestamp in the viewer’s zone', () => {
    // Asserted against the same conversion rather than a hardcoded string, so
    // the test does not depend on the machine's timezone.
    const expected = new Date('2026-08-15T01:07:03.556Z').toLocaleTimeString([], {
      hour: 'numeric',
      minute: '2-digit',
    })
    expect(clockTime('2026-08-15T01:07:03.556000')).toBe(expected)
  })

  it('is null when there is no value', () => {
    expect(clockTime(null)).toBeNull()
  })
})

describe('logTime', () => {
  it('keeps seconds, because consecutive log lines share a minute', () => {
    expect(logTime('2026-08-15T01:07:03Z')).toMatch(/^\d{2}:\d{2}:\d{2}$/)
  })

  it('is an empty string when there is no value, never "Invalid Date"', () => {
    expect(logTime(undefined)).toBe('')
    expect(logTime('nope')).toBe('')
  })
})

describe('age', () => {
  it('measures from the UTC instant, not from the local reading of it', () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 3600 * 1000)
      .toISOString()
      .replace('Z', '') // exactly how the backend serialises it
    expect(age(twoHoursAgo)).toBe('2h')
  })

  it('never reports a future posting as negative', () => {
    const ahead = new Date(Date.now() + 3600 * 1000).toISOString().replace('Z', '')
    expect(age(ahead)).toBe('0m')
  })

  it('is empty for nothing', () => {
    expect(age(null)).toBe('')
  })
})

describe('shortLocation', () => {
  it('drops the country the row has no width for', () => {
    expect(shortLocation('Bengaluru, India')).toBe('Bengaluru')
  })

  it('keeps a single-part location intact', () => {
    expect(shortLocation('Remote')).toBe('Remote')
  })
})

describe('scoreText', () => {
  it('marks the top band in accent and steps the rest back', () => {
    expect(scoreText(75)).toBe('text-accent-400')
    expect(scoreText(74)).toBe('text-neutral-500')
  })
})
