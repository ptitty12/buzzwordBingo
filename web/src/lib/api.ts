/**
 * Typed API client.
 *
 * Credentials are unusual here and the store reflects that: there is one admin token
 * (earned with the PIN) and a *separate token per meeting* for the participant identities you
 * hold. Requests carry whichever token the current view activated, so a participant token
 * can never leak into an admin call or into another meeting's requests.
 */

import type {
  AdminSession,
  AdminStats,
  ApiKey,
  AuditEntry,
  AuthConfig,
  Grid,
  Meeting,
  Identity,
  StandingsEntry,
  ParticipantSession,
  SuggestionResponse,
  TranscriptToken,
  Word,
  WordSuggestion,
} from './types'

const ADMIN_KEY = 'jw.admin'
const PARTICIPANTS_KEY = 'jw.participants'

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
  participantToken(meetingId: string): string | null {
    return readJson<Record<string, string>>(PARTICIPANTS_KEY, {})[meetingId] ?? null
  },
  setParticipantToken(meetingId: string, token: string | null): void {
    const all = readJson<Record<string, string>>(PARTICIPANTS_KEY, {})
    if (token) all[meetingId] = token
    else delete all[meetingId]
    writeJson(PARTICIPANTS_KEY, all)
  },
  clearAll(): void {
    try {
      localStorage.removeItem(ADMIN_KEY)
      localStorage.removeItem(PARTICIPANTS_KEY)
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

export function activateParticipant(meetingId: string): void {
  // Admins browsing a meeting keep their admin token — it outranks a participant token and
  // is what the admin-only endpoints on that screen require.
  activeToken = auth.adminToken() ?? auth.participantToken(meetingId)
}

export function activateNone(): void {
  activeToken = null
}

export function currentToken(): string | null {
  return activeToken
}

/**
 * Called when the server rejects the credential we just sent. A dead token is worse
 * than no token: the UI keeps rendering as if you were signed in and every action
 * fails. So we drop it at the source and let the app re-ask for an identity.
 */
type ExpiryListener = (scope: 'admin' | 'participant') => void
let onExpired: ExpiryListener | null = null

export function setExpiryHandler(handler: ExpiryListener | null): void {
  onExpired = handler
}

function discardActiveToken(): void {
  if (!activeToken) return
  const dead = activeToken
  activeToken = null

  if (auth.adminToken() === dead) {
    auth.setAdminToken(null)
    onExpired?.('admin')
    return
  }
  const participants = readJson<Record<string, string>>(PARTICIPANTS_KEY, {})
  const meetingId = Object.keys(participants).find((id) => participants[id] === dead)
  if (meetingId) {
    auth.setParticipantToken(meetingId, null)
    onExpired?.('participant')
  }
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
    // 401 means the token itself is no longer good — expired, revoked, or signed with
    // a key this server no longer has. Binning it here turns a permanent dead end into
    // one trip back through the nickname (or PIN) screen.
    if (response.status === 401) discardActiveToken()
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

  // -------------------------------------------------------------- meetings (public)
  meetings: (status?: string) => get<Meeting[]>(`/api/meetings${qs({ status })}`),
  meeting: (id: string) => get<Meeting>(`/api/meetings/${id}`),
  join: (meetingId: string, body: { nickname: string; avatar?: string; accent?: string }) =>
    post<ParticipantSession>(`/api/meetings/${meetingId}/join`, body),

  // -------------------------------------------------------------- meetings (admin)
  createMeeting: (body: {
    name: string
    description?: string
    grid_size?: number
    free_space?: boolean
  }) => post<Meeting>('/api/meetings', body),
  setMeetingStatus: (id: string, status: string) => patch<Meeting>(`/api/meetings/${id}/status`, { status }),
  resetMeeting: (id: string) => post<Meeting>(`/api/meetings/${id}/reset`),
  deleteMeeting: (id: string) => del(`/api/meetings/${id}`),

  // -------------------------------------------------------------- grids
  myGrid: (meetingId: string) => get<Grid>(`/api/meetings/${meetingId}/grid`),
  buildGrid: (meetingId: string, wordIds: string[]) =>
    post<Grid>(`/api/meetings/${meetingId}/grid`, { word_ids: wordIds }),
  meetingGrids: (meetingId: string) => get<Grid[]>(`/api/meetings/${meetingId}/grids`),
  standings: (meetingId: string) => get<StandingsEntry[]>(`/api/meetings/${meetingId}/standings`),
  transcript: (meetingId: string, limit = 120) =>
    get<TranscriptToken[]>(`/api/meetings/${meetingId}/transcript${qs({ limit })}`),

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
  participants: (meetingId?: string) =>
    get<import('./types').Participant[]>(`/api/admin/participants${qs({ meeting_id: meetingId })}`),
  removeParticipant: (id: string) => del(`/api/admin/participants/${id}`),
  suggestions: (status?: string) =>
    get<WordSuggestion[]>(`/api/admin/suggestions${qs({ status })}`),
  decideSuggestion: (id: string, approve: boolean, reason = '') =>
    post<WordSuggestion>(`/api/admin/suggestions/${id}`, { approve, reason }),
  allGrids: (meetingId?: string) => get<Grid[]>(`/api/admin/grids${qs({ meeting_id: meetingId })}`),
  keys: () => get<ApiKey[]>('/api/admin/keys'),
  createKey: (name: string) => post<ApiKey>('/api/admin/keys', { name }),
  revokeKey: (id: string) => del(`/api/admin/keys/${id}`),
  audit: (limit = 80) => get<AuditEntry[]>(`/api/admin/audit${qs({ limit })}`),

  // -------------------------------------------------------------- ingest
  /** Admin test console — posts transcript through the same path a vendor would use. */
  ingest: (
    body: { text: string; meeting_id?: string; speaker?: string; source?: string },
    apiKey: string,
  ) =>
    request<{ token_count: number; results: unknown[] }>('/api/ingest', {
      method: 'POST',
      body: JSON.stringify(body),
      headers: { 'X-API-Key': apiKey },
    }),
}
