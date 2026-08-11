/**
 * The nickname gate.
 *
 * Shown when you open a meeting you have not joined. This is the entire "sign-up": a
 * nickname, an optional glyph and colour, and you are in — scoped to this meeting only.
 */

import { useState } from 'react'

import { api } from '../lib/api'
import { useSession, useToast } from '../lib/store'
import { ErrorNote, StatusBadge } from '../components/ui'
import type { Accent, Meeting } from '../lib/types'

const ACCENTS: Accent[] = ['green', 'teal', 'cyan', 'violet', 'amber', 'lime', 'rose', 'sky']
const ACCENT_HEX: Record<Accent, string> = {
  green: '#3fcf8e',
  teal: '#2dd4bf',
  cyan: '#22d3ee',
  violet: '#a78bfa',
  amber: '#fbbf24',
  lime: '#a3e635',
  rose: '#fb7185',
  sky: '#38bdf8',
}
const AVATARS = ['◆', '◇', '▲', '△', '●', '○', '■', '□', '★', '✦', '⬢', '⬡']

export function Join({
  meeting,
  onJoined,
  onBack,
}: {
  meeting: Meeting
  onJoined: () => void
  onBack: () => void
}) {
  const { joinedMeeting } = useSession()
  const { push } = useToast()

  const [nickname, setNickname] = useState('')
  const [avatar, setAvatar] = useState(AVATARS[0])
  const [accent, setAccent] = useState<Accent>('green')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const session = await api.join(meeting.id, { nickname: nickname.trim(), avatar, accent })
      joinedMeeting(meeting.id, session.token, session.participant)
      push({
        kind: 'success',
        title: `You're in, ${session.participant.nickname}`,
        body: 'Draft a grid to get started.',
      })
      onJoined()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not join.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="gate-shell" data-accent={accent}>
      <div className="gate-panel">
        <div className="gate-hero">
          <div className="brand-mark" aria-hidden="true">
            <i /><i /><i /><i />
          </div>
          <h1>{meeting.name}</h1>
          <div className="row gap-8" style={{ justifyContent: 'center', marginTop: 10 }}>
            <span className="mono" style={{ color: 'var(--accent)', letterSpacing: '.16em' }}>
              {meeting.code}
            </span>
            <StatusBadge status={meeting.status} />
            <span className="faint" style={{ fontSize: 12 }}>
              {meeting.participant_count} participant{meeting.participant_count === 1 ? '' : 's'}
            </span>
          </div>
        </div>

        <div className="panel">
          <div className="panel-body">
            <form className="col gap-16" onSubmit={submit}>
              {error && <ErrorNote message={error} />}

              <div className="field">
                <label className="label" htmlFor="nickname">Pick a nickname</label>
                <input
                  id="nickname"
                  className="input"
                  value={nickname}
                  onChange={(event) => setNickname(event.target.value)}
                  placeholder="e.g. SynergySlayer"
                  maxLength={24}
                  autoFocus
                  autoComplete="off"
                />
                <span className="faint" style={{ fontSize: 11.5 }}>
                  Just for this meeting. Use the same nickname later to pick your grid back up.
                </span>
              </div>

              <div className="field">
                <span className="label">Avatar</span>
                <div className="avatar-picker">
                  {AVATARS.map((glyph) => (
                    <button
                      key={glyph}
                      type="button"
                      className={`avatar-opt${avatar === glyph ? ' active' : ''}`}
                      onClick={() => setAvatar(glyph)}
                      aria-label={`Avatar ${glyph}`}
                    >
                      {glyph}
                    </button>
                  ))}
                </div>
              </div>

              <div className="field">
                <span className="label">Accent</span>
                <div className="accent-picker">
                  {ACCENTS.map((option) => (
                    <button
                      key={option}
                      type="button"
                      className={`accent-swatch${accent === option ? ' active' : ''}`}
                      style={{ background: ACCENT_HEX[option] }}
                      onClick={() => setAccent(option)}
                      aria-label={option}
                    />
                  ))}
                </div>
              </div>

              <button
                className="btn btn-primary btn-lg btn-block"
                disabled={busy || nickname.trim().length < 2}
                type="submit"
              >
                {busy ? 'Joining…' : 'Enter the meeting'}
              </button>
            </form>
          </div>
        </div>

        <p style={{ textAlign: 'center', marginTop: 16 }}>
          <button className="btn btn-ghost btn-sm" onClick={onBack}>← All meetings</button>
        </p>
      </div>
    </div>
  )
}
