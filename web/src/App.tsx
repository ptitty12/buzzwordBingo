/** Shell: app bar, routing and the signed-out gate. */

import { useEffect } from 'react'

import { useRoute } from './lib/hooks'
import { useSession } from './lib/store'
import { Avatar, Spinner, ToastStack } from './components/ui'
import { Admin } from './views/Admin'
import { GameRoom } from './views/Game'
import { Lobby } from './views/Lobby'
import { Login } from './views/Login'

export default function App() {
  const { user, ready, signOut } = useSession()
  const [segments, navigate] = useRoute()

  // Keep the whole UI tinted with the player's chosen accent.
  useEffect(() => {
    document.documentElement.dataset.accent = user?.accent ?? 'green'
  }, [user?.accent])

  // Send signed-in players landing on "/" to the lobby.
  useEffect(() => {
    if (ready && user && segments.length === 0) navigate('lobby')
  }, [ready, user, segments.length, navigate])

  if (!ready) {
    return (
      <div className="login-shell">
        <Spinner label="Restoring session…" />
      </div>
    )
  }

  if (!user) {
    return (
      <>
        <Login />
        <ToastStack />
      </>
    )
  }

  const [section, param] = segments

  return (
    <>
      <header className="appbar">
        <button
          className="brand"
          style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
          onClick={() => navigate('lobby')}
        >
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /><i /></span>
          <span className="hide-sm">Buzzword Bingo</span>
        </button>

        <nav className="nav grow">
          <a
            href="#/lobby"
            className={section === 'lobby' || section === 'game' ? 'active' : ''}
          >
            Lobby
          </a>
          {user.is_admin && (
            <a href="#/admin" className={section === 'admin' ? 'active' : ''}>
              Admin
            </a>
          )}
          <a href="/api/docs" target="_blank" rel="noreferrer" className="hide-sm">
            API
          </a>
        </nav>

        <div className="row gap-8">
          <div className="row gap-8 hide-sm">
            <Avatar user={user} />
            <div className="col" style={{ lineHeight: 1.2 }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>{user.nickname}</span>
              {user.is_admin && <span className="faint" style={{ fontSize: 10.5 }}>administrator</span>}
            </div>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>

      <main>
        {section === 'admin' && user.is_admin ? (
          <Admin navigate={navigate} />
        ) : section === 'game' && param ? (
          <GameRoom gameId={param} navigate={navigate} />
        ) : (
          <Lobby navigate={navigate} />
        )}
      </main>

      <ToastStack />
    </>
  )
}
