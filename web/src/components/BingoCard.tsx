/** The bingo grid, plus the live ticker and leaderboard that surround it. */

import { useEffect, useRef, useState } from 'react'

import { Avatar } from './ui'
import type { Card, CardCell, LeaderboardEntry, TranscriptToken } from '../lib/types'

/** Positions belonging to a completed pattern, so winning lines can glow. */
function patternPositions(pattern: string, size: number): number[] {
  if (pattern.startsWith('row-')) {
    const row = Number(pattern.slice(4))
    return Array.from({ length: size }, (_, i) => row * size + i)
  }
  if (pattern.startsWith('col-')) {
    const col = Number(pattern.slice(4))
    return Array.from({ length: size }, (_, i) => i * size + col)
  }
  if (pattern === 'diag-main') return Array.from({ length: size }, (_, i) => i * size + i)
  if (pattern === 'diag-anti') return Array.from({ length: size }, (_, i) => i * size + (size - 1 - i))
  if (pattern === 'corners') return [0, size - 1, size * (size - 1), size * size - 1]
  if (pattern === 'blackout') return Array.from({ length: size * size }, (_, i) => i)
  return []
}

export function BingoGrid({
  card,
  onCellClick,
  interactive = false,
  compact = false,
}: {
  card: Card
  onCellClick?: (cell: CardCell) => void
  interactive?: boolean
  compact?: boolean
}) {
  const size = card.card_size
  const winning = new Set(card.lines.flatMap((pattern) => patternPositions(pattern, size)))

  // Track which squares flipped since the last render so they can pop once.
  const previous = useRef<Set<string>>(new Set(card.cells.filter((c) => c.marked).map((c) => c.id)))
  const [recent, setRecent] = useState<Set<string>>(new Set())

  useEffect(() => {
    const nowMarked = new Set(card.cells.filter((cell) => cell.marked).map((cell) => cell.id))
    const fresh = [...nowMarked].filter((id) => !previous.current.has(id))
    previous.current = nowMarked
    if (fresh.length === 0) return

    setRecent(new Set(fresh))
    const timer = setTimeout(() => setRecent(new Set()), 600)
    return () => clearTimeout(timer)
  }, [card.cells])

  return (
    <div
      className="card-grid"
      style={{ gridTemplateColumns: `repeat(${size}, minmax(0, 1fr))`, gap: compact ? 3 : undefined }}
      role="grid"
      aria-label={`${card.nickname}'s bingo card`}
    >
      {card.cells.map((cell) => {
        const classes = ['cell']
        if (cell.is_free) classes.push('free')
        if (cell.marked) classes.push('marked')
        if (winning.has(cell.position)) classes.push('winning')
        if (recent.has(cell.id)) classes.push('just-marked')
        if (interactive) classes.push('pick')

        return (
          <div
            key={cell.id}
            className={classes.join(' ')}
            role="gridcell"
            aria-selected={cell.marked}
            title={cell.category ? `${cell.text} — ${cell.category}` : cell.text}
            onClick={interactive && onCellClick ? () => onCellClick(cell) : undefined}
            data-accent={card.accent}
          >
            <span>{cell.text}</span>
          </div>
        )
      })}
    </div>
  )
}

/** Live transcript ticker. Matched tokens light up in the accent colour. */
export function Ticker({ tokens }: { tokens: TranscriptToken[] }) {
  const endRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  // Autoscroll, but only when the reader is already at the bottom — otherwise
  // scrolling back through history fights the incoming stream.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 90
    if (atBottom) endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [tokens])

  return (
    <div className="ticker" ref={containerRef}>
      {tokens.map((token) => (
        <span
          key={token.id}
          className={`tok${token.hit_count > 0 ? ' hit' : ''}`}
          title={token.speaker || undefined}
        >
          {token.raw}
        </span>
      ))}
      <div ref={endRef} />
    </div>
  )
}

export function Leaderboard({
  entries,
  meId,
  emptyLabel = 'No players yet.',
}: {
  entries: LeaderboardEntry[]
  meId?: string
  emptyLabel?: string
}) {
  if (entries.length === 0) {
    return <div className="empty" style={{ padding: 28 }}><p style={{ fontSize: 13 }}>{emptyLabel}</p></div>
  }

  return (
    <div className="lb">
      {entries.map((entry) => {
        const percent = entry.total > 0 ? Math.round((entry.marked / entry.total) * 100) : 0
        const champion = entry.best_rank === 1
        const classes = ['lb-row']
        if (entry.user_id === meId) classes.push('is-me')
        if (champion) classes.push('champion')

        return (
          <div key={entry.card_id} className={classes.join(' ')} data-accent={entry.accent}>
            <div className="lb-rank">{champion ? '★' : entry.position}</div>
            <div style={{ minWidth: 0 }}>
              <div className="row gap-8">
                <Avatar user={entry} size="sm" />
                <span className="lb-name truncate">{entry.nickname}</span>
                {entry.lines > 0 && (
                  <span className="badge badge-accent">
                    {entry.lines} line{entry.lines === 1 ? '' : 's'}
                  </span>
                )}
              </div>
              <div className="meter">
                <i style={{ width: `${percent}%` }} />
              </div>
            </div>
            <div className="lb-meta tnum" style={{ textAlign: 'right' }}>
              {entry.marked}/{entry.total}
              <div className="faint" style={{ fontSize: 10.5 }}>{percent}%</div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** Full-screen celebration when the signed-in player completes a line. */
export function Celebration({ label, who }: { label: string; who: string }) {
  return (
    <div className="celebrate">
      <div>
        <div className="word">BINGO</div>
        <div className="who">
          {who} — {label}
        </div>
      </div>
    </div>
  )
}
