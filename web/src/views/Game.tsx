/**
 * The game room.
 *
 * Two modes share one screen: players without a card get the drafting board, players
 * with one get the live card, ticker and standings. Everything after the initial load
 * is driven by the WebSocket, so the view never polls.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api } from '../lib/api'
import { useAsync, useDebounced, useGameSocket } from '../lib/hooks'
import { useSession, useToast } from '../lib/store'
import { BingoGrid, Celebration, Leaderboard, Ticker } from '../components/BingoCard'
import { SuggestWordButton } from '../components/SuggestWord'
import {
  Difficulty,
  Empty,
  ErrorNote,
  Panel,
  Spinner,
  StatusBadge,
  copyToClipboard,
} from '../components/ui'
import type {
  Card,
  GameEvent,
  LeaderboardEntry,
  TranscriptToken,
  Word,
} from '../lib/types'

const MAX_TICKER_TOKENS = 260

export function GameRoom({ gameId, navigate }: { gameId: string; navigate: (path: string) => void }) {
  const { player, isAdmin } = useSession()
  const { push } = useToast()
  const myPlayerId = player?.id

  const game = useAsync(() => api.game(gameId), [gameId])
  const [card, setCard] = useState<Card | null>(null)
  const [cardMissing, setCardMissing] = useState(false)
  const [cardError, setCardError] = useState<string | null>(null)
  const [tokens, setTokens] = useState<TranscriptToken[]>([])
  const [board, setBoard] = useState<LeaderboardEntry[]>([])
  const [viewers, setViewers] = useState(0)
  const [celebration, setCelebration] = useState<{ label: string; who: string } | null>(null)

  const gameData = game.data
  // Admins browse every game but play in none of them.
  const spectating = isAdmin && !myPlayerId
  const cardRef = useRef<Card | null>(null)
  cardRef.current = card

  // Initial load: card (may legitimately 404), transcript backfill, standings.
  // An admin watching a game holds no player identity and therefore has no card —
  // that is spectating, not an error, so we do not even ask for one.
  useEffect(() => {
    let cancelled = false
    setCard(null)
    setCardMissing(false)

    if (!spectating) {
      api
        .myCard(gameId)
        .then((result) => !cancelled && setCard(result))
        .catch((err: unknown) => {
          if (cancelled) return
          const status = (err as { status?: number }).status
          if (status === 404) setCardMissing(true)
          else setCardError(err instanceof Error ? err.message : 'Could not load your card.')
        })
    }

    api.transcript(gameId).then((result) => !cancelled && setTokens(result)).catch(() => undefined)
    api.leaderboard(gameId).then((result) => !cancelled && setBoard(result)).catch(() => undefined)

    return () => {
      cancelled = true
    }
  }, [gameId, spectating])

  const refreshCard = useCallback(() => {
    api
      .myCard(gameId)
      .then(setCard)
      .catch(() => undefined)
  }, [gameId])

  const onEvent = useCallback(
    (event: GameEvent) => {
      switch (event.event) {
        case 'hello':
          setBoard(event.payload.leaderboard)
          setViewers(event.payload.viewers)
          game.setData(event.payload.game)
          break

        case 'game':
          game.setData(event.payload)
          if (event.payload.status === 'live') {
            push({ kind: 'info', title: 'The meeting is live', body: 'Cards are locked. Good luck.' })
            refreshCard()
          }
          break

        case 'token': {
          // The server attributes hits per token, so the ticker highlights exactly the
          // word that scored — including the last word of a multi-word phrase.
          const timestamp = new Date().toISOString()
          setTokens((current) => {
            const additions = event.payload.tokens.map((token) => ({
              id: `t-${token.seq}`,
              seq: token.seq,
              raw: token.raw,
              speaker: event.payload.speaker,
              source: event.payload.source,
              hit_count: token.hits,
              created_at: timestamp,
            }))
            return [...current, ...additions].slice(-MAX_TICKER_TOKENS)
          })
          break
        }

        case 'marks': {
          const mine = event.payload.filter((hit) => hit.player_id === myPlayerId)
          if (mine.length === 0) break
          setCard((current) => {
            if (!current) return current
            const positions = new Set(mine.map((hit) => hit.position))
            return {
              ...current,
              marked_count: current.marked_count + mine.length,
              cells: current.cells.map((cell) =>
                positions.has(cell.position)
                  ? { ...cell, marked: true, marked_at: new Date().toISOString() }
                  : cell,
              ),
            }
          })
          push({
            kind: 'success',
            title: mine.length === 1 ? `“${mine[0].word}”` : `${mine.length} squares marked`,
            body: mine.length === 1 ? 'Square marked.' : mine.map((hit) => hit.word).join(', '),
          })
          break
        }

        case 'bingo': {
          const isMe = event.payload.player_id === myPlayerId
          if (isMe) {
            setCard((current) =>
              current ? { ...current, lines: [...new Set([...current.lines, event.payload.pattern])] } : current,
            )
            setCelebration({ label: event.payload.label, who: 'You' })
            setTimeout(() => setCelebration(null), 3600)
          }
          push({
            kind: 'bingo',
            title: `${isMe ? 'BINGO! You' : `${event.payload.nickname} got bingo`} — ${event.payload.label}`,
            body: `#${event.payload.rank} to complete a line.`,
          })
          break
        }

        case 'leaderboard':
          setBoard(event.payload)
          break

        case 'roster':
          if (event.payload.player_id !== myPlayerId) {
            push({ kind: 'info', title: `${event.payload.nickname} joined` })
          }
          break
      }
    },
    [game, push, refreshCard, myPlayerId],
  )

  const socket = useGameSocket(gameId, onEvent)

  if (game.loading) return <div className="page"><Spinner label="Loading game…" /></div>
  if (game.error || !gameData) {
    return (
      <div className="page">
        <ErrorNote message={game.error ?? 'Game not found.'} />
        <button className="btn" style={{ marginTop: 14 }} onClick={() => navigate('')}>
          ← All games
        </button>
      </div>
    )
  }

  // Drafting is a one-way door: the builder shows only until a card exists.
  const showBuilder = cardMissing

  return (
    <div className="page">
      {celebration && <Celebration label={celebration.label} who={celebration.who} />}

      <GameHeader
        game={gameData}
        viewers={viewers}
        socket={socket}
        onBack={() => navigate('')}
        onChanged={game.reload}
      />

      {cardError && <ErrorNote message={cardError} />}

      {spectating ? (
        <Spectator
          gameId={gameId}
          tokens={tokens}
          board={board}
          onOpenCards={() => navigate('admin')}
        />
      ) : showBuilder ? (
        <CardBuilder
          gameId={gameId}
          cardSize={gameData.card_size}
          freeSpace={gameData.free_space}
          onBuilt={(built) => {
            setCard(built)
            setCardMissing(false)
            push({ kind: 'success', title: 'Card locked in', body: 'Now wait for the buzzwords to fly.' })
          }}
        />
      ) : card ? (
        <div className="split split-wide">
          <div className="col gap-16">
            <Panel
              title={
                <div className="row gap-10">
                  <h2>Your card</h2>
                  {card.lines.length > 0 && (
                    <span className="badge badge-accent">
                      ★ {card.lines.length} line{card.lines.length === 1 ? '' : 's'}
                    </span>
                  )}
                </div>
              }
              subtitle={`${card.marked_count} of ${card.cells.length} squares marked`}
              actions={
                /* No rebuild and no proposals once you have committed: both would let a
                   player reshape their odds after hearing which words are landing. */
                <span className="badge badge-live">locked in</span>
              }
            >
              <BingoGrid card={card} />
            </Panel>

            <Panel
              title="Live transcript"
              subtitle="Matched buzzwords light up as they are spoken"
              flush
            >
              <Ticker tokens={tokens} />
            </Panel>
          </div>

          <div className="sticky-side">
            <Panel
              title="Leaderboard"
              subtitle="First to bingo wins"
              flush
            >
              <Leaderboard entries={board} meId={myPlayerId} />
            </Panel>
          </div>
        </div>
      ) : (
        <Spinner label="Loading your card…" />
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ header */

function GameHeader({
  game,
  viewers,
  socket,
  onBack,
  onChanged,
}: {
  game: import('../lib/types').Game
  viewers: number
  socket: string
  onBack: () => void
  onChanged: () => void
}) {
  const { isAdmin } = useSession()
  const { push } = useToast()
  const [busy, setBusy] = useState(false)

  const act = async (action: () => Promise<unknown>, message: string) => {
    setBusy(true)
    try {
      await action()
      push({ kind: 'success', title: message })
      onChanged()
    } catch (err) {
      push({ kind: 'error', title: err instanceof Error ? err.message : 'Action failed.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page-head">
      <div className="row-between wrap gap-12">
        <div className="row gap-12" style={{ minWidth: 0 }}>
          <button className="btn btn-ghost btn-sm" onClick={onBack} aria-label="Back to lobby">←</button>
          <div style={{ minWidth: 0 }}>
            <h1 className="truncate">
              {game.name} <StatusBadge status={game.status} />
            </h1>
            <div className="sub row gap-12 wrap">
              <button
                className="mono"
                style={{
                  background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                  color: 'var(--accent)', letterSpacing: '.14em', fontWeight: 700,
                }}
                onClick={async () => {
                  const ok = await copyToClipboard(game.code)
                  push({ kind: ok ? 'success' : 'error', title: ok ? `Copied ${game.code}` : 'Copy failed' })
                }}
                title="Copy join code"
              >
                {game.code}
              </button>
              <span className="faint" style={{ fontSize: 12 }}>{game.player_count} players</span>
              <span className="faint" style={{ fontSize: 12 }}>{game.token_count} words heard</span>
              <span className="faint row gap-4" style={{ fontSize: 12 }}>
                <i
                  className={`dot${socket === 'open' ? ' dot-pulse' : ''}`}
                  style={{ color: socket === 'open' ? 'var(--success)' : 'var(--text-faint)' }}
                />
                {socket === 'open' ? `live · ${viewers} watching` : socket}
              </span>
            </div>
          </div>
        </div>

        {isAdmin && (
          <div className="row gap-6 wrap">
            {game.status !== 'live' && (
              <button
                className="btn btn-primary btn-sm"
                disabled={busy}
                onClick={() => act(() => api.setGameStatus(game.id, 'live'), 'Game is live')}
              >
                ▶ Start
              </button>
            )}
            {game.status === 'live' && (
              <button
                className="btn btn-sm"
                disabled={busy}
                onClick={() => act(() => api.setGameStatus(game.id, 'paused'), 'Game paused')}
              >
                ‖ Pause
              </button>
            )}
            {game.status !== 'ended' && (
              <button
                className="btn btn-sm"
                disabled={busy}
                onClick={() => act(() => api.setGameStatus(game.id, 'ended'), 'Game ended')}
              >
                ■ End
              </button>
            )}
            <button
              className="btn btn-danger btn-sm"
              disabled={busy}
              onClick={() => {
                if (confirm('Clear the transcript, all marks and all wins? Cards are kept.')) {
                  act(() => api.resetGame(game.id), 'Game reset')
                }
              }}
            >
              Reset
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ builder */

function CardBuilder({
  gameId,
  cardSize,
  freeSpace,
  onBuilt,
}: {
  gameId: string
  cardSize: number
  freeSpace: boolean
  onBuilt: (card: Card) => void
}) {
  const { push } = useToast()
  const capacity = cardSize * cardSize - (freeSpace ? 1 : 0)

  const [selected, setSelected] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('All')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const debouncedSearch = useDebounced(search, 200)
  const words = useAsync(() => api.words(), [])
  const categories = useAsync(() => api.categories(), [])

  const visible = useMemo(() => {
    const needle = debouncedSearch.trim().toLowerCase()
    return (words.data ?? []).filter((word) => {
      if (category !== 'All' && word.category !== category) return false
      if (!needle) return true
      return (
        word.text.toLowerCase().includes(needle) ||
        word.aliases.some((alias) => alias.toLowerCase().includes(needle))
      )
    })
  }, [words.data, debouncedSearch, category])

  const toggle = (word: Word) => {
    setSelected((current) => {
      if (current.includes(word.id)) return current.filter((id) => id !== word.id)
      if (current.length >= capacity) {
        push({ kind: 'error', title: 'Card is full', body: `Deselect a word to swap it out.` })
        return current
      }
      return [...current, word.id]
    })
  }

  /**
   * Board positions skip over the free centre square; the selection array does not.
   * This converts one to the other so a drop on a square knows which pick it moved.
   */
  const freeIndex = freeSpace ? Math.floor((cardSize * cardSize) / 2) : -1
  const slotOf = (position: number) =>
    freeIndex >= 0 && position > freeIndex ? position - 1 : position

  const swapSquares = (from: number, to: number) => {
    const a = slotOf(from)
    const b = slotOf(to)
    setSelected((current) => {
      // Dragging onto a square that will be auto-filled moves the pick to the end of
      // the queue instead of swapping with a word that does not exist yet.
      if (a >= current.length && b >= current.length) return current
      const next = [...current]
      if (b >= next.length) {
        const [moved] = next.splice(a, 1)
        next.push(moved)
        return next
      }
      if (a >= next.length) return next
      ;[next[a], next[b]] = [next[b], next[a]]
      return next
    })
  }

  const randomFill = () => {
    const pool = (words.data ?? []).map((word) => word.id).filter((id) => !selected.includes(id))
    for (let i = pool.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1))
      ;[pool[i], pool[j]] = [pool[j], pool[i]]
    }
    setSelected((current) => [...current, ...pool.slice(0, capacity - current.length)])
  }

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      onBuilt(await api.buildCard(gameId, selected))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not build the card.')
    } finally {
      setBusy(false)
    }
  }

  // Live preview: chosen words in the order you arranged them, free space carved out of
  // the centre. The server no longer reshuffles, so this is exactly the card you get.
  const preview: Card = useMemo(() => {
    const byId = new Map((words.data ?? []).map((word) => [word.id, word]))
    const cells = []
    let cursor = 0
    for (let position = 0; position < cardSize * cardSize; position += 1) {
      if (position === freeIndex) {
        cells.push({
          id: `free-${position}`, position, word_id: null, text: 'FREE',
          category: '', is_free: true, marked: true, marked_at: null,
        })
        continue
      }
      const word = byId.get(selected[cursor])
      cursor += 1
      cells.push({
        id: word ? `p-${word.id}` : `empty-${position}`,
        position,
        word_id: word?.id ?? null,
        text: word?.text ?? '',
        category: word?.category ?? '',
        is_free: false,
        marked: false,
        marked_at: null,
      })
    }
    return {
      id: 'preview', game_id: gameId, player_id: '', nickname: 'Preview', avatar: '', accent: 'green',
      card_size: cardSize, locked: false, created_at: '', cells, marked_count: 0, lines: [], best_rank: null,
    }
  }, [selected, words.data, cardSize, freeIndex, gameId])

  const remaining = capacity - selected.length

  return (
    <div className="split split-wide">
      <Panel
        title="Draft your card"
        subtitle={`Pick up to ${capacity} buzzwords — anything you leave blank is filled at random.`}
        actions={
          <>
            <SuggestWordButton onWordAdded={words.reload} />
            <button className="btn btn-sm" onClick={randomFill} disabled={remaining <= 0}>
              Surprise me
            </button>
            <button
              className="btn btn-sm"
              onClick={() => setSelected([])}
              disabled={selected.length === 0}
            >
              Clear
            </button>
          </>
        }
      >
        {error && <ErrorNote message={error} />}

        <div className="row gap-8 wrap" style={{ marginBottom: 14 }}>
          <div className="search grow" style={{ minWidth: 190 }}>
            <input
              className="input"
              placeholder="Search buzzwords…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              aria-label="Search buzzwords"
            />
          </div>
          <select
            className="select"
            style={{ width: 'auto', minWidth: 160 }}
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            aria-label="Filter by category"
          >
            <option value="All">All categories</option>
            {(categories.data ?? []).map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
        </div>

        {words.loading && <Spinner label="Loading the word pool…" />}
        {words.error && <ErrorNote message={words.error} />}

        {words.data && visible.length === 0 && (
          <Empty icon="⌕" title="Nothing matches">Try a different search or category.</Empty>
        )}

        <div className="chip-cloud">
          {visible.map((word) => {
            const isSelected = selected.includes(word.id)
            const full = !isSelected && remaining <= 0
            return (
              <button
                key={word.id}
                className={`chip${isSelected ? ' selected' : ''}${full ? ' disabled' : ''}`}
                onClick={() => toggle(word)}
                disabled={full}
                title={word.aliases.length > 0 ? `Also matches: ${word.aliases.join(', ')}` : undefined}
              >
                {word.text}
                <Difficulty level={word.difficulty} />
              </button>
            )
          })}
        </div>
      </Panel>

      <div className="sticky-side">
        <Panel
          title="Preview"
          subtitle={
            remaining > 0
              ? `${selected.length}/${capacity} chosen · ${remaining} auto-filled`
              : `${capacity}/${capacity} — card is full`
          }
        >
          <BingoGrid card={preview} compact onSwap={swapSquares} />
          {selected.length > 1 && (
            <p className="faint" style={{ fontSize: 11.5, marginTop: 10 }}>
              ⠿ Drag a square onto another to swap them — or tap one, then tap where it
              should go. This layout is the card you get.
            </p>
          )}
          <div className="meter" style={{ marginTop: 14 }}>
            <i style={{ width: `${Math.min(100, (selected.length / capacity) * 100)}%` }} />
          </div>
          <div className="row gap-8" style={{ marginTop: 14 }}>
            <button className="btn btn-primary grow" onClick={submit} disabled={busy}>
              {busy ? 'Locking in…' : 'Lock in card'}
            </button>
          </div>
          <p className="faint" style={{ fontSize: 11.5, marginTop: 10 }}>
            Locking in is final — you cannot redraft afterwards. Squares mark themselves
            when the word is spoken, and inflections count, so “synergies” marks{' '}
            <em>synergy</em> and “leveraged” marks <em>leverage</em>.
          </p>
        </Panel>
      </div>
    </div>
  )
}


/* ------------------------------------------------------------------ spectator */

/**
 * What an administrator sees in a game room: the room's live state without a card,
 * because admins do not hold a player identity and cannot draft one.
 */
function Spectator({
  gameId,
  tokens,
  board,
  onOpenCards,
}: {
  gameId: string
  tokens: TranscriptToken[]
  board: LeaderboardEntry[]
  onOpenCards: () => void
}) {
  const cards = useAsync(() => api.gameCards(gameId), [gameId])

  return (
    <div className="split split-wide">
      <div className="col gap-16">
        <Panel
          title="Live transcript"
          subtitle="Matched buzzwords light up as they are spoken"
          flush
        >
          <Ticker tokens={tokens} />
        </Panel>

        <Panel
          title={`${cards.data?.length ?? 0} cards in play`}
          subtitle="You are watching as an administrator — you have no card of your own"
          actions={
            <button className="btn btn-sm" onClick={onOpenCards}>
              Open console
            </button>
          }
        >
          {cards.loading && <Spinner />}
          {cards.data && cards.data.length === 0 && (
            <Empty icon="▦" title="Nobody has joined yet">
              Share the game code and players can drop in with a nickname.
            </Empty>
          )}
          <div
            className="game-grid"
            style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}
          >
            {(cards.data ?? []).map((entry) => (
              <div key={entry.id} className="col gap-6">
                <div className="row gap-8">
                  <span style={{ fontWeight: 620, fontSize: 13 }}>{entry.nickname}</span>
                  <span className="faint" style={{ fontSize: 11.5 }}>
                    {entry.marked_count}/{entry.cells.length}
                  </span>
                </div>
                <BingoGrid card={entry} compact />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="sticky-side">
        <Panel title="Leaderboard" subtitle="First to bingo wins" flush>
          <Leaderboard entries={board} />
        </Panel>
      </div>
    </div>
  )
}
