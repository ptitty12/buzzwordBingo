/** Game lobby: browse open rooms, join by code, or spin one up as an admin. */

import { useState } from 'react'

import { api } from '../lib/api'
import { useAsync } from '../lib/hooks'
import { useSession, useToast } from '../lib/store'
import { Empty, ErrorNote, Modal, Panel, Spinner, StatusBadge, relativeTime } from '../components/ui'
import type { Game } from '../lib/types'

export function Lobby({ navigate }: { navigate: (path: string) => void }) {
  const { user } = useSession()
  const { push } = useToast()
  const games = useAsync(() => api.games(), [])

  const [creating, setCreating] = useState(false)
  const [joinCode, setJoinCode] = useState('')
  const [form, setForm] = useState({ name: '', description: '', card_size: 5, free_space: true })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const createGame = async () => {
    setBusy(true)
    setError(null)
    try {
      const game = await api.createGame(form)
      push({ kind: 'success', title: `"${game.name}" is open`, body: `Join code ${game.code}` })
      setCreating(false)
      setForm({ name: '', description: '', card_size: 5, free_space: true })
      games.reload()
      navigate(`game/${game.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the game.')
    } finally {
      setBusy(false)
    }
  }

  const join = (event: React.FormEvent) => {
    event.preventDefault()
    const code = joinCode.trim().toUpperCase()
    if (!code) return
    const match = games.data?.find((game) => game.code === code)
    if (match) navigate(`game/${match.id}`)
    else push({ kind: 'error', title: 'No game with that code', body: `Checked for "${code}".` })
  }

  const active = games.data?.filter((game) => game.status !== 'ended') ?? []
  const archived = games.data?.filter((game) => game.status === 'ended') ?? []

  return (
    <div className="page">
      <div className="page-head row-between wrap">
        <div>
          <h1>Lobby</h1>
          <p className="sub">Pick a room, draft your card, and wait for someone to say “synergy”.</p>
        </div>
        <div className="row gap-8 wrap">
          <form className="row gap-6" onSubmit={join}>
            <input
              className="input mono"
              style={{ width: 130, letterSpacing: '.16em', textTransform: 'uppercase' }}
              placeholder="CODE"
              value={joinCode}
              maxLength={5}
              onChange={(event) => setJoinCode(event.target.value.toUpperCase())}
              aria-label="Join by code"
            />
            <button className="btn" type="submit">Join</button>
          </form>
          {user?.is_admin && (
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              + New game
            </button>
          )}
        </div>
      </div>

      {games.loading && <Spinner label="Loading games…" />}
      {games.error && <ErrorNote message={games.error} />}

      {games.data && active.length === 0 && archived.length === 0 && (
        <Panel>
          <Empty icon="◫" title="No games yet">
            {user?.is_admin
              ? 'Create the first game to get things started.'
              : 'An administrator needs to open a game before you can play.'}
          </Empty>
        </Panel>
      )}

      {active.length > 0 && (
        <>
          <div className="label" style={{ marginBottom: 10 }}>Open rooms</div>
          <div className="game-grid" style={{ marginBottom: 28 }}>
            {active.map((game) => (
              <GameCard key={game.id} game={game} onOpen={() => navigate(`game/${game.id}`)} />
            ))}
          </div>
        </>
      )}

      {archived.length > 0 && (
        <>
          <div className="label" style={{ marginBottom: 10 }}>Archive</div>
          <div className="game-grid">
            {archived.map((game) => (
              <GameCard key={game.id} game={game} onOpen={() => navigate(`game/${game.id}`)} />
            ))}
          </div>
        </>
      )}

      {creating && (
        <Modal
          title="New game"
          onClose={() => setCreating(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
              <button
                className="btn btn-primary"
                onClick={createGame}
                disabled={busy || form.name.trim().length < 2}
              >
                {busy ? 'Creating…' : 'Create game'}
              </button>
            </>
          }
        >
          {error && <ErrorNote message={error} />}
          <div className="field">
            <label className="label" htmlFor="game-name">Name</label>
            <input
              id="game-name"
              className="input"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="Q4 Strategy Offsite"
              autoFocus
            />
          </div>
          <div className="field">
            <label className="label" htmlFor="game-desc">Description</label>
            <input
              id="game-desc"
              className="input"
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
              placeholder="Optional — what meeting is this?"
            />
          </div>
          <div className="field">
            <label className="label" htmlFor="card-size">Card size</label>
            <select
              id="card-size"
              className="select"
              value={form.card_size}
              onChange={(event) => setForm({ ...form, card_size: Number(event.target.value) })}
            >
              <option value={3}>3 × 3 — quick round</option>
              <option value={5}>5 × 5 — classic</option>
              <option value={7}>7 × 7 — all-day workshop</option>
            </select>
          </div>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={form.free_space}
              onChange={(event) => setForm({ ...form, free_space: event.target.checked })}
            />
            Free space in the centre
          </label>
        </Modal>
      )}
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
