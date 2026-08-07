/**
 * Typed API client.
 *
 * One module owns the base URL, the bearer token and error shaping, so views never
 * touch fetch directly and every failure surfaces as a readable ApiError.
 */

import type {
  AdminStats,
  ApiKey,
  AuditEntry,
  AuthConfig,
  BingoRecord,
  Card,
  Game,
  LeaderboardEntry,
  Session,
  TranscriptToken,
  User,
  UserSummary,
  Word,
} from './types'

const TOKEN_KEY = 'bb.token'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable (private mode) — session simply won't persist */
  }
}

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
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

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
  authConfig: () => get<AuthConfig>('/api/auth/config'),
  listAccounts: () => get<UserSummary[]>('/api/auth/users'),
  signUp: (body: { nickname: string; avatar?: string; accent?: string }) =>
    post<Session>('/api/auth/signup', body),
  signIn: (body: { user_id: string; admin_pin?: string }) => post<Session>('/api/auth/signin', body),
  me: () => get<User>('/api/auth/me'),

  // -------------------------------------------------------------- words
  words: (params: { include_inactive?: boolean; category?: string; search?: string } = {}) =>
    get<Word[]>(`/api/words${qs(params)}`),
  categories: () => get<string[]>('/api/words/categories'),
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

  // -------------------------------------------------------------- games
  games: (status?: string) => get<Game[]>(`/api/games${qs({ status })}`),
  game: (id: string) => get<Game>(`/api/games/${id}`),
  createGame: (body: { name: string; description?: string; card_size?: number; free_space?: boolean }) =>
    post<Game>('/api/games', body),
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
  bingos: (gameId: string) => get<BingoRecord[]>(`/api/games/${gameId}/bingos`),

  // -------------------------------------------------------------- admin
  stats: () => get<AdminStats>('/api/admin/stats'),
  adminUsers: () => get<User[]>('/api/admin/users'),
  updateUser: (id: string, body: { is_admin?: boolean; nickname?: string }) =>
    patch<User>(`/api/admin/users/${id}`, body),
  deleteUser: (id: string) => del(`/api/admin/users/${id}`),
  allCards: (gameId?: string) => get<Card[]>(`/api/admin/cards${qs({ game_id: gameId })}`),
  keys: () => get<ApiKey[]>('/api/admin/keys'),
  createKey: (name: string) => post<ApiKey>('/api/admin/keys', { name }),
  revokeKey: (id: string) => del(`/api/admin/keys/${id}`),
  audit: (limit = 80) => get<AuditEntry[]>(`/api/admin/audit${qs({ limit })}`),

  // -------------------------------------------------------------- ingest
  /** Admin test console — posts transcript through the same path a vendor would use. */
  ingest: (body: { text: string; game_id?: string; speaker?: string; source?: string }, apiKey: string) =>
    request<{ token_count: number; results: unknown[] }>('/api/ingest', {
      method: 'POST',
      body: JSON.stringify(body),
      headers: { 'X-API-Key': apiKey },
    }),
}
