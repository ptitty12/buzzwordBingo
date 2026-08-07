/** Mirrors the Pydantic models in server/app/models.py. */

export type GameStatus = 'lobby' | 'live' | 'paused' | 'ended'

export type Accent = 'green' | 'teal' | 'cyan' | 'violet' | 'amber' | 'lime' | 'rose' | 'sky'

/** A player exists only inside one game — there are no accounts. */
export interface Player {
  id: string
  game_id: string
  nickname: string
  avatar: string
  accent: Accent
  created_at: string
  last_seen_at: string | null
}

export interface PlayerSession {
  token: string
  player: Player
  game_id: string
}

export interface AdminSession {
  token: string
  is_admin: boolean
}

export interface Identity {
  is_admin: boolean
  player: Player | null
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

export interface Game {
  id: string
  name: string
  code: string
  status: GameStatus
  card_size: number
  free_space: boolean
  description: string
  created_at: string
  started_at: string | null
  ended_at: string | null
  player_count: number
  token_count: number
  bingo_count: number
}

export interface CardCell {
  id: string
  position: number
  word_id: string | null
  text: string
  category: string
  is_free: boolean
  marked: boolean
  marked_at: string | null
}

export interface Card {
  id: string
  game_id: string
  player_id: string
  nickname: string
  avatar: string
  accent: Accent
  card_size: number
  locked: boolean
  created_at: string
  cells: CardCell[]
  marked_count: number
  lines: string[]
  best_rank: number | null
}

export interface LeaderboardEntry {
  position: number
  player_id: string
  card_id: string
  nickname: string
  avatar: string
  accent: Accent
  marked: number
  total: number
  lines: number
  first_bingo_at: string | null
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

/** A player's proposed buzzword and the judge's verdict. */
export interface WordSuggestion {
  id: string
  text: string
  canonical: string
  status: 'pending' | 'approved' | 'rejected'
  verdict: string
  category: string
  difficulty: number
  judged_by: string
  player_name: string
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
  players: number
  words: number
  active_words: number
  games: number
  live_games: number
  cards: number
  tokens: number
  bingos: number
  connected_sockets: number
  pending_suggestions: number
  moderation_enabled: boolean
  moderation_model: string
  environment: string
}

export interface IngestHit {
  player_id: string
  nickname: string
  card_id: string
  position: number
  word: string
  matched_phrase: string
}

/** Events pushed over the game WebSocket. */
export type GameEvent =
  | { event: 'hello'; payload: { game: Game; leaderboard: LeaderboardEntry[]; viewers: number } }
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
      event: 'bingo'
      payload: {
        player_id: string
        nickname: string
        pattern: string
        label: string
        rank: number
        cells: number[]
        achieved_at: string
      }
    }
  | { event: 'game'; payload: Game }
  | { event: 'roster'; payload: { player_id: string; nickname: string; card_id?: string } }
  | { event: 'leaderboard'; payload: LeaderboardEntry[] }
