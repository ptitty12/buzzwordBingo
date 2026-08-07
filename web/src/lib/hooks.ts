/** Shared hooks: hash routing, the game WebSocket, and async data loading. */

import { useCallback, useEffect, useRef, useState } from 'react'

import { currentToken } from './api'
import type { GameEvent } from './types'

/* ------------------------------------------------------------------ routing */

/**
 * Minimal hash router. The app has six views and no need for nested routes or
 * loaders, so a 30-line hook beats a routing dependency.
 */
export function useRoute(): [string[], (path: string) => void] {
  const read = () => window.location.hash.replace(/^#\/?/, '')
  const [path, setPath] = useState(read)

  useEffect(() => {
    const onChange = () => setPath(read())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const navigate = useCallback((next: string) => {
    const clean = next.replace(/^#?\/?/, '')
    if (read() === clean) return
    window.location.hash = `/${clean}`
    window.scrollTo({ top: 0 })
  }, [])

  return [path.split('/').filter(Boolean), navigate]
}

/* ------------------------------------------------------------------ sockets */

export type SocketStatus = 'connecting' | 'open' | 'closed'

/**
 * Subscribe to a game's event stream.
 *
 * Reconnects with exponential backoff, and pings every 25s so intermediaries do not
 * reap an idle connection. The handler is held in a ref so callers can pass an inline
 * closure without forcing a reconnect on every render.
 */
export function useGameSocket(gameId: string | null, onEvent: (event: GameEvent) => void) {
  const [status, setStatus] = useState<SocketStatus>('closed')
  const handlerRef = useRef(onEvent)
  handlerRef.current = onEvent

  useEffect(() => {
    if (!gameId) {
      setStatus('closed')
      return
    }

    let socket: WebSocket | null = null
    let heartbeat: ReturnType<typeof setInterval> | undefined
    let retry: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let disposed = false

    const connect = () => {
      if (disposed) return
      setStatus('connecting')

      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const token = currentToken() ?? ''
      const url = `${protocol}://${window.location.host}/ws/games/${gameId}?token=${encodeURIComponent(token)}`

      socket = new WebSocket(url)

      socket.onopen = () => {
        if (disposed) return
        attempt = 0
        setStatus('open')
        heartbeat = setInterval(() => socket?.readyState === WebSocket.OPEN && socket.send('ping'), 25_000)
      }

      socket.onmessage = (message) => {
        try {
          handlerRef.current(JSON.parse(message.data) as GameEvent)
        } catch {
          /* ignore malformed frames rather than tearing down the stream */
        }
      }

      socket.onclose = () => {
        if (heartbeat) clearInterval(heartbeat)
        if (disposed) return
        setStatus('closed')
        attempt += 1
        retry = setTimeout(connect, Math.min(1000 * 2 ** attempt, 15_000))
      }

      socket.onerror = () => socket?.close()
    }

    connect()

    return () => {
      disposed = true
      if (heartbeat) clearInterval(heartbeat)
      if (retry) clearTimeout(retry)
      socket?.close()
    }
  }, [gameId])

  return status
}

/* ------------------------------------------------------------------ data */

export interface AsyncState<T> {
  data: T | undefined
  error: string | null
  loading: boolean
  reload: () => void
  setData: React.Dispatch<React.SetStateAction<T | undefined>>
}

/**
 * Load async data with loading/error state and a manual reload.
 *
 * `deps` controls refetching; the loader itself is intentionally not a dependency so
 * callers can pass inline arrow functions.
 */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    loaderRef
      .current()
      .then((result) => {
        if (cancelled) return
        setData(result)
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Something went wrong.')
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { data, error, loading, reload: () => setNonce((n) => n + 1), setData }
}

/** Debounce a rapidly-changing value (search boxes). */
export function useDebounced<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}
