import { describe, it, expect } from 'vitest'
import { wordDiff, changeSummary } from './diff'

/**
 * This diff is the only control on overstated framing — the validator proves
 * no invented skill or quantity appears, but cannot see "led delivery of"
 * replacing "contributed to". If the diff under-reports, a human reviewing the
 * additions is reviewing the wrong thing.
 */

const textOf = (parts, type) =>
  parts.filter((p) => p.type === type).map((p) => p.text).join('')

// Reassembling each side is the invariant that matters: nothing invented,
// nothing dropped. Both sides must be walked in part order — grouping by type
// first would reorder the text and prove nothing.
const original = (parts) =>
  parts.filter((p) => p.type !== 'add').map((p) => p.text).join('')
const tailored = (parts) =>
  parts.filter((p) => p.type !== 'del').map((p) => p.text).join('')

describe('wordDiff', () => {
  it('reports no change for identical text', () => {
    const parts = wordDiff('Built retrieval pipelines', 'Built retrieval pipelines')
    expect(parts.every((p) => p.type === 'same')).toBe(true)
  })

  it('marks an inserted word as added', () => {
    const parts = wordDiff('Built pipelines', 'Built scalable pipelines')
    expect(textOf(parts, 'add')).toContain('scalable')
    expect(textOf(parts, 'del').trim()).toBe('')
  })

  it('marks a removed word as deleted', () => {
    const parts = wordDiff('Built scalable pipelines', 'Built pipelines')
    expect(textOf(parts, 'del')).toContain('scalable')
  })

  it('shows both sides of a replacement', () => {
    const parts = wordDiff('contributed to delivery', 'led delivery')
    expect(textOf(parts, 'del')).toContain('contributed')
    expect(textOf(parts, 'add')).toContain('led')
  })

  it('reconstructs both inputs exactly', () => {
    const a = 'AI Software Engineer at Incrivelsoft building retrieval pipelines'
    const b = 'Senior AI Engineer at Incrivelsoft designing retrieval systems'
    const parts = wordDiff(a, b)
    expect(original(parts)).toBe(a)
    expect(tailored(parts)).toBe(b)
  })

  it('preserves whitespace and newlines', () => {
    const a = 'Mukul Mokkapati\nAI Engineer'
    const parts = wordDiff(a, a)
    expect(parts.map((p) => p.text).join('')).toBe(a)
  })

  it('merges runs instead of emitting one span per word', () => {
    // Rendering is one <span> per part, so unmerged runs would be hundreds of
    // elements for a single edit.
    const parts = wordDiff('a b c d e f', 'a b c d e f')
    expect(parts.length).toBe(1)
    expect(parts[0].type).toBe('same')
  })

  it('treats an empty original as all additions', () => {
    const parts = wordDiff('', 'Entirely new resume')
    expect(parts).toEqual([{ type: 'add', text: 'Entirely new resume' }])
  })

  it('handles both sides empty', () => {
    expect(wordDiff('', '')).toEqual([])
  })

  it('treats an empty tailored side as all deletions', () => {
    const parts = wordDiff('Some text', '')
    expect(parts.every((p) => p.type === 'del')).toBe(true)
    expect(original(parts)).toBe('Some text')
  })

  it('tolerates null input', () => {
    expect(() => wordDiff(null, null)).not.toThrow()
    expect(wordDiff(null, null)).toEqual([])
  })
})

describe('changeSummary', () => {
  it('counts words per category', () => {
    const s = changeSummary([
      { type: 'same', text: 'Built retrieval ' },
      { type: 'add', text: 'scalable robust ' },
      { type: 'del', text: 'simple ' },
    ])
    expect(s).toEqual({ same: 2, added: 2, removed: 1 })
  })

  it('does not count whitespace-only parts', () => {
    const s = changeSummary([
      { type: 'same', text: '   ' },
      { type: 'add', text: '\n\n' },
    ])
    expect(s).toEqual({ same: 0, added: 0, removed: 0 })
  })

  it('reports zero for an empty diff', () => {
    expect(changeSummary([])).toEqual({ same: 0, added: 0, removed: 0 })
  })

  it('agrees with the diff it summarises', () => {
    const parts = wordDiff('one two three', 'one two three four')
    expect(changeSummary(parts).added).toBe(1)
    expect(changeSummary(parts).removed).toBe(0)
  })
})
