/** Mirrors the Pydantic models in server/app/models.py. */

export type GameStatus = 'lobby' | 'live' | 'paused' | 'ended'

export type Accent = 'green' | 'teal' | 'cyan' | 'violet' | 'amber' | 'lime' | 'rose' | 'sky'

export interface User {
  id: string
  nickname: string
  avatar: string
  accent: Accent
  is_admin: boolean
  created_at: string
  last_seen_at: string | null
}

export interface UserSummary {
  id: string
  nickname: string
  avatar: string
  accent: Accent
  is_admin: boolean
  games_played: number
}

export interface Session {
  token: string
  user: User
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
  user_id: string
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
  user_id: string
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

export interface BingoRecord {
  id: string
  user_id: string
  nickname: string
  avatar: string
  accent: Accent
  pattern: string
  label: string
  rank: number
  achieved_at: string
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
  users: number
  admins: number
  words: number
  active_words: number
  games: number
  live_games: number
  cards: number
  tokens: number
  bingos: number
  connected_sockets: number
  admin_pin_set: boolean
  environment: string
}

export interface AuthConfig {
  admin_pin_required: boolean
  environment: string
  app_name: string
}

export interface IngestHit {
  user_id: string
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
  | { event: 'bingo'; payload: { user_id: string; nickname: string; pattern: string; label: string; rank: number; cells: number[]; achieved_at: string } }
  | { event: 'game'; payload: Game }
  | { event: 'roster'; payload: { user_id: string; nickname: string; card_id: string } }
  | { event: 'leaderboard'; payload: LeaderboardEntry[] }
