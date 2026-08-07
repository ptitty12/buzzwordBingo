/**
 * The front door.
 *
 * No accounts, so there is nothing to sign into: you pick a game (or type its code)
 * and choose a nickname on the way in. The admin entrance is a small, quiet link —
 * it leads to a PIN prompt, not to anything a player can browse.
 */

import { useState } from 'react'

import { api } from '../lib/api'
import { useAsync } from '../lib/hooks'
import { useToast } from '../lib/store'
import { Empty, ErrorNote, Panel, Spinner, StatusBadge, relativeTime } from '../components/ui'
import type { Game } from '../lib/types'

export function Landing({ navigate }: { navigate: (path: string) => void }) {
  const { push } = useToast()
  const games = useAsync(() => api.games(), [])
  const [code, setCode] = useState('')

  const joinByCode = (event: React.FormEvent) => {
    event.preventDefault()
    const wanted = code.trim().toUpperCase()
    if (!wanted) return
    const match = games.data?.find((game) => game.code === wanted)
    if (match) navigate(`game/${match.id}`)
    else push({ kind: 'error', title: 'No game with that code', body: `Checked for "${wanted}".` })
  }

  const open = games.data?.filter((game) => game.status !== 'ended') ?? []
  const archived = games.data?.filter((game) => game.status === 'ended') ?? []

  return (
    <div className="page page-narrow">
      <header className="hero">
        <div className="brand-mark hero-mark" aria-hidden="true">
          <i /><i /><i /><i />
        </div>
        <h1>Buzzword Bingo</h1>
        <p className="hero-tag">
          Live transcript. Real-time cards. First to five wins.
        </p>
        <p className="hero-sub">
          Pick a room below and choose a nickname — no account, no password.
        </p>

        <form className="hero-code" onSubmit={joinByCode}>
          <input
            className="input mono"
            placeholder="GAME CODE"
            value={code}
            maxLength={5}
            onChange={(event) => setCode(event.target.value.toUpperCase())}
            aria-label="Join by game code"
          />
          <button className="btn btn-primary" type="submit">Join</button>
        </form>
      </header>

      {games.loading && <Spinner label="Looking for games…" />}
      {games.error && <ErrorNote message={games.error} />}

      {games.data && open.length === 0 && (
        <Panel>
          <Empty icon="◫" title="No games are open">
            An administrator needs to open a game before anyone can play.
          </Empty>
        </Panel>
      )}

      {open.length > 0 && (
        <>
          <div className="label" style={{ marginBottom: 10 }}>Open rooms</div>
          <div className="game-grid" style={{ marginBottom: 28 }}>
            {open.map((game) => (
              <GameCard key={game.id} game={game} onOpen={() => navigate(`game/${game.id}`)} />
            ))}
          </div>
        </>
      )}

      {archived.length > 0 && (
        <>
          <div className="label" style={{ marginBottom: 10 }}>Finished</div>
          <div className="game-grid">
            {archived.map((game) => (
              <GameCard key={game.id} game={game} onOpen={() => navigate(`game/${game.id}`)} />
            ))}
          </div>
        </>
      )}

      <footer className="landing-foot">
        <button className="btn btn-ghost btn-sm" onClick={() => navigate('admin')}>
          Administrator
        </button>
      </footer>
    </div>
  )
}

function GameCard({ game, onOpen }: { game: Game; onOpen: () => void }) {
  return (
    <button className="game-card" onClick={onOpen}>
      <div className="row-between">
        <span className="code">{game.code}</span>
        <StatusBadge status={game.status} />
      </div>
      <div>
        <div style={{ fontWeight: 620, fontSize: 15 }}>{game.name}</div>
        {game.description && (
          <div className="faint truncate" style={{ fontSize: 12.5, marginTop: 2 }}>
            {game.description}
          </div>
        )}
      </div>
      <div className="game-stats">
        <span>{game.player_count} player{game.player_count === 1 ? '' : 's'}</span>
        <span>{game.token_count} words</span>
        <span>{game.bingo_count} bingo{game.bingo_count === 1 ? '' : 's'}</span>
      </div>
      <div className="faint" style={{ fontSize: 11, fontFamily: 'var(--mono)' }}>
        {game.card_size}×{game.card_size} · created {relativeTime(game.created_at)}
      </div>
    </button>
  )
}
