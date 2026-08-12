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

  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => {
      if (!wrap.current?.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const Icon = icon === 'plus' ? Plus : CaretDown

  return (
    <div className="relative" ref={wrap}>
      <button
        type="button"
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
          className="absolute left-0 top-[calc(100%+6px)] z-30 min-w-[168px] rounded border border-line
                     bg-bg py-1 animate-viewIn"
        >
          {options.map((opt) => {
            const selected = opt.value === value
            return (
              <button
                key={String(opt.value)}
                type="button"
                onClick={() => {
                  onChange(opt.value)
                  setOpen(false)
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
