/**
 * "Propose a buzzword" — the player's route into the word pool.
 *
 * The submission is judged by an LLM curator, so the interesting part of this UI is
 * the verdict: an approval shows the word as it was filed (including any spelling the
 * judge corrected), a rejection shows the reason in the judge's own words, and a
 * pending result explains that a human will look at it.
 */

import { useState } from 'react'

import { api } from '../lib/api'
import { useAsync } from '../lib/hooks'
import { useToast } from '../lib/store'
import { Difficulty, ErrorNote, Modal } from './ui'
import type { SuggestionResponse } from '../lib/types'

export function SuggestWordButton({ onWordAdded }: { onWordAdded?: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button className="btn btn-sm" onClick={() => setOpen(true)}>
        ✦ Propose a word
      </button>
      {open && <SuggestWordModal onClose={() => setOpen(false)} onWordAdded={onWordAdded} />}
    </>
  )
}

function SuggestWordModal({
  onClose,
  onWordAdded,
}: {
  onClose: () => void
  onWordAdded?: () => void
}) {
  const { push } = useToast()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<SuggestionResponse | null>(null)

  const history = useAsync(() => api.mySuggestions(), [])

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    setResult(null)
    setBusy(true)
    try {
      const response = await api.suggestWord(text.trim())
      setResult(response)
      setText('')
      history.reload()

      if (response.suggestion.status === 'approved') {
        push({
          kind: 'success',
          title: `“${response.word?.text}” is in the pool`,
          body: 'Everyone can draft it now.',
        })
        onWordAdded?.()
      } else if (response.suggestion.status === 'rejected') {
        push({ kind: 'info', title: 'Not buzzwordy enough', body: response.suggestion.verdict })
      } else {
        push({ kind: 'info', title: 'Sent for review' })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not submit that.')
    } finally {
      setBusy(false)
    }
  }

  const remaining = result?.remaining ?? history.data?.remaining ?? 0

  return (
    <Modal
      title="Propose a buzzword"
      onClose={onClose}
      footer={<button className="btn btn-ghost" onClick={onClose}>Done</button>}
    >
      <p className="dim" style={{ fontSize: 13 }}>
        Suggest a term for the shared word pool. An AI curator decides whether it is
        genuine jargon — <em>“business fundamentals”</em> gets in, <em>“sales”</em> does not.
      </p>

      <form className="row gap-8" onSubmit={submit}>
        <input
          className="input grow"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="e.g. cross-pollination"
          maxLength={48}
          autoFocus
          disabled={remaining <= 0 && Boolean(history.data)}
        />
        <button
          className="btn btn-primary"
          type="submit"
          disabled={busy || text.trim().length < 2 || (remaining <= 0 && Boolean(history.data))}
        >
          {busy ? 'Judging…' : 'Submit'}
        </button>
      </form>

      {history.data && (
        <div className="faint" style={{ fontSize: 11.5 }}>
          {remaining > 0
            ? `${remaining} of ${history.data.limit} suggestions left this game.`
            : 'You have used all of your suggestions for this game.'}
        </div>
      )}

      {error && <ErrorNote message={error} />}
      {result && <Verdict result={result} />}

      {history.data && history.data.suggestions.length > 0 && (
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Your submissions</div>
          <div className="col gap-6">
            {history.data.suggestions.map((entry) => (
              <div key={entry.id} className="row gap-8 suggestion-row">
                <span className={`badge badge-${statusTone(entry.status)}`}>{entry.status}</span>
                <span className="grow truncate" style={{ fontSize: 13 }}>
                  {entry.canonical || entry.text}
                </span>
                {entry.verdict && (
                  <span className="faint truncate" style={{ fontSize: 11.5, maxWidth: 180 }}>
                    {entry.verdict}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </Modal>
  )
}

function statusTone(status: string): string {
  if (status === 'approved') return 'live'
  if (status === 'rejected') return 'danger'
  return 'paused'
}

function Verdict({ result }: { result: SuggestionResponse }) {
  const { suggestion, word } = result

  if (suggestion.status === 'approved' && word) {
    return (
      <div className="verdict verdict-yes">
        <div className="row gap-8">
          <span className="verdict-icon">✓</span>
          <div className="grow">
            <div style={{ fontWeight: 640 }}>
              “{word.text}” is in
              {word.text.toLowerCase() !== suggestion.text.toLowerCase() && (
                <span className="faint" style={{ fontWeight: 400 }}>
                  {' '}— spelling corrected from “{suggestion.text}”
                </span>
              )}
            </div>
            <div className="dim" style={{ fontSize: 12.5, marginTop: 2 }}>{suggestion.verdict}</div>
            <div className="row gap-8" style={{ marginTop: 6 }}>
              <span className="badge badge-accent">{word.category}</span>
              <Difficulty level={word.difficulty} />
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (suggestion.status === 'rejected') {
    return (
      <div className="verdict verdict-no">
        <div className="row gap-8">
          <span className="verdict-icon">✕</span>
          <div>
            <div style={{ fontWeight: 640 }}>“{suggestion.text}” did not make the cut</div>
            <div className="dim" style={{ fontSize: 12.5, marginTop: 2 }}>{suggestion.verdict}</div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="verdict verdict-wait">
      <div className="row gap-8">
        <span className="verdict-icon">◷</span>
        <div>
          <div style={{ fontWeight: 640 }}>Sent to an administrator</div>
          <div className="dim" style={{ fontSize: 12.5, marginTop: 2 }}>{suggestion.verdict}</div>
        </div>
      </div>
    </div>
  )
}
