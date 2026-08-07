/** App-wide context: the signed-in player and the toast queue. */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import { api, getToken, setToken } from './api'
import type { Session, User } from './types'

/* ------------------------------------------------------------------ toasts */

export interface Toast {
  id: number
  kind: 'info' | 'success' | 'error' | 'bingo'
  title: string
  body?: string
}

interface ToastApi {
  toasts: Toast[]
  push: (toast: Omit<Toast, 'id'>) => void
  dismiss: (id: number) => void
}

const ToastContext = createContext<ToastApi | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
    const timer = timers.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timers.current.delete(id)
    }
  }, [])

  const push = useCallback(
    (toast: Omit<Toast, 'id'>) => {
      const id = nextId.current++
      setToasts((current) => [...current.slice(-4), { ...toast, id }])
      const lifetime = toast.kind === 'bingo' ? 6500 : toast.kind === 'error' ? 6000 : 4000
      timers.current.set(id, setTimeout(() => dismiss(id), lifetime))
    },
    [dismiss],
  )

  useEffect(() => {
    const pending = timers.current
    return () => pending.forEach(clearTimeout)
  }, [])

  const value = useMemo(() => ({ toasts, push, dismiss }), [toasts, push, dismiss])
  return <ToastContext.Provider value={value}>{children}</ToastContext.Provider>
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside <ToastProvider>')
  return context
}

/* ------------------------------------------------------------------ session */

interface SessionApi {
  user: User | null
  ready: boolean
  signIn: (session: Session) => void
  signOut: () => void
  refresh: () => Promise<void>
}

const SessionContext = createContext<SessionApi | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  // Restore a stored token on boot; a rejected token is discarded silently.
  useEffect(() => {
    if (!getToken()) {
      setReady(true)
      return
    }
    api
      .me()
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setReady(true))
  }, [])

  const signIn = useCallback((session: Session) => {
    setToken(session.token)
    setUser(session.user)
  }, [])

  const signOut = useCallback(() => {
    setToken(null)
    setUser(null)
    window.location.hash = '/'
  }, [])

  const refresh = useCallback(async () => {
    try {
      setUser(await api.me())
    } catch {
      setToken(null)
      setUser(null)
    }
  }, [])

  const value = useMemo(() => ({ user, ready, signIn, signOut, refresh }), [user, ready, signIn, signOut, refresh])
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionApi {
  const context = useContext(SessionContext)
  if (!context) throw new Error('useSession must be used inside <SessionProvider>')
  return context
}
