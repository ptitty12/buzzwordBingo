/** Mirrors the Pydantic models in server/app/models.py. */

export type MeetingStatus = 'open' | 'live' | 'paused' | 'ended'

export type Accent = 'green' | 'teal' | 'cyan' | 'violet' | 'amber' | 'lime' | 'rose' | 'sky'

/** A participant exists only inside one meeting — there are no accounts. */
export interface Participant {
  id: string
  meeting_id: string
  nickname: string
  avatar: string
  accent: Accent
  created_at: string
  last_seen_at: string | null
}

export interface ParticipantSession {
  token: string
  participant: Participant
  meeting_id: string
}

export interface AdminSession {
  token: string
  is_admin: boolean
}

export interface Identity {
  is_admin: boolean
  participant: Participant | null
}

export interface AuthConfig {
  app_name: string
  environment: string
  admin_enabled: boolean
  suggestions_enabled: boolean
  moderation_enabled: boolean
}

export interface Word {
  id: string
  text: string
  category: string
  difficulty: number
  aliases: string[]
  strict_match: boolean
  active: boolean
  created_at: string
  created_by: string
  source: string
  usage_count: number
}

export interface Meeting {
  id: string
  name: string
  code: string
  status: MeetingStatus
  grid_size: number
  free_space: boolean
  description: string
  created_at: string
  started_at: string | null
  ended_at: string | null
  participant_count: number
  token_count: number
  completion_count: number
}

export interface GridCell {
  id: string
  position: number
  word_id: string | null
  text: string
  category: string
  is_free: boolean
  marked: boolean
  marked_at: string | null
}

export interface Grid {
  id: string
  meeting_id: string
  participant_id: string
  nickname: string
  avatar: string
  accent: Accent
  grid_size: number
  locked: boolean
  created_at: string
  cells: GridCell[]
  marked_count: number
  lines: string[]
  best_rank: number | null
}

export interface StandingsEntry {
  position: number
  participant_id: string
  grid_id: string
  nickname: string
  avatar: string
  accent: Accent
  marked: number
  total: number
  lines: number
  first_completion_at: string | null
  best_rank: number | null
}

export interface TranscriptToken {
  id: string
  seq: number
  raw: string
  speaker: string
  source: string
  hit_count: number
  created_at: string
}

/** A participant's proposed buzzword and the judge's verdict. */
export interface WordSuggestion {
  id: string
  text: string
  canonical: string
  status: 'pending' | 'approved' | 'rejected'
  verdict: string
  category: string
  difficulty: number
  judged_by: string
  participant_name: string
  word_id: string | null
  created_at: string
  decided_at: string | null
}

export interface SuggestionResponse {
  suggestion: WordSuggestion
  word: Word | null
  remaining: number
}

export interface ApiKey {
  id: string
  name: string
  prefix: string
  active: boolean
  created_at: string
  last_used_at: string | null
  call_count: number
  key?: string
}

export interface AuditEntry {
  id: string
  actor_name: string
  action: string
  entity: string
  entity_id: string
  detail: string
  created_at: string
}

export interface AdminStats {
  participants: number
  words: number
  active_words: number
  meetings: number
  live_meetings: number
  grids: number
  tokens: number
  completions: number
  connected_sockets: number
  pending_suggestions: number
  moderation_enabled: boolean
  moderation_model: string
  environment: string
}

export interface IngestHit {
  participant_id: string
  nickname: string
  grid_id: string
  position: number
  word: string
  matched_phrase: string
}

/** Events pushed over the meeting WebSocket. */
export type MeetingEvent =
  | { event: 'hello'; payload: { meeting: Meeting; standings: StandingsEntry[]; viewers: number } }
  | {
      event: 'token'
      payload: {
        tokens: { raw: string; seq: number; hits: number }[]
        speaker: string
        source: string
      }
    }
  | { event: 'marks'; payload: IngestHit[] }
  | {
      event: 'completion'
      payload: {
        participant_id: string
        nickname: string
        pattern: string
        label: string
        rank: number
        cells: number[]
        achieved_at: string
      }
    }
  | { event: 'meeting'; payload: Meeting }
  | { event: 'roster'; payload: { participant_id: string; nickname: string; grid_id?: string } }
  | { event: 'standings'; payload: StandingsEntry[] }
