/** App-wide context: who you are right now, and the toast queue. */

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

import { activateAdmin, activateNone, activateParticipant, api, auth, setExpiryHandler } from './api'
import type { Participant } from './types'

/* ------------------------------------------------------------------ toasts */

export interface Toast {
  id: number
  kind: 'info' | 'success' | 'error' | 'completion'
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
      const lifetime = toast.kind === 'completion' ? 6500 : toast.kind === 'error' ? 6000 : 4000
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
  /** True when an admin PIN has been exchanged for a token. */
  isAdmin: boolean
  /** The participant identity for the meeting currently being viewed, if any. */
  participant: Participant | null
  ready: boolean
  /** Set when the server rejected a stored token, so the UI can explain the eviction. */
  expired: 'admin' | 'participant' | null
  signInAdmin: (token: string) => void
  signOutAdmin: () => void
  /** Record a freshly-joined participant and make them the active identity. */
  joinedMeeting: (meetingId: string, token: string, participant: Participant) => void
  /** Point the API client at a meeting (participant token, or admin token if you hold one). */
  enterMeeting: (meetingId: string) => Promise<void>
  leaveContext: () => void
  hasParticipantToken: (meetingId: string) => boolean
  /** Acknowledge an expiry notice once it has been shown. */
  clearExpiry: () => void
}

const SessionContext = createContext<SessionApi | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [isAdmin, setIsAdmin] = useState(false)
  const [participant, setParticipant] = useState<Participant | null>(null)
  const [ready, setReady] = useState(false)
  const [expired, setExpired] = useState<'admin' | 'participant' | null>(null)

  // The API client bins a token the server rejects; mirror that into React state so
  // the screen stops pretending the identity is still good.
  useEffect(() => {
    setExpiryHandler((scope) => {
      if (scope === 'admin') setIsAdmin(false)
      else setParticipant(null)
      setExpired(scope)
    })
    return () => setExpiryHandler(null)
  }, [])

  // Validate any stored admin token once on boot; a rejected token is discarded.
  useEffect(() => {
    const stored = auth.adminToken()
    if (!stored) {
      setReady(true)
      return
    }
    activateAdmin()
    api
      .me()
      .then((identity) => setIsAdmin(identity.is_admin))
      .catch(() => auth.setAdminToken(null))
      .finally(() => setReady(true))
  }, [])

  const signInAdmin = useCallback((token: string) => {
    auth.setAdminToken(token)
    activateAdmin()
    setIsAdmin(true)
  }, [])

  const signOutAdmin = useCallback(() => {
    auth.setAdminToken(null)
    activateNone()
    setIsAdmin(false)
    window.location.hash = '/'
  }, [])

  const joinedMeeting = useCallback((meetingId: string, token: string, joined: Participant) => {
    auth.setParticipantToken(meetingId, token)
    activateParticipant(meetingId)
    setParticipant(joined)
  }, [])

  const enterMeeting = useCallback(async (meetingId: string) => {
    activateParticipant(meetingId)
    if (!auth.participantToken(meetingId)) {
      // Admins have no participant identity of their own; that is expected, not an error.
      setParticipant(null)
      return
    }
    try {
      const identity = await api.me()
      setParticipant(identity.participant)
    } catch {
      auth.setParticipantToken(meetingId, null)
      setParticipant(null)
    }
  }, [])

  const leaveContext = useCallback(() => {
    setParticipant(null)
    if (auth.adminToken()) activateAdmin()
    else activateNone()
  }, [])

  const hasParticipantToken = useCallback((meetingId: string) => Boolean(auth.participantToken(meetingId)), [])

  const clearExpiry = useCallback(() => setExpired(null), [])

  const value = useMemo(
    () => ({
      isAdmin,
      participant,
      ready,
      expired,
      signInAdmin,
      signOutAdmin,
      joinedMeeting,
      enterMeeting,
      leaveContext,
      hasParticipantToken,
      clearExpiry,
    }),
    [
      isAdmin,
      participant,
      ready,
      expired,
      signInAdmin,
      signOutAdmin,
      joinedMeeting,
      enterMeeting,
      leaveContext,
      hasParticipantToken,
      clearExpiry,
    ],
  )
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionApi {
  const context = useContext(SessionContext)
  if (!context) throw new Error('useSession must be used inside <SessionProvider>')
  return context
}
