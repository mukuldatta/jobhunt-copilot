import { useEffect, useRef, useState } from 'react'
import { CaretDown, Plus, Check } from '@phosphor-icons/react'

/**
 * One filter, closed by default. The Jobs page rendered six groups as ~22
 * always-visible buttons; here each group is a chip that carries its current
 * value and opens only when asked.
 *
 * `options` are `{ label, value }`. A chip is "active" — accent outline — when
 * its value is not the group's default.
 */
export default function FilterMenu({ label, options, value, onChange, active, icon = 'caret' }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef(null)
  const trigger = useRef(null)
  const listRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => {
      if (!wrap.current?.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => {
      if (e.key !== 'Escape') return
      setOpen(false)
      trigger.current?.focus()      // closing should not strand focus on body
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    // Move into the list so the options are reachable without a mouse.
    listRef.current?.querySelector('[role="menuitemradio"]')?.focus()
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Up/Down walk the options; Home/End jump. Without this the menu could be
  // opened from the keyboard and then only left again.
  function onListKey(e) {
    const items = [...(listRef.current?.querySelectorAll('[role="menuitemradio"]') || [])]
    const i = items.indexOf(document.activeElement)
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      items[(i + 1) % items.length]?.focus()
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      items[(i - 1 + items.length) % items.length]?.focus()
    } else if (e.key === 'Home') {
      e.preventDefault()
      items[0]?.focus()
    } else if (e.key === 'End') {
      e.preventDefault()
      items[items.length - 1]?.focus()
    }
  }

  const Icon = icon === 'plus' ? Plus : CaretDown

  return (
    <div className="relative" ref={wrap}>
      <button
        ref={trigger}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={`inline-flex items-center gap-1.5 rounded border px-2.5 py-1 text-sm
                    transition-colors duration-180 ${
                      active
                        ? 'border-accent text-accent-400'
                        : 'border-line text-neutral-500 hover:border-text/40'
                    }`}
      >
        {label}
        <Icon size={10} />
      </button>

      {open && (
        <div
          ref={listRef}
          role="menu"
          aria-label={label}
          onKeyDown={onListKey}
          className="absolute left-0 top-[calc(100%+6px)] z-30 min-w-[168px] max-w-[min(240px,90vw)]
                     rounded border border-line bg-bg py-1 animate-viewIn"
        >
          {options.map((opt) => {
            const selected = opt.value === value
            return (
              <button
                key={String(opt.value)}
                type="button"
                role="menuitemradio"
                aria-checked={selected}
                onClick={() => {
                  onChange(opt.value)
                  setOpen(false)
                  trigger.current?.focus()
                }}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-base
                            transition-colors duration-180 hover:bg-text/[0.04] ${
                              selected ? 'text-accent-400' : 'text-neutral-500'
                            }`}
              >
                <span className="w-3 flex-none">{selected && <Check size={12} />}</span>
                {opt.label}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
