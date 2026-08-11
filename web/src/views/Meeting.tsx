/**
 * The meeting room.
 *
 * Two modes share one screen: participants without a grid get the drafting board, participants
 * with one get the live grid, ticker and standings. Everything after the initial load
 * is driven by the WebSocket, so the view never polls.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api } from '../lib/api'
import { useAsync, useDebounced, useMeetingSocket } from '../lib/hooks'
import { useSession, useToast } from '../lib/store'
import { TermGrid, Celebration, Standings, Ticker } from '../components/TermGrid'
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
  Grid,
  MeetingEvent,
  StandingsEntry,
  TranscriptToken,
  Word,
} from '../lib/types'

const MAX_TICKER_TOKENS = 260

export function MeetingRoom({ meetingId, navigate }: { meetingId: string; navigate: (path: string) => void }) {
  const { participant, isAdmin } = useSession()
  const { push } = useToast()
  const myParticipantId = participant?.id

  const meeting = useAsync(() => api.meeting(meetingId), [meetingId])
  const [grid, setGrid] = useState<Grid | null>(null)
  const [gridMissing, setGridMissing] = useState(false)
  const [gridError, setGridError] = useState<string | null>(null)
  const [tokens, setTokens] = useState<TranscriptToken[]>([])
  const [board, setBoard] = useState<StandingsEntry[]>([])
  const [viewers, setViewers] = useState(0)
  const [celebration, setCelebration] = useState<{ label: string; who: string } | null>(null)

  const meetingData = meeting.data
  // Admins browse every meeting but play in none of them.
  const spectating = isAdmin && !myParticipantId
  const gridRef = useRef<Grid | null>(null)
  gridRef.current = grid

  // Initial load: grid (may legitimately 404), transcript backfill, standings.
  // An admin watching a meeting holds no participant identity and therefore has no grid —
  // that is spectating, not an error, so we do not even ask for one.
  useEffect(() => {
    let cancelled = false
    setGrid(null)
    setGridMissing(false)

    if (!spectating) {
      api
        .myGrid(meetingId)
        .then((result) => !cancelled && setGrid(result))
        .catch((err: unknown) => {
          if (cancelled) return
          const status = (err as { status?: number }).status
          if (status === 404) setGridMissing(true)
          else setGridError(err instanceof Error ? err.message : 'Could not load your grid.')
        })
    }

    api.transcript(meetingId).then((result) => !cancelled && setTokens(result)).catch(() => undefined)
    api.standings(meetingId).then((result) => !cancelled && setBoard(result)).catch(() => undefined)

    return () => {
      cancelled = true
    }
  }, [meetingId, spectating])

  const refreshGrid = useCallback(() => {
    api
      .myGrid(meetingId)
      .then(setGrid)
      .catch(() => undefined)
  }, [meetingId])

  const onEvent = useCallback(
    (event: MeetingEvent) => {
      switch (event.event) {
        case 'hello':
          setBoard(event.payload.standings)
          setViewers(event.payload.viewers)
          meeting.setData(event.payload.meeting)
          break

        case 'meeting':
          meeting.setData(event.payload)
          if (event.payload.status === 'live') {
            push({ kind: 'info', title: 'The meeting is live', body: 'Grids are locked. Good luck.' })
            refreshGrid()
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
          const mine = event.payload.filter((hit) => hit.participant_id === myParticipantId)
          if (mine.length === 0) break
          setGrid((current) => {
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

        case 'completion': {
          const isMe = event.payload.participant_id === myParticipantId
          if (isMe) {
            setGrid((current) =>
              current ? { ...current, lines: [...new Set([...current.lines, event.payload.pattern])] } : current,
            )
            setCelebration({ label: event.payload.label, who: 'You' })
            setTimeout(() => setCelebration(null), 3600)
          }
          push({
            kind: 'completion',
            title: `${isMe ? 'Line complete! You' : `${event.payload.nickname} completed a line`} — ${event.payload.label}`,
            body: `#${event.payload.rank} to complete a line.`,
          })
          break
        }

        case 'standings':
          setBoard(event.payload)
          break

        case 'roster':
          if (event.payload.participant_id !== myParticipantId) {
            push({ kind: 'info', title: `${event.payload.nickname} joined` })
          }
          break
      }
    },
    [meeting, push, refreshGrid, myParticipantId],
  )

  const socket = useMeetingSocket(meetingId, onEvent)

  if (meeting.loading) return <div className="page"><Spinner label="Loading meeting…" /></div>
  if (meeting.error || !meetingData) {
    return (
      <div className="page">
        <ErrorNote message={meeting.error ?? 'Meeting not found.'} />
        <button className="btn" style={{ marginTop: 14 }} onClick={() => navigate('')}>
          ← All meetings
        </button>
      </div>
    )
  }

  // Drafting is a one-way door: the builder shows only until a grid exists.
  const showBuilder = gridMissing

  return (
    <div className="page">
      {celebration && <Celebration label={celebration.label} who={celebration.who} />}

      <MeetingHeader
        meeting={meetingData}
        viewers={viewers}
        socket={socket}
        onBack={() => navigate('')}
        onChanged={meeting.reload}
      />

      {gridError && <ErrorNote message={gridError} />}

      {spectating ? (
        <Spectator
          meetingId={meetingId}
          tokens={tokens}
          board={board}
          onOpenGrids={() => navigate('admin')}
        />
      ) : showBuilder ? (
        <GridBuilder
          meetingId={meetingId}
          gridSize={meetingData.grid_size}
          freeSpace={meetingData.free_space}
          onBuilt={(built) => {
            setGrid(built)
            setGridMissing(false)
            push({ kind: 'success', title: 'Grid locked in', body: 'Now wait for the buzzwords to fly.' })
          }}
        />
      ) : grid ? (
        <div className="split split-wide">
          <div className="col gap-16">
            <Panel
              title={
                <div className="row gap-10">
                  <h2>Your grid</h2>
                  {grid.lines.length > 0 && (
                    <span className="badge badge-accent">
                      ★ {grid.lines.length} line{grid.lines.length === 1 ? '' : 's'}
                    </span>
                  )}
                </div>
              }
              subtitle={`${grid.marked_count} of ${grid.cells.length} squares marked`}
              actions={
                /* No rebuild and no proposals once you have committed: both would let a
                   participant reshape their odds after hearing which words are landing. */
                <span className="badge badge-live">locked in</span>
              }
            >
              <TermGrid grid={grid} />
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
              title="Standings"
              subtitle="First to complete a line"
              flush
            >
              <Standings entries={board} meId={myParticipantId} />
            </Panel>
          </div>
        </div>
      ) : (
        <Spinner label="Loading your grid…" />
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ header */

function MeetingHeader({
  meeting,
  viewers,
  socket,
  onBack,
  onChanged,
}: {
  meeting: import('../lib/types').Meeting
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
          <button className="btn btn-ghost btn-sm" onClick={onBack} aria-label="Back to open">←</button>
          <div style={{ minWidth: 0 }}>
            <h1 className="truncate">
              {meeting.name} <StatusBadge status={meeting.status} />
            </h1>
            <div className="sub row gap-12 wrap">
              <button
                className="mono"
                style={{
                  background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                  color: 'var(--accent)', letterSpacing: '.14em', fontWeight: 700,
                }}
                onClick={async () => {
                  const ok = await copyToClipboard(meeting.code)
                  push({ kind: ok ? 'success' : 'error', title: ok ? `Copied ${meeting.code}` : 'Copy failed' })
                }}
                title="Copy join code"
              >
                {meeting.code}
              </button>
              <span className="faint" style={{ fontSize: 12 }}>{meeting.participant_count} participants</span>
              <span className="faint" style={{ fontSize: 12 }}>{meeting.token_count} words heard</span>
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
            {meeting.status !== 'live' && (
              <button
                className="btn btn-primary btn-sm"
                disabled={busy}
                onClick={() => act(() => api.setMeetingStatus(meeting.id, 'live'), 'Meeting is live')}
              >
                ▶ Start
              </button>
            )}
            {meeting.status === 'live' && (
              <button
                className="btn btn-sm"
                disabled={busy}
                onClick={() => act(() => api.setMeetingStatus(meeting.id, 'paused'), 'Meeting paused')}
              >
                ‖ Pause
              </button>
            )}
            {meeting.status !== 'ended' && (
              <button
                className="btn btn-sm"
                disabled={busy}
                onClick={() => act(() => api.setMeetingStatus(meeting.id, 'ended'), 'Meeting ended')}
              >
                ■ End
              </button>
            )}
            <button
              className="btn btn-danger btn-sm"
              disabled={busy}
              onClick={() => {
                if (confirm('Clear the transcript, all marks and all completed lines? Grids are kept.')) {
                  act(() => api.resetMeeting(meeting.id), 'Meeting reset')
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

function GridBuilder({
  meetingId,
  gridSize,
  freeSpace,
  onBuilt,
}: {
  meetingId: string
  gridSize: number
  freeSpace: boolean
  onBuilt: (grid: Grid) => void
}) {
  const { push } = useToast()
  const capacity = gridSize * gridSize - (freeSpace ? 1 : 0)

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
        push({ kind: 'error', title: 'Grid is full', body: `Deselect a word to swap it out.` })
        return current
      }
      return [...current, word.id]
    })
  }

  /**
   * Board positions skip over the free centre square; the selection array does not.
   * This converts one to the other so a drop on a square knows which pick it moved.
   */
  const freeIndex = freeSpace ? Math.floor((gridSize * gridSize) / 2) : -1
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
      onBuilt(await api.buildGrid(meetingId, selected))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not build the grid.')
    } finally {
      setBusy(false)
    }
  }

  // Live preview: chosen words in the order you arranged them, free space carved out of
  // the centre. The server no longer reshuffles, so this is exactly the grid you get.
  const preview: Grid = useMemo(() => {
    const byId = new Map((words.data ?? []).map((word) => [word.id, word]))
    const cells = []
    let cursor = 0
    for (let position = 0; position < gridSize * gridSize; position += 1) {
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
      id: 'preview', meeting_id: meetingId, participant_id: '', nickname: 'Preview', avatar: '', accent: 'green',
      grid_size: gridSize, locked: false, created_at: '', cells, marked_count: 0, lines: [], best_rank: null,
    }
  }, [selected, words.data, gridSize, freeIndex, meetingId])

  const remaining = capacity - selected.length

  return (
    <div className="split split-wide">
      <Panel
        title="Draft your grid"
        subtitle={`Pick up to ${capacity} buzzwords — anything you leave blank is filled at random.`}
        actions={
          <>
            <SuggestWordButton onWordAdded={words.reload} />
            <button className="btn btn-sm" onClick={randomFill} disabled={remaining <= 0}>
              Fill the rest
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
              : `${capacity}/${capacity} — grid is full`
          }
        >
          <TermGrid grid={preview} compact onSwap={swapSquares} />
          {selected.length > 1 && (
            <p className="faint" style={{ fontSize: 11.5, marginTop: 10 }}>
              ⠿ Drag a square onto another to swap them — or tap one, then tap where it
              should go. This layout is the grid you get.
            </p>
          )}
          <div className="meter" style={{ marginTop: 14 }}>
            <i style={{ width: `${Math.min(100, (selected.length / capacity) * 100)}%` }} />
          </div>
          <div className="row gap-8" style={{ marginTop: 14 }}>
            <button className="btn btn-primary grow" onClick={submit} disabled={busy}>
              {busy ? 'Locking in…' : 'Lock in grid'}
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
 * What an administrator sees in a meeting room: the room's live state without a grid,
 * because admins do not hold a participant identity and cannot draft one.
 */
function Spectator({
  meetingId,
  tokens,
  board,
  onOpenGrids,
}: {
  meetingId: string
  tokens: TranscriptToken[]
  board: StandingsEntry[]
  onOpenGrids: () => void
}) {
  const grids = useAsync(() => api.meetingGrids(meetingId), [meetingId])

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
          title={`${grids.data?.length ?? 0} active grids`}
          subtitle="You are watching as an administrator — you have no grid of your own"
          actions={
            <button className="btn btn-sm" onClick={onOpenGrids}>
              Open console
            </button>
          }
        >
          {grids.loading && <Spinner />}
          {grids.data && grids.data.length === 0 && (
            <Empty icon="▦" title="Nobody has joined yet">
              Share the meeting code and participants can drop in with a nickname.
            </Empty>
          )}
          <div
            className="meeting-grid"
            style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}
          >
            {(grids.data ?? []).map((entry) => (
              <div key={entry.id} className="col gap-6">
                <div className="row gap-8">
                  <span style={{ fontWeight: 620, fontSize: 13 }}>{entry.nickname}</span>
                  <span className="faint" style={{ fontSize: 11.5 }}>
                    {entry.marked_count}/{entry.cells.length}
                  </span>
                </div>
                <TermGrid grid={entry} compact />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="sticky-side">
        <Panel title="Standings" subtitle="First to complete a line" flush>
          <Standings entries={board} />
        </Panel>
      </div>
    </div>
  )
}
