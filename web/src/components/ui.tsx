/** Presentational building blocks shared across every view. */

import { useEffect, type ReactNode } from 'react'

import { useToast } from '../lib/store'
import type { Accent, GameStatus } from '../lib/types'

/* ------------------------------------------------------------------ primitives */

export function Panel({
  title,
  subtitle,
  actions,
  children,
  flush,
  className = '',
}: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  flush?: boolean
  className?: string
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <header className="panel-head">
          <div className="col gap-4" style={{ minWidth: 0 }}>
            {typeof title === 'string' ? <h2 className="truncate">{title}</h2> : title}
            {subtitle && <div className="faint" style={{ fontSize: 12 }}>{subtitle}</div>}
          </div>
          {actions && <div className="row gap-6">{actions}</div>}
        </header>
      )}
      <div className={`panel-body${flush ? ' flush' : ''}`}>{children}</div>
    </section>
  )
}

export function Stat({
  value,
  label,
  hint,
  accent,
}: {
  value: ReactNode
  label: string
  hint?: string
  accent?: Accent
}) {
  // Numeric values get the big tabular treatment; word values would overflow at that size.
  const isText = typeof value === 'string' && !/^[\d.,\s%+-]+$/.test(value)

  return (
    <div className="stat" data-accent={accent}>
      <div className={`stat-value${isText ? ' is-text' : ''}`}>{value}</div>
      <div className="label stat-label">{label}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  )
}

export function StatusBadge({ status }: { status: GameStatus }) {
  return (
    <span className={`badge badge-${status}`}>
      {status === 'live' && <i className="dot dot-pulse" />}
      {status}
    </span>
  )
}

export function Avatar({
  user,
  size = 'md',
}: {
  user: { avatar?: string; nickname: string; accent?: Accent }
  size?: 'sm' | 'md' | 'lg'
}) {
  const glyph = user.avatar?.trim() || user.nickname.charAt(0).toUpperCase()
  const className = size === 'lg' ? 'avatar avatar-lg' : size === 'sm' ? 'avatar avatar-sm' : 'avatar'
  return (
    <span className={className} data-accent={user.accent} aria-hidden="true">
      {glyph}
    </span>
  )
}

export function Difficulty({ level }: { level: number }) {
  return (
    <span className="diff" title={['common', 'uncommon', 'rare'][level - 1] ?? 'unknown'}>
      {[1, 2, 3].map((step) => (
        <i key={step} className={step <= level ? 'on' : ''} />
      ))}
    </span>
  )
}

export function Empty({
  icon = '◇',
  title,
  children,
}: {
  icon?: string
  title: string
  children?: ReactNode
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      {children && <p style={{ fontSize: 13 }}>{children}</p>}
    </div>
  )
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="row gap-8 faint" style={{ padding: 20, justifyContent: 'center' }}>
      <span className="spinner" />
      {label && <span style={{ fontSize: 13 }}>{label}</span>}
    </div>
  )
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div className="banner" style={{ borderColor: 'rgba(248,113,113,.32)', background: 'rgba(248,113,113,.08)', color: '#fca5a5' }}>
      <span>⚠</span>
      <span>{message}</span>
    </div>
  )
}

/* ------------------------------------------------------------------ modal */

export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [onClose])

  return (
    <div className="overlay" onClick={onClose} role="presentation">
      <div
        className="modal"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-head">
          <div className="row-between">
            <h2>{title}</h2>
            <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ toasts */

const TOAST_ICONS = { info: 'ℹ', success: '✓', error: '⚠', bingo: '★' } as const

export function ToastStack() {
  const { toasts, dismiss } = useToast()
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast ${toast.kind}`} onClick={() => dismiss(toast.id)}>
          <span className="toast-icon">{TOAST_ICONS[toast.kind]}</span>
          <div className="grow">
            <div className="toast-title">{toast.title}</div>
            {toast.body && <div className="toast-body">{toast.body}</div>}
          </div>
        </div>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ helpers */

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const seconds = Math.round((Date.now() - then) / 1000)
  if (seconds < 45) return 'just now'
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 86_400) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86_400)}d ago`
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
