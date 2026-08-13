/**
 * Word-level diff, for showing what tailoring actually changed.
 *
 * The validator is lexical: it proves no invented technology or quantity
 * appears. It cannot judge whether "led delivery of" overstates "contributed
 * to", because every word is already in the original. There is no mechanical
 * check for that, so the control is a human reading it — which only works if
 * the changes are visible rather than buried in a wall of plausible prose.
 */

const WORD = /(\s+)/

function tokenize(text) {
  return (text || '').split(WORD).filter((t) => t !== '')
}

/**
 * Longest common subsequence over word tokens.
 * Resume-sized inputs are a few hundred words, so the quadratic table is fine.
 */
function lcsTable(a, b) {
  const table = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1))
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i][j] =
        a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1])
    }
  }
  return table
}

/**
 * Returns [{ type: 'same' | 'add' | 'del', text }] walking original → tailored.
 * Runs of the same type are merged so the rendered output isn't one span per word.
 */
export function wordDiff(original, tailored) {
  const a = tokenize(original)
  const b = tokenize(tailored)
  if (!a.length) return b.length ? [{ type: 'add', text: b.join('') }] : []

  const table = lcsTable(a, b)
  const out = []
  const push = (type, text) => {
    const last = out[out.length - 1]
    if (last && last.type === type) last.text += text
    else out.push({ type, text })
  }

  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      push('same', a[i])
      i++
      j++
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      push('del', a[i])
      i++
    } else {
      push('add', b[j])
      j++
    }
  }
  while (i < a.length) push('del', a[i++])
  while (j < b.length) push('add', b[j++])

  return out
}

/** How much of the tailored text is new — a quick read on how far it drifted. */
export function changeSummary(parts) {
  let added = 0
  let removed = 0
  let same = 0
  parts.forEach((p) => {
    const n = p.text.trim() ? p.text.trim().split(/\s+/).length : 0
    if (p.type === 'add') added += n
    else if (p.type === 'del') removed += n
    else same += n
  })
  return { added, removed, same }
}
