/** Shell: app bar, routing, and the two very different doors into the app. */

import { useEffect, useState } from 'react'

import { activateAdmin, activateNone, api } from './lib/api'
import { useAsync, useRoute } from './lib/hooks'
import { useSession } from './lib/store'
import { Avatar, ErrorNote, Spinner, ToastStack } from './components/ui'
import { Admin } from './views/Admin'
import { AdminGate } from './views/AdminGate'
import { GameRoom } from './views/Game'
import { Join } from './views/Join'
import { Landing } from './views/Landing'

export default function App() {
  const { isAdmin, player, ready, signOutAdmin } = useSession()
  const [segments, navigate] = useRoute()

  const [section, param] = segments

  // Tint the whole UI with the current player's accent (admins stay on the default).
  useEffect(() => {
    document.documentElement.dataset.accent = player?.accent ?? 'green'
  }, [player?.accent])

  // Keep the API client pointed at the right credential for the current screen.
  useEffect(() => {
    if (section === 'admin') activateAdmin()
    else if (section !== 'game') {
      if (isAdmin) activateAdmin()
      else activateNone()
    }
  }, [section, isAdmin])

  if (!ready) {
    return (
      <div className="login-shell">
        <Spinner label="Loading…" />
      </div>
    )
  }

  // The admin console and its PIN gate are a self-contained area with no player chrome.
  if (section === 'admin') {
    return (
      <>
        {isAdmin ? (
          <>
            <AppBar
              isAdmin
              nickname="admin"
              section={section}
              navigate={navigate}
              onSignOut={signOutAdmin}
            />
            <main>
              <Admin navigate={navigate} />
            </main>
          </>
        ) : (
          <AdminGate onBack={() => navigate('')} />
        )}
        <ToastStack />
      </>
    )
  }

  if (section === 'game' && param) {
    return <GameScreen gameId={param} navigate={navigate} />
  }

  return (
    <>
      <Landing navigate={navigate} />
      <ToastStack />
    </>
  )
}

/**
 * A game route resolves to one of three things: the nickname gate (no identity yet),
 * the game room (player), or the game room in spectator mode (admin).
 */
function GameScreen({ gameId, navigate }: { gameId: string; navigate: (path: string) => void }) {
  const { isAdmin, player, enterGame, hasPlayerToken, signOutAdmin } = useSession()
  const [entered, setEntered] = useState(false)
  const game = useAsync(() => api.game(gameId), [gameId])

  useEffect(() => {
    setEntered(false)
    enterGame(gameId).then(() => setEntered(true))
  }, [gameId, enterGame])

  if (game.loading || !entered) {
    return (
      <div className="login-shell">
        <Spinner label="Opening the room…" />
      </div>
    )
  }

  if (game.error || !game.data) {
    return (
      <div className="page page-narrow">
        <ErrorNote message={game.error ?? 'Game not found.'} />
        <button className="btn" style={{ marginTop: 14 }} onClick={() => navigate('')}>
          ← All games
        </button>
      </div>
    )
  }

  const needsNickname = !isAdmin && !hasPlayerToken(gameId)
  if (needsNickname) {
    return (
      <>
        <Join
          game={game.data}
          onJoined={() => enterGame(gameId).then(() => setEntered(true))}
          onBack={() => navigate('')}
        />
        <ToastStack />
      </>
    )
  }

  return (
    <>
      <AppBar
        isAdmin={isAdmin}
        nickname={player?.nickname ?? 'admin'}
        avatar={player?.avatar}
        accent={player?.accent}
        section="game"
        navigate={navigate}
        onSignOut={isAdmin ? signOutAdmin : undefined}
      />
      <main>
        <GameRoom gameId={gameId} navigate={navigate} />
      </main>
      <ToastStack />
    </>
  )
}

function AppBar({
  isAdmin,
  nickname,
  avatar,
  accent,
  section,
  navigate,
  onSignOut,
}: {
  isAdmin: boolean
  nickname: string
  avatar?: string
  accent?: string
  section: string
  navigate: (path: string) => void
  onSignOut?: () => void
}) {
  return (
    <header className="appbar">
      <button
        className="brand"
        style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
        onClick={() => navigate('')}
      >
        <span className="brand-mark" aria-hidden="true"><i /><i /><i /><i /></span>
        <span className="hide-sm">Buzzword Bingo</span>
      </button>

      <nav className="nav grow">
        <a href="#/" className={section === 'game' ? '' : 'active'}>Games</a>
        {isAdmin && (
          <a href="#/admin" className={section === 'admin' ? 'active' : ''}>Admin</a>
        )}
        {/* The API reference is an operator surface — never advertised to players. */}
        {isAdmin && (
          <a href="/api/docs" target="_blank" rel="noreferrer" className="hide-sm">
            API
          </a>
        )}
      </nav>

      <div className="row gap-8">
        <div className="row gap-8 hide-sm">
          <Avatar
            user={{ avatar, nickname, accent: accent as never }}
          />
          <div className="col" style={{ lineHeight: 1.2 }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>{nickname}</span>
            {isAdmin && <span className="faint" style={{ fontSize: 10.5 }}>administrator</span>}
          </div>
        </div>
        {onSignOut && (
          <button className="btn btn-ghost btn-sm" onClick={onSignOut}>
            Sign out
          </button>
        )}
      </div>
    </header>
  )
}
