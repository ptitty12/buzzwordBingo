/** Shell: app bar, routing, and the two very different doors into the app. */

import { useEffect, useState } from 'react'

import { activateAdmin, activateNone, api } from './lib/api'
import { useAsync, useRoute } from './lib/hooks'
import { useSession, useToast } from './lib/store'
import { Avatar, ErrorNote, Spinner, ToastStack } from './components/ui'
import { Admin } from './views/Admin'
import { AdminGate } from './views/AdminGate'
import { MeetingRoom } from './views/Meeting'
import { Join } from './views/Join'
import { Landing } from './views/Landing'

export default function App() {
  const { isAdmin, participant, ready, expired, clearExpiry, signOutAdmin } = useSession()
  const { push } = useToast()
  const [segments, navigate] = useRoute()

  const [section, param] = segments

  // A rejected token has already been binned by the API client. Say so plainly —
  // silently reverting to the nickname screen looks like the app lost its mind.
  useEffect(() => {
    if (!expired) return
    push({
      kind: 'info',
      title: expired === 'admin' ? 'Administrator session ended' : 'Session expired',
      body:
        expired === 'admin'
          ? 'Enter the console PIN again to continue.'
          : 'The server restarted or your seat was cleared. Rejoin with your nickname.',
    })
    clearExpiry()
  }, [expired, push, clearExpiry])

  // Tint the whole UI with the current participant's accent (admins stay on the default).
  useEffect(() => {
    document.documentElement.dataset.accent = participant?.accent ?? 'green'
  }, [participant?.accent])

  // Keep the API client pointed at the right credential for the current screen.
  useEffect(() => {
    if (section === 'admin') activateAdmin()
    else if (section !== 'meeting') {
      if (isAdmin) activateAdmin()
      else activateNone()
    }
  }, [section, isAdmin])

  if (!ready) {
    return (
      <div className="gate-shell">
        <Spinner label="Loading…" />
      </div>
    )
  }

  // The admin console and its PIN gate are a self-contained area with no participant chrome.
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

  if (section === 'meeting' && param) {
    return <MeetingScreen meetingId={param} navigate={navigate} />
  }

  return (
    <>
      <Landing navigate={navigate} />
      <ToastStack />
    </>
  )
}

/**
 * A meeting route resolves to one of three things: the nickname gate (no identity yet),
 * the meeting room (participant), or the meeting room in spectator mode (admin).
 */
function MeetingScreen({ meetingId, navigate }: { meetingId: string; navigate: (path: string) => void }) {
  const { isAdmin, participant, enterMeeting, hasParticipantToken, signOutAdmin } = useSession()
  const [entered, setEntered] = useState(false)
  const meeting = useAsync(() => api.meeting(meetingId), [meetingId])

  useEffect(() => {
    setEntered(false)
    enterMeeting(meetingId).then(() => setEntered(true))
  }, [meetingId, enterMeeting])

  if (meeting.loading || !entered) {
    return (
      <div className="gate-shell">
        <Spinner label="Opening the room…" />
      </div>
    )
  }

  if (meeting.error || !meeting.data) {
    return (
      <div className="page page-narrow">
        <ErrorNote message={meeting.error ?? 'Meeting not found.'} />
        <button className="btn" style={{ marginTop: 14 }} onClick={() => navigate('')}>
          ← All meetings
        </button>
      </div>
    )
  }

  const needsNickname = !isAdmin && !hasParticipantToken(meetingId)
  if (needsNickname) {
    return (
      <>
        <Join
          meeting={meeting.data}
          onJoined={() => enterMeeting(meetingId).then(() => setEntered(true))}
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
        nickname={participant?.nickname ?? 'admin'}
        avatar={participant?.avatar}
        accent={participant?.accent}
        section="meeting"
        navigate={navigate}
        onSignOut={isAdmin ? signOutAdmin : undefined}
      />
      <main>
        <MeetingRoom meetingId={meetingId} navigate={navigate} />
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
        <span className="hide-sm">Jargon Watch</span>
      </button>

      <nav className="nav grow">
        <a href="#/" className={section === 'meeting' ? '' : 'active'}>Meetings</a>
        {isAdmin && (
          <a href="#/admin" className={section === 'admin' ? 'active' : ''}>Admin</a>
        )}
        {/* The API reference is an operator surface — never advertised to participants. */}
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
