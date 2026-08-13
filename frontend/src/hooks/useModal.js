import { useEffect, useRef } from 'react'

/**
 * The behaviour a dialog needs to actually behave like one.
 *
 * Escape already worked in both modals; everything else did not. Tab walked
 * straight out into the page behind, the background scrolled under the
 * overlay, and closing left focus on <body> so the next keystroke went
 * nowhere. Screen readers were never told a dialog had opened at all.
 *
 * Returns a ref to put on the dialog element. Pair it with
 * role="dialog" aria-modal="true" and an aria-labelledby.
 */
export function useModal(onClose) {
  const ref = useRef(null)
  const restoreTo = useRef(null)

  useEffect(() => {
    restoreTo.current = document.activeElement

    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    // Focus the dialog itself rather than its first control: landing on
    // "Close" invites dismissing the thing you just opened.
    ref.current?.focus()

    const onKey = (e) => {
      if (e.key === 'Escape') {
        onClose()
        return
      }
      if (e.key !== 'Tab' || !ref.current) return

      const focusable = ref.current.querySelectorAll(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
      )
      if (!focusable.length) return

      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement

      // Wrap at both ends, and pull focus in when it is still on the
      // container from the initial ref.focus() above.
      if (e.shiftKey && (active === first || active === ref.current)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
      restoreTo.current?.focus?.()
    }
  }, [onClose])

  return ref
}

/**
 * Close when the click started AND ended on the backdrop itself.
 *
 * A plain onClick fires when a text selection that began inside the dialog
 * happens to release over the backdrop, which closed the modal and threw away
 * the text you were trying to copy.
 */
export function backdropProps(onClose) {
  let downOnBackdrop = false
  return {
    onMouseDown: (e) => {
      downOnBackdrop = e.target === e.currentTarget
    },
    onClick: (e) => {
      if (downOnBackdrop && e.target === e.currentTarget) onClose()
      downOnBackdrop = false
    },
  }
}
