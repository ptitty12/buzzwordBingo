/**
 * The front door.
 *
 * No accounts, so there is nothing to sign into: you pick a meeting (or type its code)
 * and choose a nickname on the way in. The admin entrance is a small, quiet link —
 * it leads to a PIN prompt, not to anything a participant can browse.
 */

import { useState } from 'react'

import { api } from '../lib/api'
import { useAsync } from '../lib/hooks'
import { useToast } from '../lib/store'
import { Empty, ErrorNote, Panel, Spinner, StatusBadge, relativeTime } from '../components/ui'
import type { Meeting } from '../lib/types'

export function Landing({ navigate }: { navigate: (path: string) => void }) {
  const { push } = useToast()
  const meetings = useAsync(() => api.meetings(), [])
  const [code, setCode] = useState('')

  const joinByCode = (event: React.FormEvent) => {
    event.preventDefault()
    const wanted = code.trim().toUpperCase()
    if (!wanted) return
    const match = meetings.data?.find((meeting) => meeting.code === wanted)
    if (match) navigate(`meeting/${match.id}`)
    else push({ kind: 'error', title: 'No meeting with that code', body: `Checked for "${wanted}".` })
  }

  const open = meetings.data?.filter((meeting) => meeting.status !== 'ended') ?? []
  const archived = meetings.data?.filter((meeting) => meeting.status === 'ended') ?? []

  return (
    <div className="page page-narrow">
      <header className="hero">
        <div className="brand-mark hero-mark" aria-hidden="true">
          <i /><i /><i /><i />
        </div>
        <h1>Jargon Watch</h1>
        <p className="hero-tag">
          Live transcript. Real-time grids. First to complete a line.
        </p>
        <p className="hero-sub">
          Pick a room below and choose a nickname — no account, no password.
        </p>

        <form className="hero-code" onSubmit={joinByCode}>
          <input
            className="input mono"
            placeholder="MEETING CODE"
            value={code}
            maxLength={5}
            onChange={(event) => setCode(event.target.value.toUpperCase())}
            aria-label="Join by meeting code"
          />
          <button className="btn btn-primary" type="submit">Join</button>
        </form>
      </header>

      {meetings.loading && <Spinner label="Looking for meetings…" />}
      {meetings.error && <ErrorNote message={meetings.error} />}

      {meetings.data && open.length === 0 && (
        <Panel>
          <Empty icon="◫" title="No meetings are open">
            An administrator needs to open a meeting before anyone can join.
          </Empty>
        </Panel>
      )}

      {open.length > 0 && (
        <>
          <div className="label" style={{ marginBottom: 10 }}>Open rooms</div>
          <div className="meeting-grid" style={{ marginBottom: 28 }}>
            {open.map((meeting) => (
              <MeetingGrid key={meeting.id} meeting={meeting} onOpen={() => navigate(`meeting/${meeting.id}`)} />
            ))}
          </div>
        </>
      )}

      {archived.length > 0 && (
        <>
          <div className="label" style={{ marginBottom: 10 }}>Finished</div>
          <div className="meeting-grid">
            {archived.map((meeting) => (
              <MeetingGrid key={meeting.id} meeting={meeting} onOpen={() => navigate(`meeting/${meeting.id}`)} />
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

function MeetingGrid({ meeting, onOpen }: { meeting: Meeting; onOpen: () => void }) {
  return (
    <button className="meeting-tile" onClick={onOpen}>
      <div className="row-between">
        <span className="code">{meeting.code}</span>
        <StatusBadge status={meeting.status} />
      </div>
      <div>
        <div style={{ fontWeight: 620, fontSize: 15 }}>{meeting.name}</div>
        {meeting.description && (
          <div className="faint truncate" style={{ fontSize: 12.5, marginTop: 2 }}>
            {meeting.description}
          </div>
        )}
      </div>
      <div className="meeting-stats">
        <span>{meeting.participant_count} participant{meeting.participant_count === 1 ? '' : 's'}</span>
        <span>{meeting.token_count} words</span>
        <span>{meeting.completion_count} line{meeting.completion_count === 1 ? '' : 's'}</span>
      </div>
      <div className="faint" style={{ fontSize: 11, fontFamily: 'var(--mono)' }}>
        {meeting.grid_size}×{meeting.grid_size} · created {relativeTime(meeting.created_at)}
      </div>
    </button>
  )
}
