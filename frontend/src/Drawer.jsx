import React, { useEffect, useId, useRef } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import './drawer.css'

export default function Drawer({
  title,
  eyebrow,
  description,
  className = '',
  variant = 'drawer',
  onClose,
  children,
}) {
  const titleId = useId()
  const dialog = useRef(null)
  const close = useRef(onClose)
  close.current = onClose

  useEffect(() => {
    const trigger = document.activeElement
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog.current.focus()
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close.current()
      }
      if (event.key !== 'Tab') return
      const controls = [
        ...dialog.current.querySelectorAll(
          'button, a[href], input, select, textarea, [tabindex="0"]',
        ),
      ].filter((element) => !element.disabled && element.getClientRects().length)
      const first = controls[0],
        last = controls.at(-1)
      if (!first) {
        event.preventDefault()
        return
      }
      if (
        event.shiftKey &&
        (document.activeElement === first || document.activeElement === dialog.current)
      ) {
        event.preventDefault()
        last.focus()
      } else if (
        !event.shiftKey &&
        (document.activeElement === last || document.activeElement === dialog.current)
      ) {
        event.preventDefault()
        first.focus()
      }
    }
    const element = dialog.current
    element.addEventListener('keydown', onKeyDown)
    return () => {
      element.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = overflow
      if (trigger?.isConnected) trigger.focus()
    }
  }, [])

  return createPortal(
    <div
      className={`app-drawer-backdrop ${variant === 'modal' ? 'app-dialog-backdrop' : ''}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <aside
        ref={dialog}
        tabIndex={-1}
        className={`app-drawer ${variant === 'modal' ? 'app-dialog' : ''} ${className}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="app-drawer-header">
          <div>
            {eyebrow && <small>{eyebrow}</small>}
            <h2 id={titleId}>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label={`关闭${title}`}
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </header>
        {children}
      </aside>
    </div>,
    document.body,
  )
}
