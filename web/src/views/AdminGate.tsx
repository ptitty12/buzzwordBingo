/**
 * The admin door: a PIN, and nothing else.
 *
 * There is no admin account to select — the PIN *is* the credential — so this screen
 * deliberately reveals nothing about the instance beyond its name.
 */

import { useState } from 'react'

import { api } from '../lib/api'
import { useSession, useToast } from '../lib/store'
import { ErrorNote } from '../components/ui'

export function AdminGate({ onBack }: { onBack: () => void }) {
  const { signInAdmin } = useSession()
  const { push } = useToast()

  const [pin, setPin] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const session = await api.adminSignIn(pin)
      signInAdmin(session.token)
      push({ kind: 'success', title: 'Administrator mode' })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed.')
      setPin('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-shell">
      <div className="login-card" style={{ maxWidth: 380 }}>
        <div className="login-hero">
          <div className="brand-mark" aria-hidden="true">
            <i /><i /><i /><i />
          </div>
          <h1>Administrator</h1>
          <p className="tag">Enter the console PIN to continue.</p>
        </div>

        <div className="panel">
          <div className="panel-body">
            <form className="col gap-16" onSubmit={submit}>
              {error && <ErrorNote message={error} />}

              <div className="field">
                <label className="label" htmlFor="pin">Console PIN</label>
                <input
                  id="pin"
                  className="input mono pin-input"
                  type="password"
                  inputMode="numeric"
                  value={pin}
                  onChange={(event) => setPin(event.target.value)}
                  placeholder="••••"
                  autoFocus
                  autoComplete="off"
                />
              </div>

              <button
                className="btn btn-primary btn-lg btn-block"
                disabled={busy || pin.length === 0}
                type="submit"
              >
                {busy ? 'Checking…' : 'Unlock console'}
              </button>
            </form>
          </div>
        </div>

        <p style={{ textAlign: 'center', marginTop: 16 }}>
          <button className="btn btn-ghost btn-sm" onClick={onBack}>← Back to games</button>
        </p>
      </div>
    </div>
  )
}
