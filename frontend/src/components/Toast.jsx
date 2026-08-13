import { createContext, useContext, useCallback, useRef, useState } from 'react'
import { X } from '@phosphor-icons/react'

/**
 * One notification channel, replacing four near-identical banners.
 *
 * Today, Review, Pipeline and Setup each grew their own dismissible strip, and
 * three of them pushed successes and failures through the same accent-coloured
 * element — "Resume uploaded" and "Save failed" looked identical. Tone is now
 * explicit, failures carry an optional retry, and the region is announced, which
 * none of the banners were.
 *
 * Nocturne has no red or green, so tone is carried by the border and the label
 * rather than by hue.
 */

const ToastContext = createContext(null)

const AUTO_DISMISS_MS = 5000

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const nextId = useRef(0)

  const dismiss = useCallback((id) => {
    setToasts((t) => t.filter((x) => x.id !== id))
  }, [])

  const push = useCallback(
    (tone, message, options = {}) => {
      const id = nextId.current++
      setToasts((t) => [...t, { id, tone, message, retry: options.retry }])
      // Failures stay until dismissed: an error that vanishes on its own is an
      // error the user may never have read.
      if (tone === 'ok') setTimeout(() => dismiss(id), options.duration ?? AUTO_DISMISS_MS)
      return id
    },
    [dismiss]
  )

  const notify = useRef({
    ok: (m, o) => push('ok', m, o),
    err: (m, o) => push('err', m, o),
  })
  notify.current.ok = (m, o) => push('ok', m, o)
  notify.current.err = (m, o) => push('err', m, o)

  return (
    <ToastContext.Provider value={notify.current}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-full max-w-sm flex-col gap-2"
        role="status"
        aria-live="polite"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto flex items-start gap-3 rounded border bg-bg px-4 py-3 animate-viewIn ${
              t.tone === 'err' ? 'border-accent' : 'border-line'
            }`}
          >
            <div className="min-w-0 flex-1">
              <p className="section-label mb-1">{t.tone === 'err' ? 'Failed' : 'Done'}</p>
              <p className="text-base text-text">{t.message}</p>
              {t.retry && (
                <button
                  onClick={() => {
                    dismiss(t.id)
                    t.retry()
                  }}
                  className="mt-2 text-xs+ text-accent-400 hover:text-accent-300"
                >
                  Try again
                </button>
              )}
            </div>
            <button
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss"
              className="btn btn-neutral btn-icon shrink-0"
            >
              <X size={12} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}
