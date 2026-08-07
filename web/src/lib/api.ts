/**
 * Typed API client.
 *
 * Credentials are unusual here and the store reflects that: there is one admin token
 * (earned with the PIN) and a *separate token per game* for the player identities you
 * hold. Requests carry whichever token the current view activated, so a player token
 * can never leak into an admin call or into another game's requests.
 */

import type {
  AdminSession,
  AdminStats,
  ApiKey,
  AuditEntry,
  AuthConfig,
  Card,
  Game,
  Identity,
  LeaderboardEntry,
  PlayerSession,
  SuggestionResponse,
  TranscriptToken,
  Word,
  WordSuggestion,
} from './types'

const ADMIN_KEY = 'bb.admin'
const PLAYERS_KEY = 'bb.players'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/* ------------------------------------------------------------------ credential store */

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* storage unavailable (private mode) — the session simply won't persist */
  }
}

export const auth = {
  adminToken(): string | null {
    try {
      return localStorage.getItem(ADMIN_KEY)
    } catch {
      return null
    }
  },
  setAdminToken(token: string | null): void {
    try {
      if (token) localStorage.setItem(ADMIN_KEY, token)
      else localStorage.removeItem(ADMIN_KEY)
    } catch {
      /* ignore */
    }
  },
  playerToken(gameId: string): string | null {
    return readJson<Record<string, string>>(PLAYERS_KEY, {})[gameId] ?? null
  },
  setPlayerToken(gameId: string, token: string | null): void {
    const all = readJson<Record<string, string>>(PLAYERS_KEY, {})
    if (token) all[gameId] = token
    else delete all[gameId]
    writeJson(PLAYERS_KEY, all)
  },
  clearAll(): void {
    try {
      localStorage.removeItem(ADMIN_KEY)
      localStorage.removeItem(PLAYERS_KEY)
    } catch {
      /* ignore */
    }
  },
}

/**
 * The token attached to outgoing requests. Views call `activate*` when they mount, so
 * the credential in play always matches the screen you are looking at.
 */
let activeToken: string | null = null

export function activateAdmin(): void {
  activeToken = auth.adminToken()
}

export function activatePlayer(gameId: string): void {
  // Admins browsing a game keep their admin token — it outranks a player token and
  // is what the admin-only endpoints on that screen require.
  activeToken = auth.adminToken() ?? auth.playerToken(gameId)
}

export function activateNone(): void {
  activeToken = null
}

export function currentToken(): string | null {
  return activeToken
}

/* ------------------------------------------------------------------ transport */

/** FastAPI returns `detail` as a string, or a list of validation objects. */
function readDetail(payload: unknown, fallback: string): string {
  if (typeof payload === 'string' && payload) return payload
  if (payload && typeof payload === 'object') {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string; loc?: unknown[] }
      const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : ''
      return field ? `${field}: ${first.msg ?? 'invalid'}` : (first.msg ?? fallback)
    }
  }
  return fallback
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (activeToken) headers.set('Authorization', `Bearer ${activeToken}`)

  let response: Response
  try {
    response = await fetch(path, { ...init, headers })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Is the API running?')
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, readDetail(payload, `Request failed (${response.status})`))
  }
  return payload as T
}

const get = <T,>(path: string) => request<T>(path)
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const patch = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
const del = (path: string) => request<void>(path, { method: 'DELETE' })

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const api = {
  // -------------------------------------------------------------- auth
  config: () => get<AuthConfig>('/api/auth/config'),
  adminSignIn: (pin: string) => post<AdminSession>('/api/auth/admin', { pin }),
  me: () => get<Identity>('/api/auth/me'),

  // -------------------------------------------------------------- games (public)
  games: (status?: string) => get<Game[]>(`/api/games${qs({ status })}`),
  game: (id: string) => get<Game>(`/api/games/${id}`),
  join: (gameId: string, body: { nickname: string; avatar?: string; accent?: string }) =>
    post<PlayerSession>(`/api/games/${gameId}/join`, body),

  // -------------------------------------------------------------- games (admin)
  createGame: (body: {
    name: string
    description?: string
    card_size?: number
    free_space?: boolean
  }) => post<Game>('/api/games', body),
  setGameStatus: (id: string, status: string) => patch<Game>(`/api/games/${id}/status`, { status }),
  resetGame: (id: string) => post<Game>(`/api/games/${id}/reset`),
  deleteGame: (id: string) => del(`/api/games/${id}`),

  // -------------------------------------------------------------- cards
  myCard: (gameId: string) => get<Card>(`/api/games/${gameId}/card`),
  buildCard: (gameId: string, wordIds: string[]) =>
    post<Card>(`/api/games/${gameId}/card`, { word_ids: wordIds }),
  gameCards: (gameId: string) => get<Card[]>(`/api/games/${gameId}/cards`),
  leaderboard: (gameId: string) => get<LeaderboardEntry[]>(`/api/games/${gameId}/leaderboard`),
  transcript: (gameId: string, limit = 120) =>
    get<TranscriptToken[]>(`/api/games/${gameId}/transcript${qs({ limit })}`),

  // -------------------------------------------------------------- words
  words: (params: { include_inactive?: boolean; category?: string; search?: string } = {}) =>
    get<Word[]>(`/api/words${qs(params)}`),
  categories: () => get<string[]>('/api/words/categories'),
  suggestWord: (text: string) => post<SuggestionResponse>('/api/words/suggest', { text }),
  mySuggestions: () =>
    get<{ suggestions: WordSuggestion[]; remaining: number; limit: number }>(
      '/api/words/suggestions/mine',
    ),
  createWord: (body: Partial<Word> & { text: string }) => post<Word>('/api/words', body),
  bulkWords: (body: { payload: string; category?: string; difficulty?: number }) =>
    post<Word[]>('/api/words/bulk', body),
  updateWord: (id: string, body: Partial<Word>) => patch<Word>(`/api/words/${id}`, body),
  deleteWord: (id: string) => del(`/api/words/${id}`),
  inspect: (phrase: string, against?: string) =>
    get<{
      phrase: string
      tokens: string[]
      match_keys: string[]
      exact_key: string
      against?: { phrase: string; match_keys: string[]; matches: boolean }
    }>(`/api/words/inspect${qs({ phrase, against })}`),

  // -------------------------------------------------------------- admin
  stats: () => get<AdminStats>('/api/admin/stats'),
  players: (gameId?: string) =>
    get<import('./types').Player[]>(`/api/admin/players${qs({ game_id: gameId })}`),
  removePlayer: (id: string) => del(`/api/admin/players/${id}`),
  suggestions: (status?: string) =>
    get<WordSuggestion[]>(`/api/admin/suggestions${qs({ status })}`),
  decideSuggestion: (id: string, approve: boolean, reason = '') =>
    post<WordSuggestion>(`/api/admin/suggestions/${id}`, { approve, reason }),
  allCards: (gameId?: string) => get<Card[]>(`/api/admin/cards${qs({ game_id: gameId })}`),
  keys: () => get<ApiKey[]>('/api/admin/keys'),
  createKey: (name: string) => post<ApiKey>('/api/admin/keys', { name }),
  revokeKey: (id: string) => del(`/api/admin/keys/${id}`),
  audit: (limit = 80) => get<AuditEntry[]>(`/api/admin/audit${qs({ limit })}`),

  // -------------------------------------------------------------- ingest
  /** Admin test console — posts transcript through the same path a vendor would use. */
  ingest: (
    body: { text: string; game_id?: string; speaker?: string; source?: string },
    apiKey: string,
  ) =>
    request<{ token_count: number; results: unknown[] }>('/api/ingest', {
      method: 'POST',
      body: JSON.stringify(body),
      headers: { 'X-API-Key': apiKey },
    }),
}
