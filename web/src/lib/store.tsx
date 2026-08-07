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

import { activateAdmin, activateNone, activatePlayer, api, auth } from './api'
import type { Player } from './types'

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
  /** True when an admin PIN has been exchanged for a token. */
  isAdmin: boolean
  /** The player identity for the game currently being viewed, if any. */
  player: Player | null
  ready: boolean
  signInAdmin: (token: string) => void
  signOutAdmin: () => void
  /** Record a freshly-joined player and make them the active identity. */
  joinedGame: (gameId: string, token: string, player: Player) => void
  /** Point the API client at a game (player token, or admin token if you hold one). */
  enterGame: (gameId: string) => Promise<void>
  leaveContext: () => void
  hasPlayerToken: (gameId: string) => boolean
}

const SessionContext = createContext<SessionApi | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [isAdmin, setIsAdmin] = useState(false)
  const [player, setPlayer] = useState<Player | null>(null)
  const [ready, setReady] = useState(false)

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

  const joinedGame = useCallback((gameId: string, token: string, joined: Player) => {
    auth.setPlayerToken(gameId, token)
    activatePlayer(gameId)
    setPlayer(joined)
  }, [])

  const enterGame = useCallback(async (gameId: string) => {
    activatePlayer(gameId)
    if (!auth.playerToken(gameId)) {
      // Admins have no player identity of their own; that is expected, not an error.
      setPlayer(null)
      return
    }
    try {
      const identity = await api.me()
      setPlayer(identity.player)
    } catch {
      auth.setPlayerToken(gameId, null)
      setPlayer(null)
    }
  }, [])

  const leaveContext = useCallback(() => {
    setPlayer(null)
    if (auth.adminToken()) activateAdmin()
    else activateNone()
  }, [])

  const hasPlayerToken = useCallback((gameId: string) => Boolean(auth.playerToken(gameId)), [])

  const value = useMemo(
    () => ({
      isAdmin,
      player,
      ready,
      signInAdmin,
      signOutAdmin,
      joinedGame,
      enterGame,
      leaveContext,
      hasPlayerToken,
    }),
    [
      isAdmin,
      player,
      ready,
      signInAdmin,
      signOutAdmin,
      joinedGame,
      enterGame,
      leaveContext,
      hasPlayerToken,
    ],
  )
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionApi {
  const context = useContext(SessionContext)
  if (!context) throw new Error('useSession must be used inside <SessionProvider>')
  return context
}
