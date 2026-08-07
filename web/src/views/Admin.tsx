/**
 * Admin console.
 *
 * Everything an operator needs during a live meeting: pool curation, game control,
 * every player's card, a transcript injector for testing, key management and an
 * audit trail.
 */

import { useMemo, useState } from 'react'

import { api } from '../lib/api'
import { useAsync, useDebounced } from '../lib/hooks'
import { useToast } from '../lib/store'
import { BingoGrid } from '../components/BingoCard'
import {
  Avatar,
  Difficulty,
  Empty,
  ErrorNote,
  Modal,
  Panel,
  Spinner,
  Stat,
  StatusBadge,
  clockTime,
  copyToClipboard,
  relativeTime,
} from '../components/ui'
import type { ApiKey, Word } from '../lib/types'

type Tab =
  | 'overview'
  | 'words'
  | 'suggestions'
  | 'games'
  | 'cards'
  | 'feed'
  | 'players'
  | 'keys'
  | 'audit'

const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'words', label: 'Word pool' },
  { id: 'suggestions', label: 'Suggestions' },
  { id: 'games', label: 'Games' },
  { id: 'cards', label: 'Cards' },
  { id: 'feed', label: 'Transcript feed' },
  { id: 'players', label: 'Players' },
  { id: 'keys', label: 'API keys' },
  { id: 'audit', label: 'Audit log' },
]

export function Admin({ navigate }: { navigate: (path: string) => void }) {
  const [tab, setTab] = useState<Tab>('overview')

  return (
    <div className="page">
      <div className="page-head">
        <h1>Admin console</h1>
        <p className="sub">Curate the pool, run the room, and watch every card at once.</p>
      </div>

      <div className="tabs" role="tablist">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            role="tab"
            aria-selected={tab === entry.id}
            className={`tab${tab === entry.id ? ' active' : ''}`}
            onClick={() => setTab(entry.id)}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {tab === 'overview' && <Overview />}
      {tab === 'words' && <WordPool />}
      {tab === 'suggestions' && <Suggestions />}
      {tab === 'games' && <Games navigate={navigate} />}
      {tab === 'cards' && <AllCards />}
      {tab === 'feed' && <Feed />}
      {tab === 'players' && <Players />}
      {tab === 'keys' && <Keys />}
      {tab === 'audit' && <AuditLog />}
    </div>
  )
}

/* ------------------------------------------------------------------ overview */

function Overview() {
  const stats = useAsync(() => api.stats(), [])
  const audit = useAsync(() => api.audit(8), [])

  if (stats.loading) return <Spinner label="Loading metrics…" />
  if (stats.error) return <ErrorNote message={stats.error} />
  if (!stats.data) return null

  const s = stats.data

  return (
    <div className="col gap-16">
      {!s.moderation_enabled && (
        <div className="banner">
          <span>⚠</span>
          <div>
            <strong>Word judge is offline.</strong> No{' '}
            <code className="mono">ANTHROPIC_API_KEY</code> is configured, so player word
            suggestions queue for your review instead of being decided automatically.
          </div>
        </div>
      )}

      <div className="stat-grid">
        <Stat value={s.players} label="Players" hint="across all games" />
        <Stat
          value={s.active_words}
          label="Active words"
          hint={`${s.words} total in pool`}
          accent="violet"
        />
        <Stat value={s.live_games} label="Live games" hint={`${s.games} all time`} accent="lime" />
        <Stat value={s.cards} label="Cards dealt" accent="sky" />
        <Stat value={s.tokens.toLocaleString()} label="Words heard" accent="amber" />
        <Stat value={s.bingos} label="Bingos" accent="rose" />
        <Stat
          value={s.pending_suggestions}
          label="Pending words"
          hint={s.moderation_enabled ? s.moderation_model : 'judge offline'}
          accent="teal"
        />
        <Stat value={s.environment} label="Environment" />
      </div>

      <div className="split">
        <Panel title="Integration" subtitle="Point your transcription pipeline here">
          <p className="dim" style={{ fontSize: 13, marginBottom: 12 }}>
            Post transcript text one word at a time, or in whole sentences — it is tokenised
            server-side either way. Omit <code className="mono">game_code</code> to broadcast to
            every live game at once.
          </p>
          <pre className="code-block">
{`curl -X POST http://localhost:8000/api/ingest \\
  -H `}<span className="s">'X-API-Key: bb_your_key'</span>{` \\
  -H `}<span className="s">'Content-Type: application/json'</span>{` \\
  -d `}<span className="s">{`'{"text": "synergy", "game_code": "DEMO1"}'`}</span>
          </pre>
          <p className="faint" style={{ fontSize: 12, marginTop: 12 }}>
            Full reference at <a href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a>.
          </p>
        </Panel>

        <Panel title="Recent activity" flush>
          {audit.data && audit.data.length > 0 ? (
            <div className="lb">
              {audit.data.map((entry) => (
                <div key={entry.id} className="lb-row" style={{ gridTemplateColumns: '1fr auto' }}>
                  <div style={{ minWidth: 0 }}>
                    <div className="mono" style={{ fontSize: 12, color: 'var(--accent)' }}>{entry.action}</div>
                    <div className="faint truncate" style={{ fontSize: 11.5 }}>
                      {entry.actor_name}{entry.detail ? ` · ${entry.detail}` : ''}
                    </div>
                  </div>
                  <div className="lb-meta">{relativeTime(entry.created_at)}</div>
                </div>
              ))}
            </div>
          ) : (
            <Empty icon="◷" title="Nothing yet" />
          )}
        </Panel>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ words */

function WordPool() {
  const { push } = useToast()
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('All')
  const [showInactive, setShowInactive] = useState(true)
  const [adding, setAdding] = useState(false)
  const [bulk, setBulk] = useState(false)
  const [editing, setEditing] = useState<Word | null>(null)

  const debounced = useDebounced(search, 200)
  const words = useAsync(() => api.words({ include_inactive: true }), [])

  const visible = useMemo(() => {
    const needle = debounced.trim().toLowerCase()
    return (words.data ?? []).filter((word) => {
      if (!showInactive && !word.active) return false
      if (category !== 'All' && word.category !== category) return false
      if (!needle) return true
      return (
        word.text.toLowerCase().includes(needle) ||
        word.aliases.some((alias) => alias.toLowerCase().includes(needle))
      )
    })
  }, [words.data, debounced, category, showInactive])

  const categories = useMemo(
    () => [...new Set((words.data ?? []).map((word) => word.category))].sort(),
    [words.data],
  )

  const remove = async (word: Word) => {
    if (!confirm(`Remove "${word.text}"? Words already on a card are deactivated instead.`)) return
    try {
      await api.deleteWord(word.id)
      push({ kind: 'success', title: `"${word.text}" removed` })
      words.reload()
    } catch (err) {
      push({ kind: 'error', title: err instanceof Error ? err.message : 'Delete failed.' })
    }
  }

  const toggleActive = async (word: Word) => {
    try {
      await api.updateWord(word.id, { active: !word.active })
      words.reload()
    } catch (err) {
      push({ kind: 'error', title: err instanceof Error ? err.message : 'Update failed.' })
    }
  }

  return (
    <div className="col gap-16">
      <div className="row-between wrap gap-12">
        <div className="row gap-8 wrap grow">
          <div className="search grow" style={{ maxWidth: 300 }}>
            <input
              className="input"
              placeholder="Search words and aliases…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <select
            className="select"
            style={{ width: 'auto', minWidth: 160 }}
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          >
            <option value="All">All categories</option>
            {categories.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={(event) => setShowInactive(event.target.checked)}
            />
            Show inactive
          </label>
        </div>
        <div className="row gap-8">
          <button className="btn" onClick={() => setBulk(true)}>Bulk import</button>
          <button className="btn btn-primary" onClick={() => setAdding(true)}>+ Add word</button>
        </div>
      </div>

      <MatcherPlayground />

      <Panel
        title={`${visible.length} word${visible.length === 1 ? '' : 's'}`}
        subtitle="Aliases are extra surface forms; strict words skip fuzzy stemming"
        flush
      >
        {words.loading && <Spinner />}
        {words.error && <div style={{ padding: 16 }}><ErrorNote message={words.error} /></div>}
        {visible.length === 0 && !words.loading && <Empty icon="⌕" title="No words match" />}

        {visible.length > 0 && (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Word</th>
                  <th>Category</th>
                  <th>Rarity</th>
                  <th>Aliases</th>
                  <th>On cards</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visible.map((word) => (
                  <tr key={word.id} style={{ opacity: word.active ? 1 : 0.5 }}>
                    <td style={{ fontWeight: 570 }}>
                      {word.text}
                      {word.strict_match && <span className="badge" style={{ marginLeft: 7 }}>strict</span>}
                    </td>
                    <td className="dim">{word.category}</td>
                    <td><Difficulty level={word.difficulty} /></td>
                    <td className="faint mono" style={{ fontSize: 11.5, maxWidth: 200 }}>
                      <div className="truncate">{word.aliases.join(', ') || '—'}</div>
                    </td>
                    <td className="mono tnum dim">{word.usage_count}</td>
                    <td>
                      <span className={`badge ${word.active ? 'badge-live' : 'badge-ended'}`}>
                        {word.active ? 'active' : 'inactive'}
                      </span>
                    </td>
                    <td>
                      <div className="row gap-4" style={{ justifyContent: 'flex-end' }}>
                        <button className="btn btn-ghost btn-sm" onClick={() => setEditing(word)}>Edit</button>
                        <button className="btn btn-ghost btn-sm" onClick={() => toggleActive(word)}>
                          {word.active ? 'Disable' : 'Enable'}
                        </button>
                        <button className="btn btn-ghost btn-sm" onClick={() => remove(word)}>✕</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {(adding || editing) && (
        <WordEditor
          word={editing}
          categories={categories}
          onClose={() => { setAdding(false); setEditing(null) }}
          onSaved={() => { setAdding(false); setEditing(null); words.reload() }}
        />
      )}

      {bulk && <BulkImport onClose={() => setBulk(false)} onDone={() => { setBulk(false); words.reload() }} />}
    </div>
  )
}

/** Live view of how the stemmer sees a phrase — settles "but he said synergies!" disputes. */
function MatcherPlayground() {
  const [spoken, setSpoken] = useState('leveraging')
  const [target, setTarget] = useState('leverage')
  const debouncedSpoken = useDebounced(spoken, 300)
  const debouncedTarget = useDebounced(target, 300)

  const result = useAsync(
    () =>
      debouncedSpoken.trim()
        ? api.inspect(debouncedSpoken, debouncedTarget || undefined)
        : Promise.resolve(undefined),
    [debouncedSpoken, debouncedTarget],
  )

  const matches = result.data?.against?.matches

  return (
    <Panel title="Matcher playground" subtitle="Check whether a spoken word would mark a square">
      <div className="row gap-12 wrap">
        <div className="field grow" style={{ minWidth: 160 }}>
          <label className="label" htmlFor="spoken">Spoken word</label>
          <input id="spoken" className="input" value={spoken} onChange={(e) => setSpoken(e.target.value)} />
        </div>
        <div className="field grow" style={{ minWidth: 160 }}>
          <label className="label" htmlFor="target">Card word</label>
          <input id="target" className="input" value={target} onChange={(e) => setTarget(e.target.value)} />
        </div>
        <div className="field" style={{ minWidth: 110 }}>
          <span className="label">Result</span>
          <div style={{ paddingTop: 6 }}>
            {matches === undefined ? (
              <span className="badge">—</span>
            ) : matches ? (
              <span className="badge badge-live">✓ marks</span>
            ) : (
              <span className="badge badge-danger">✕ no match</span>
            )}
          </div>
        </div>
      </div>
      {result.data && (
        <div className="kv" style={{ marginTop: 14 }}>
          <dt>Tokens</dt>
          <dd className="mono" style={{ fontSize: 12 }}>{result.data.tokens.join(' · ') || '—'}</dd>
          <dt>Match keys</dt>
          <dd className="mono" style={{ fontSize: 12, color: 'var(--accent)' }}>
            {result.data.match_keys.join(' · ') || '—'}
          </dd>
          {result.data.against && (
            <>
              <dt>Card keys</dt>
              <dd className="mono" style={{ fontSize: 12, color: 'var(--violet)' }}>
                {result.data.against.match_keys.join(' · ') || '—'}
              </dd>
            </>
          )}
        </div>
      )}
    </Panel>
  )
}

function WordEditor({
  word,
  categories,
  onClose,
  onSaved,
}: {
  word: Word | null
  categories: string[]
  onClose: () => void
  onSaved: () => void
}) {
  const { push } = useToast()
  const [form, setForm] = useState({
    text: word?.text ?? '',
    category: word?.category ?? categories[0] ?? 'General',
    difficulty: word?.difficulty ?? 2,
    aliases: word?.aliases.join(', ') ?? '',
    strict_match: word?.strict_match ?? false,
    active: word?.active ?? true,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    setBusy(true)
    setError(null)
    const payload = {
      text: form.text.trim(),
      category: form.category.trim() || 'General',
      difficulty: form.difficulty,
      aliases: form.aliases.split(',').map((alias) => alias.trim()).filter(Boolean),
      strict_match: form.strict_match,
      active: form.active,
    }
    try {
      if (word) await api.updateWord(word.id, payload)
      else await api.createWord(payload)
      push({ kind: 'success', title: word ? 'Word updated' : `"${payload.text}" added` })
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={word ? `Edit "${word.text}"` : 'Add a buzzword'}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={save} disabled={busy || !form.text.trim()}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </>
      }
    >
      {error && <ErrorNote message={error} />}
      <div className="field">
        <label className="label" htmlFor="w-text">Word or phrase</label>
        <input
          id="w-text"
          className="input"
          value={form.text}
          onChange={(event) => setForm({ ...form, text: event.target.value })}
          placeholder="e.g. paradigm shift"
          autoFocus
        />
      </div>
      <div className="row gap-12">
        <div className="field grow">
          <label className="label" htmlFor="w-cat">Category</label>
          <input
            id="w-cat"
            className="input"
            list="category-options"
            value={form.category}
            onChange={(event) => setForm({ ...form, category: event.target.value })}
          />
          <datalist id="category-options">
            {categories.map((name) => <option key={name} value={name} />)}
          </datalist>
        </div>
        <div className="field" style={{ width: 130 }}>
          <label className="label" htmlFor="w-diff">Rarity</label>
          <select
            id="w-diff"
            className="select"
            value={form.difficulty}
            onChange={(event) => setForm({ ...form, difficulty: Number(event.target.value) })}
          >
            <option value={1}>Common</option>
            <option value={2}>Uncommon</option>
            <option value={3}>Rare</option>
          </select>
        </div>
      </div>
      <div className="field">
        <label className="label" htmlFor="w-alias">Aliases</label>
        <input
          id="w-alias"
          className="input"
          value={form.aliases}
          onChange={(event) => setForm({ ...form, aliases: event.target.value })}
          placeholder="comma separated, e.g. value-add, add value"
        />
        <span className="faint" style={{ fontSize: 11.5 }}>
          Extra phrasings that should also mark this square. Inflections (-s, -ed, -ing, -ly)
          are handled automatically.
        </span>
      </div>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={form.strict_match}
          onChange={(event) => setForm({ ...form, strict_match: event.target.checked })}
        />
        Strict match — require the exact wording, no stemming
      </label>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={form.active}
          onChange={(event) => setForm({ ...form, active: event.target.checked })}
        />
        Active — available when players draft cards
      </label>
    </Modal>
  )
}

function BulkImport({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { push } = useToast()
  const [payload, setPayload] = useState('')
  const [category, setCategory] = useState('General')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const created = await api.bulkWords({ payload, category })
      push({
        kind: 'success',
        title: `${created.length} word${created.length === 1 ? '' : 's'} imported`,
        body: created.length === 0 ? 'Everything was already in the pool.' : undefined,
      })
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Bulk import"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={submit} disabled={busy || !payload.trim()}>
            {busy ? 'Importing…' : 'Import'}
          </button>
        </>
      }
    >
      {error && <ErrorNote message={error} />}
      <div className="field">
        <label className="label" htmlFor="bulk-cat">Default category</label>
        <input
          id="bulk-cat"
          className="input"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
        />
      </div>
      <div className="field">
        <label className="label" htmlFor="bulk-body">Words</label>
        <textarea
          id="bulk-body"
          className="textarea"
          rows={9}
          value={payload}
          onChange={(event) => setPayload(event.target.value)}
          placeholder={'one per line\nor comma separated\nMeeting Filler: circle back'}
        />
        <span className="faint" style={{ fontSize: 11.5 }}>
          Prefix a line with <code className="mono">Category:</code> to override the default.
          Duplicates are skipped.
        </span>
      </div>
    </Modal>
  )
}

/* ------------------------------------------------------------------ games */

function Games({ navigate }: { navigate: (path: string) => void }) {
  const { push } = useToast()
  const games = useAsync(() => api.games(), [])

  const act = async (label: string, action: () => Promise<unknown>) => {
    try {
      await action()
      push({ kind: 'success', title: label })
      games.reload()
    } catch (err) {
      push({ kind: 'error', title: err instanceof Error ? err.message : 'Action failed.' })
    }
  }

  if (games.loading) return <Spinner />
  if (games.error) return <ErrorNote message={games.error} />

  return (
    <Panel title={`${games.data?.length ?? 0} games`} flush>
      {games.data && games.data.length > 0 ? (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Game</th><th>Code</th><th>Status</th><th>Players</th>
                <th>Words</th><th>Bingos</th><th>Created</th><th />
              </tr>
            </thead>
            <tbody>
              {games.data.map((game) => (
                <tr key={game.id}>
                  <td>
                    <button
                      className="btn btn-ghost btn-sm"
                      style={{ padding: 0, fontWeight: 600 }}
                      onClick={() => navigate(`game/${game.id}`)}
                    >
                      {game.name}
                    </button>
                  </td>
                  <td className="mono" style={{ letterSpacing: '.1em', color: 'var(--accent)' }}>{game.code}</td>
                  <td><StatusBadge status={game.status} /></td>
                  <td className="mono tnum dim">{game.player_count}</td>
                  <td className="mono tnum dim">{game.token_count}</td>
                  <td className="mono tnum dim">{game.bingo_count}</td>
                  <td className="faint" style={{ fontSize: 12 }}>{relativeTime(game.created_at)}</td>
                  <td>
                    <div className="row gap-4" style={{ justifyContent: 'flex-end' }}>
                      {game.status !== 'live' && (
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => act('Game started', () => api.setGameStatus(game.id, 'live'))}
                        >▶</button>
                      )}
                      {game.status === 'live' && (
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => act('Game paused', () => api.setGameStatus(game.id, 'paused'))}
                        >‖</button>
                      )}
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => {
                          if (confirm(`Reset "${game.name}"? Marks, wins and transcript are cleared.`)) {
                            act('Game reset', () => api.resetGame(game.id))
                          }
                        }}
                      >⟲</button>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => {
                          if (confirm(`Delete "${game.name}" permanently? All cards go with it.`)) {
                            act('Game deleted', () => api.deleteGame(game.id))
                          }
                        }}
                      >✕</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty icon="◫" title="No games yet">Create one from the lobby.</Empty>
      )}
    </Panel>
  )
}

/* ------------------------------------------------------------------ cards */

function AllCards() {
  const [gameId, setGameId] = useState<string>('')
  const games = useAsync(() => api.games(), [])
  const cards = useAsync(() => api.allCards(gameId || undefined), [gameId])

  return (
    <div className="col gap-16">
      <div className="row gap-8 wrap">
        <select
          className="select"
          style={{ width: 'auto', minWidth: 230 }}
          value={gameId}
          onChange={(event) => setGameId(event.target.value)}
        >
          <option value="">All games</option>
          {(games.data ?? []).map((game) => (
            <option key={game.id} value={game.id}>{game.name} ({game.code})</option>
          ))}
        </select>
        <span className="faint" style={{ fontSize: 12.5, alignSelf: 'center' }}>
          {cards.data?.length ?? 0} card{cards.data?.length === 1 ? '' : 's'}
        </span>
      </div>

      {cards.loading && <Spinner />}
      {cards.error && <ErrorNote message={cards.error} />}
      {cards.data && cards.data.length === 0 && (
        <Panel><Empty icon="▦" title="No cards yet">Players build cards from the game room.</Empty></Panel>
      )}

      <div className="game-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(258px, 1fr))' }}>
        {(cards.data ?? []).map((card) => (
          <Panel
            key={card.id}
            title={
              <div className="row gap-8">
                <Avatar user={card} size="sm" />
                <span style={{ fontSize: 14, fontWeight: 620 }}>{card.nickname}</span>
              </div>
            }
            subtitle={`${card.marked_count}/${card.cells.length} marked${card.lines.length ? ` · ${card.lines.length} line(s)` : ''}`}
            actions={card.best_rank === 1 ? <span className="badge badge-accent">★ 1st</span> : undefined}
          >
            <BingoGrid card={card} compact />
          </Panel>
        ))}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ feed */

/** Transcript injector — drives the real ingest endpoint, so it exercises the true path. */
function Feed() {
  const { push } = useToast()
  const games = useAsync(() => api.games(), [])
  const keys = useAsync(() => api.keys(), [])

  const [gameId, setGameId] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [text, setText] = useState('')
  const [speaker, setSpeaker] = useState('Test Console')
  const [busy, setBusy] = useState(false)
  const [log, setLog] = useState<string[]>([])

  const live = (games.data ?? []).filter((game) => game.status === 'live')

  const send = async (event?: React.FormEvent) => {
    event?.preventDefault()
    const body = text.trim()
    if (!body) return
    setBusy(true)
    try {
      const result = await api.ingest(
        { text: body, game_id: gameId || undefined, speaker, source: 'admin-console' },
        apiKey,
      )
      const hits = (result.results as { hits: unknown[]; bingos: unknown[] }[])
        .reduce((sum, entry) => sum + entry.hits.length, 0)
      const bingos = (result.results as { hits: unknown[]; bingos: unknown[] }[])
        .reduce((sum, entry) => sum + entry.bingos.length, 0)

      setLog((current) => [
        `${clockTime(new Date().toISOString())}  ${result.token_count} token(s) → ${hits} hit(s)${bingos ? `, ${bingos} BINGO` : ''}`,
        ...current.slice(0, 40),
      ])
      setText('')
      if (bingos > 0) push({ kind: 'bingo', title: `${bingos} bingo triggered` })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Ingest failed.'
      setLog((current) => [`${clockTime(new Date().toISOString())}  ✕ ${message}`, ...current.slice(0, 40)])
      push({ kind: 'error', title: message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="split">
      <Panel
        title="Transcript injector"
        subtitle="Posts to POST /api/ingest — the same path a transcription vendor uses"
      >
        {live.length === 0 && (
          <div className="banner" style={{ marginBottom: 14 }}>
            <span>⚠</span>
            <div>No games are live. Start one before sending transcript, or it will be rejected.</div>
          </div>
        )}

        <form className="col gap-12" onSubmit={send}>
          <div className="row gap-12 wrap">
            <div className="field grow" style={{ minWidth: 180 }}>
              <label className="label" htmlFor="feed-game">Target</label>
              <select
                id="feed-game"
                className="select"
                value={gameId}
                onChange={(event) => setGameId(event.target.value)}
              >
                <option value="">All live games (broadcast)</option>
                {(games.data ?? []).map((game) => (
                  <option key={game.id} value={game.id}>
                    {game.name} ({game.code}) — {game.status}
                  </option>
                ))}
              </select>
            </div>
            <div className="field grow" style={{ minWidth: 160 }}>
              <label className="label" htmlFor="feed-speaker">Speaker</label>
              <input
                id="feed-speaker"
                className="input"
                value={speaker}
                onChange={(event) => setSpeaker(event.target.value)}
              />
            </div>
          </div>

          <div className="field">
            <label className="label" htmlFor="feed-key">API key</label>
            <input
              id="feed-key"
              className="input mono"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="bb_…"
              autoComplete="off"
            />
            <span className="faint" style={{ fontSize: 11.5 }}>
              {keys.data && keys.data.length > 0
                ? `${keys.data.filter((key) => key.active).length} active key(s). Full values are only shown at creation — mint a new one under API keys if you need it.`
                : 'No keys yet — create one under API keys.'}
            </span>
          </div>

          <div className="field">
            <label className="label" htmlFor="feed-text">Transcript</label>
            <textarea
              id="feed-text"
              className="textarea"
              rows={4}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="We need to leverage our synergies and circle back on the low hanging fruit…"
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) send()
              }}
            />
          </div>

          <div className="row gap-8">
            <button className="btn btn-primary" type="submit" disabled={busy || !text.trim()}>
              {busy ? 'Sending…' : 'Send to game'}
            </button>
            <span className="faint" style={{ fontSize: 11.5 }}>⌘/Ctrl + Enter</span>
          </div>
        </form>
      </Panel>

      <Panel title="Response log" flush>
        {log.length === 0 ? (
          <Empty icon="⌁" title="No calls yet">Send some transcript to see results.</Empty>
        ) : (
          <div className="ticker" style={{ flexDirection: 'column', flexWrap: 'nowrap', maxHeight: 420 }}>
            {log.map((line, index) => (
              <div key={index} className="tok" style={{ width: '100%', background: 'transparent' }}>
                {line}
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}

/* ------------------------------------------------------------------ players */

function Players() {
  const { push } = useToast()
  const [gameId, setGameId] = useState('')
  const games = useAsync(() => api.games(), [])
  const players = useAsync(() => api.players(gameId || undefined), [gameId])

  const gameName = (id: string) => games.data?.find((g) => g.id === id)?.name ?? '—'

  const remove = async (id: string, nickname: string) => {
    if (!confirm(`Remove "${nickname}" and their card from this game?`)) return
    try {
      await api.removePlayer(id)
      push({ kind: 'success', title: `${nickname} removed` })
      players.reload()
    } catch (err) {
      push({ kind: 'error', title: err instanceof Error ? err.message : 'Remove failed.' })
    }
  }

  return (
    <div className="col gap-16">
      <div className="row gap-8 wrap">
        <select
          className="select"
          style={{ width: 'auto', minWidth: 230 }}
          value={gameId}
          onChange={(event) => setGameId(event.target.value)}
        >
          <option value="">All games</option>
          {(games.data ?? []).map((game) => (
            <option key={game.id} value={game.id}>{game.name} ({game.code})</option>
          ))}
        </select>
        <span className="faint" style={{ fontSize: 12.5, alignSelf: 'center' }}>
          Players exist only inside the game they joined — there are no accounts.
        </span>
      </div>

      <Panel title={`${players.data?.length ?? 0} players`} flush>
        {players.loading && <Spinner />}
        {players.error && <div style={{ padding: 16 }}><ErrorNote message={players.error} /></div>}
        {players.data && players.data.length === 0 && (
          <Empty icon="◌" title="Nobody has joined yet" />
        )}
        {players.data && players.data.length > 0 && (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr><th>Nickname</th><th>Game</th><th>Joined</th><th>Last seen</th><th /></tr>
              </thead>
              <tbody>
                {players.data.map((entry) => (
                  <tr key={entry.id}>
                    <td>
                      <div className="row gap-8">
                        <Avatar user={entry} size="sm" />
                        <span style={{ fontWeight: 570 }}>{entry.nickname}</span>
                      </div>
                    </td>
                    <td className="dim">{gameName(entry.game_id)}</td>
                    <td className="faint" style={{ fontSize: 12 }}>{relativeTime(entry.created_at)}</td>
                    <td className="faint" style={{ fontSize: 12 }}>{relativeTime(entry.last_seen_at)}</td>
                    <td style={{ textAlign: 'right' }}>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => remove(entry.id, entry.nickname)}
                      >✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}

/* ------------------------------------------------------------------ suggestions */

/**
 * Player word submissions and what the judge made of them. Every verdict is
 * overridable — the model curates, the admin decides.
 */
function Suggestions() {
  const { push } = useToast()
  const [filter, setFilter] = useState('')
  const suggestions = useAsync(() => api.suggestions(filter || undefined), [filter])
  const stats = useAsync(() => api.stats(), [])

  const decide = async (id: string, approve: boolean, text: string) => {
    try {
      await api.decideSuggestion(id, approve)
      push({
        kind: 'success',
        title: approve ? `"${text}" added to the pool` : `"${text}" rejected`,
      })
      suggestions.reload()
      stats.reload()
    } catch (err) {
      push({ kind: 'error', title: err instanceof Error ? err.message : 'Action failed.' })
    }
  }

  const tone = (status: string) =>
    status === 'approved' ? 'live' : status === 'rejected' ? 'danger' : 'paused'

  return (
    <div className="col gap-16">
      <div className="row-between wrap gap-12">
        <div className="row gap-6">
          {['', 'pending', 'approved', 'rejected'].map((value) => (
            <button
              key={value || 'all'}
              className={`btn btn-sm${filter === value ? ' btn-primary' : ''}`}
              onClick={() => setFilter(value)}
            >
              {value || 'All'}
            </button>
          ))}
        </div>
        {stats.data && (
          <span className="faint" style={{ fontSize: 12.5 }}>
            {stats.data.moderation_enabled
              ? `Judged by ${stats.data.moderation_model}`
              : 'Judge offline — everything queues for manual review'}
          </span>
        )}
      </div>

      <Panel
        title={`${suggestions.data?.length ?? 0} submissions`}
        subtitle="Players propose words; the AI curator decides whether they are buzzwordy enough"
        flush
      >
        {suggestions.loading && <Spinner />}
        {suggestions.error && (
          <div style={{ padding: 16 }}><ErrorNote message={suggestions.error} /></div>
        )}
        {suggestions.data && suggestions.data.length === 0 && (
          <Empty icon="✦" title="No submissions yet">
            Players can propose words from inside a game.
          </Empty>
        )}
        {suggestions.data && suggestions.data.length > 0 && (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Word</th><th>Player</th><th>Verdict</th>
                  <th>Judge</th><th>When</th><th>Status</th><th />
                </tr>
              </thead>
              <tbody>
                {suggestions.data.map((entry) => (
                  <tr key={entry.id}>
                    <td style={{ fontWeight: 570 }}>
                      {entry.canonical || entry.text}
                      {entry.canonical && entry.canonical !== entry.text && (
                        <div className="faint" style={{ fontSize: 11 }}>
                          submitted as “{entry.text}”
                        </div>
                      )}
                    </td>
                    <td className="dim">{entry.player_name || '—'}</td>
                    <td className="dim" style={{ fontSize: 12.5, maxWidth: 280 }}>
                      {entry.verdict || '—'}
                    </td>
                    <td className="faint mono" style={{ fontSize: 11 }}>
                      {entry.judged_by || '—'}
                    </td>
                    <td className="faint" style={{ fontSize: 12 }}>
                      {relativeTime(entry.created_at)}
                    </td>
                    <td>
                      <span className={`badge badge-${tone(entry.status)}`}>{entry.status}</span>
                    </td>
                    <td>
                      <div className="row gap-4" style={{ justifyContent: 'flex-end' }}>
                        {entry.status !== 'approved' && (
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={() => decide(entry.id, true, entry.canonical || entry.text)}
                          >Approve</button>
                        )}
                        {entry.status !== 'rejected' && (
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={() => decide(entry.id, false, entry.canonical || entry.text)}
                          >Reject</button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}

/* ------------------------------------------------------------------ keys */

function Keys() {
  const { push } = useToast()
  const keys = useAsync(() => api.keys(), [])
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [minted, setMinted] = useState<ApiKey | null>(null)

  const create = async () => {
    try {
      const created = await api.createKey(name.trim() || 'Untitled key')
      setMinted(created)
      setCreating(false)
      setName('')
      keys.reload()
    } catch (err) {
      push({ kind: 'error', title: err instanceof Error ? err.message : 'Could not create key.' })
    }
  }

  const revoke = async (key: ApiKey) => {
    if (!confirm(`Revoke "${key.name}"? Any integration using it stops working immediately.`)) return
    try {
      await api.revokeKey(key.id)
      push({ kind: 'success', title: 'Key revoked' })
      keys.reload()
    } catch (err) {
      push({ kind: 'error', title: err instanceof Error ? err.message : 'Revoke failed.' })
    }
  }

  return (
    <div className="col gap-16">
      <div className="row-between">
        <p className="dim" style={{ fontSize: 13 }}>
          Keys authenticate <code className="mono">POST /api/ingest</code>. Values are hashed —
          copy a new key when it is created, it cannot be shown again.
        </p>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>+ New key</button>
      </div>

      <Panel flush>
        {keys.loading && <Spinner />}
        {keys.data && keys.data.length === 0 && (
          <Empty icon="⚿" title="No API keys">Create one to let a transcription pipeline connect.</Empty>
        )}
        {keys.data && keys.data.length > 0 && (
          <table className="table">
            <thead>
              <tr><th>Name</th><th>Prefix</th><th>Calls</th><th>Last used</th><th>Status</th><th /></tr>
            </thead>
            <tbody>
              {keys.data.map((key) => (
                <tr key={key.id} style={{ opacity: key.active ? 1 : 0.5 }}>
                  <td style={{ fontWeight: 570 }}>{key.name}</td>
                  <td className="mono faint">{key.prefix}…</td>
                  <td className="mono tnum dim">{key.call_count}</td>
                  <td className="faint" style={{ fontSize: 12 }}>{relativeTime(key.last_used_at)}</td>
                  <td>
                    <span className={`badge ${key.active ? 'badge-live' : 'badge-ended'}`}>
                      {key.active ? 'active' : 'revoked'}
                    </span>
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    {key.active && (
                      <button className="btn btn-ghost btn-sm" onClick={() => revoke(key)}>Revoke</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {creating && (
        <Modal
          title="New API key"
          onClose={() => setCreating(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={create}>Create key</button>
            </>
          }
        >
          <div className="field">
            <label className="label" htmlFor="key-name">Label</label>
            <input
              id="key-name"
              className="input"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Zoom caption bridge"
              autoFocus
            />
          </div>
        </Modal>
      )}

      {minted && (
        <Modal
          title="Copy your key now"
          onClose={() => setMinted(null)}
          footer={<button className="btn btn-primary" onClick={() => setMinted(null)}>Done</button>}
        >
          <div className="banner">
            <span>⚠</span>
            <div>This is the only time the full key is shown. Store it somewhere safe.</div>
          </div>
          <pre className="code-block" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            {minted.key}
          </pre>
          <button
            className="btn"
            onClick={async () => {
              const ok = await copyToClipboard(minted.key ?? '')
              push({ kind: ok ? 'success' : 'error', title: ok ? 'Copied to clipboard' : 'Copy failed' })
            }}
          >
            Copy key
          </button>
        </Modal>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ audit */

function AuditLog() {
  const audit = useAsync(() => api.audit(200), [])

  if (audit.loading) return <Spinner />
  if (audit.error) return <ErrorNote message={audit.error} />

  return (
    <Panel title="Audit log" subtitle="Every administrative mutation, newest first" flush>
      {audit.data && audit.data.length > 0 ? (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr><th>When</th><th>Actor</th><th>Action</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {audit.data.map((entry) => (
                <tr key={entry.id}>
                  <td className="faint mono" style={{ fontSize: 11.5, whiteSpace: 'nowrap' }}>
                    {clockTime(entry.created_at)}
                  </td>
                  <td>{entry.actor_name}</td>
                  <td className="mono" style={{ fontSize: 12, color: 'var(--accent)' }}>{entry.action}</td>
                  <td className="dim" style={{ fontSize: 12.5 }}>{entry.detail || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty icon="◷" title="Nothing logged yet" />
      )}
    </Panel>
  )
}
