/**
 * Sign-in. No passwords by design: pick an existing handle from the dropdown, or
 * create a new one. Admin accounts may additionally require a PIN when the server
 * has one configured.
 */

import { useEffect, useMemo, useState } from 'react'

import { api } from '../lib/api'
import { useAsync } from '../lib/hooks'
import { useSession, useToast } from '../lib/store'
import { Avatar, ErrorNote } from '../components/ui'
import type { Accent } from '../lib/types'

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

export function Login() {
  const { signIn } = useSession()
  const { push } = useToast()

  const [mode, setMode] = useState<'existing' | 'new'>('existing')
  const [selectedId, setSelectedId] = useState('')
  const [pin, setPin] = useState('')
  const [nickname, setNickname] = useState('')
  const [avatar, setAvatar] = useState(AVATARS[0])
  const [accent, setAccent] = useState<Accent>('green')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const accounts = useAsync(() => api.listAccounts(), [])
  const config = useAsync(() => api.authConfig(), [])

  // Default the dropdown to the most recently active account, and switch to the
  // create form automatically on a brand-new instance.
  useEffect(() => {
    if (!accounts.data) return
    if (accounts.data.length === 0) setMode('new')
    else if (!selectedId) setSelectedId(accounts.data[0].id)
  }, [accounts.data, selectedId])

  const selected = useMemo(
    () => accounts.data?.find((account) => account.id === selectedId),
    [accounts.data, selectedId],
  )
  const needsPin = Boolean(selected?.is_admin && config.data?.admin_pin_required)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (mode === 'existing') {
        if (!selectedId) throw new Error('Pick an account to continue.')
        const session = await api.signIn({ user_id: selectedId, admin_pin: pin })
        signIn(session)
        push({ kind: 'success', title: `Welcome back, ${session.user.nickname}` })
      } else {
        const session = await api.signUp({ nickname: nickname.trim(), avatar, accent })
        signIn(session)
        push({ kind: 'success', title: `You're in, ${session.user.nickname}`, body: 'Draft a card to start playing.' })
      }
      window.location.hash = '/lobby'
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-shell" data-accent={accent}>
      <div className="login-card">
        <div className="login-hero">
          <div className="brand-mark" aria-hidden="true">
            <i /><i /><i /><i />
          </div>
          <h1>Buzzword Bingo</h1>
          <p className="tag">
            Live transcript. Real-time cards. First to five wins.
          </p>
        </div>

        <div className="panel">
          <div className="panel-body">
            <form className="col gap-16" onSubmit={submit}>
              <div className="seg" role="tablist">
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === 'existing'}
                  className={mode === 'existing' ? 'active' : ''}
                  onClick={() => { setMode('existing'); setError(null) }}
                >
                  Sign in
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === 'new'}
                  className={mode === 'new' ? 'active' : ''}
                  onClick={() => { setMode('new'); setError(null) }}
                >
                  Create account
                </button>
              </div>

              {error && <ErrorNote message={error} />}

              {mode === 'existing' ? (
                <>
                  <div className="field">
                    <label className="label" htmlFor="account">Your handle</label>
                    {accounts.loading ? (
                      <div className="input faint">Loading accounts…</div>
                    ) : accounts.data && accounts.data.length > 0 ? (
                      <select
                        id="account"
                        className="select"
                        value={selectedId}
                        onChange={(event) => setSelectedId(event.target.value)}
                      >
                        {accounts.data.map((account) => (
                          <option key={account.id} value={account.id}>
                            {account.avatar} {account.nickname}
                            {account.is_admin ? ' — admin' : ''}
                            {account.games_played > 0
                              ? ` · ${account.games_played} card${account.games_played === 1 ? '' : 's'}`
                              : ''}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <div className="input faint">No accounts yet — create one.</div>
                    )}
                  </div>

                  {selected && (
                    <div className="row gap-12" style={{ padding: '2px 2px' }}>
                      <Avatar user={selected} size="lg" />
                      <div className="grow">
                        <div style={{ fontWeight: 620 }}>{selected.nickname}</div>
                        <div className="faint" style={{ fontSize: 12 }}>
                          {selected.games_played} card{selected.games_played === 1 ? '' : 's'} played
                          {selected.is_admin && ' · administrator'}
                        </div>
                      </div>
                    </div>
                  )}

                  {needsPin && (
                    <div className="field">
                      <label className="label" htmlFor="pin">Administrator PIN</label>
                      <input
                        id="pin"
                        className="input mono"
                        type="password"
                        value={pin}
                        onChange={(event) => setPin(event.target.value)}
                        placeholder="••••"
                        autoComplete="off"
                      />
                    </div>
                  )}

                  <button
                    className="btn btn-primary btn-lg btn-block"
                    disabled={busy || !selectedId}
                    type="submit"
                  >
                    {busy ? 'Signing in…' : 'Enter the meeting'}
                  </button>
                </>
              ) : (
                <>
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
                      No password, no email. Your handle is your account.
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
                    {busy ? 'Creating…' : 'Create account'}
                  </button>
                </>
              )}
            </form>
          </div>
        </div>

        <p className="faint" style={{ textAlign: 'center', marginTop: 16, fontSize: 11.5 }}>
          {config.data?.environment === 'production' ? 'Production' : 'Development'} instance ·{' '}
          <a href="/api/docs" target="_blank" rel="noreferrer">API docs</a>
        </p>
      </div>
    </div>
  )
}
