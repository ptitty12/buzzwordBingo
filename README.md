# Jargon Watch

Real-time, transcript-driven jargon tracking for meetings that could have been an email.

Participants draft a grid from a curated buzzword pool. A transcription pipeline streams the
meeting into the API word by word. Every grid carrying a spoken word marks itself —
instantly, over WebSocket — and the first participant to complete a line takes the standings.

The matching engine understands inflection, so **"synergies" marks _synergy_**,
**"leveraged" marks _leverage_**, and **"strategically" marks _strategic_**.

No accounts, no passwords: participants type a nickname to enter a meeting, and administrators
authenticate with a PIN. Words can be proposed mid-meeting and are judged by Claude.

> **On the vocabulary.** Rooms are *meetings*, people are *participants*, boards are *grids*,
> and a win is a *completed line*. That is deliberate: the obvious words for this app trip
> corporate web filters that block gambling, gaming and entertainment categories, which put
> the whole tool behind a proxy block for exactly the office audience it is built for.
> The terminology carries the same meaning without the false positive — please keep it when
> adding features. A migration renames legacy databases on first open, so nothing is lost.

```
┌── transcription ──┐      ┌──────── FastAPI ────────┐      ┌──── React ────┐
│  Zoom / Teams /   │ POST │  lexicon → match index  │  WS  │  live grid    │
│  Whisper / manual │─────▶│  n-gram phrase scanner  │─────▶│  ticker       │
└───────────────────┘      │  line detection + ranks │      │  standings    │
                           └───────────┬─────────────┘      └───────────────┘
                                       │ SQLite (WAL)
```

---

## Quick start

```bash
# 1. Backend
cd server
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m app.main            # http://localhost:8000

# 2. Frontend (separate terminal, from the repo root)
npm install
npm run dev:web                          # http://localhost:5173
```

On first boot the server seeds 135 buzzwords, a demo meeting, and an ingest API key —
**the key is printed to the log exactly once**:

```
WARNING  jargon  INGEST API KEY (shown once): jw_xxxxxxxxxxxxxxxxxxxxxxxx
INFO     jargon  ANTHROPIC_API_KEY unset — participant word suggestions will queue for admin review.
```

Open `/#/admin`, enter the console PIN (`2165` out of the box — change it), start a meeting,
and feed it:

```bash
python -m app.scripts.simulate --meeting DEMO1 --api-key jw_xxx --wpm 160
```

### Single-process deployment

```bash
npm run build            # builds the SPA into web/dist
npm start                # FastAPI serves the API *and* the built frontend on :8000
```

---

## How a word becomes a mark

This is the interesting part, and it is all in [`server/app/lexicon.py`](server/app/lexicon.py).

Every phrase — spoken token or grid word — resolves to a **set of match keys**. Two
phrases match when their key sets intersect:

| Spoken        | Keys                        | Grid word  | Keys                 | Match |
| ------------- | --------------------------- | ---------- | -------------------- | :---: |
| `synergies`   | `synergy`, `synerg`         | `synergy`  | `synergy`, `synerg`  |   ✅   |
| `leveraged`   | `leverag`                   | `leverage` | `leverag`            |   ✅   |
| `disruption`  | `disruption`, `disrupt`     | `disrupt`  | `disrupt`            |   ✅   |
| `energy`      | `energy`, `energ`           | `synergy`  | `synergy`, `synerg`  |   ❌   |

Two mechanisms, kept deliberately separate:

- **Inflection** (`stem`) applies *at most one* rule — adverbial `-ly` first, then
  exactly one of `-ing` / `-ed` / plural / comparative. Chaining every rule causes
  runaway over-stemming, so the step is exclusive.
- **Derivation** (`-ion`, `-ive`, `-ment`, `-ness`, `-ability`, …) emits *additional*
  keys rather than replacing the canonical stem. That is what lets `disruption` reach
  `disrupt` without `decision` hijacking `decide`.

The pipeline is lossy but **symmetric** — grid words and transcript tokens run through
identical code, so a word always matches itself no matter how aggressive the stemmer is.

**Multi-word phrases** are found by scanning every n-gram ending at the newest token
against a rolling window. "low hanging fruit" therefore matches even when the three
words arrive in three separate API calls, which is exactly how live captioning behaves.

Two escape hatches for the cases stemming cannot reach:

- **Aliases** — admin-managed extra surface forms per word (`value add` also matches
  `value-add`, `add value`).
- **Strict match** — a per-word flag that disables fuzzy matching entirely.

The admin console ships a **matcher sandbox** that shows the keys for any two
phrases and whether they match, which settles "but she definitely said it!" on the spot.

---

## The transcript API

One endpoint, deliberately forgiving about shape. Post a single word, a sentence, or a
whole paragraph — it is tokenised server-side either way.

```bash
curl -X POST http://localhost:8000/api/ingest \
  -H 'X-API-Key: jw_your_key' \
  -H 'Content-Type: application/json' \
  -d '{"text": "synergy", "meeting_code": "DEMO1"}'
```

```jsonc
{
  "accepted": true,
  "token_count": 1,
  "results": [{
    "meeting_id": "…", "meeting_name": "Q3 All-Hands",
    "tokens": ["synergy"],
    "hits":   [{ "nickname": "SynergySlayer", "word": "synergy", "position": 7, … }],
    "completions": [{ "nickname": "SynergySlayer", "label": "Row 1", "rank": 1, … }]
  }]
}
```

**Targeting.** Pass `meeting_id` or `meeting_code` to hit one room. Omit both and the token
fans out to *every live meeting* — what you want when one meeting drives every room in the
building.

**Ordering.** Each meeting serialises ingest behind its own lock and stamps every token
with a monotonic sequence number, because "first to complete a line" is only meaningful if tokens
apply in the order they were spoken.

Interactive reference: **`/api/docs`** (OpenAPI 3.1, generated from the Pydantic models) —
behind HTTP Basic, password = `ADMIN_PIN`. It is an operator surface, so it is not linked
anywhere a participant can see.

### Live event stream

```
ws://localhost:8000/ws/meetings/{meeting_id}?token={session_token}
```

| Event         | Fires when                                                        |
| ------------- | ----------------------------------------------------------------- |
| `hello`       | on connect — meeting state, standings, viewer count                   |
| `token`       | transcript arrived (each token carries its own seq and hit count)   |
| `marks`       | one or more squares were marked                                    |
| `completion`  | a participant completed a line                                    |
| `meeting`        | status changed (open → live → paused → ended)                     |
| `roster`      | a participant joined or locked in their grid                            |
| `standings`   | standings recomputed                                              |

Delivery is best-effort — a broken socket is dropped rather than blocking ingest, and
REST stays the source of truth, so a client that misses an event recovers on reconnect.

---

## Taking part

**There are no accounts.** The landing page lists the open meetings; picking one asks for a
nickname, and that nickname *is* the participant — scoped to that meeting, gone when the meeting is.
Join two meetings and you are two participants, free to be `SynergySlayer` in one and
`Deck Chair Rearranger` in the other.

1. **Pick a meeting** from the landing page. Anyone can watch the list; no sign-in screen.
2. **Claim a nickname.** Unique within the meeting, free everywhere else. Your token is
   remembered per meeting, so a refresh drops you back into the same seat.
3. **Draft a grid** — search and filter the pool, pick as many squares as you like, and
   anything you leave blank is auto-filled. "Fill the rest" fills the whole grid.
4. **Arrange it.** Drag a square onto another to swap them, or tap one and then tap its
   destination on touch. The preview is not a suggestion — the layout you build is the
   grid you are dealt.
5. **Lock in.** This is final: no redrafting, and word proposals close with it. Both
   would let you reshape your odds after hearing which words are landing.
6. **Wait for the meeting.** Squares light up as words are spoken.
7. **Complete a line** — any row, column or diagonal. Four corners and blackout count too.
   Ranking is first-to-complete, tie-broken on lines completed, then squares marked.

### Proposing a word

Participants are not stuck with the shipped pool. **✦ Propose a word** submits a term to an
LLM curator that decides, on the spot, whether it is genuine jargon:

```
✕ sales               "sales" is an ordinary business word, not jargon worth a square.
✓ crosspollination    Genuine consultant-speak — earns a square.  → 'cross-pollination'
✓ blamestorming       Genuine consultant-speak — earns a square.
✕ the                 "the" is a function word, not a buzzword.
```

Approved words land in the shared pool immediately, filed under a category and a
difficulty the model picked. The judge also normalises spelling — `crosspollination`
enters the pool as *cross-pollination*, with the participant's original spelling kept as an
alias so the transcript matches either. Each participant gets `SUGGESTIONS_PER_PARTICIPANT`
submissions per meeting (10 by default), and duplicates are caught before the model is
called. Proposals close the moment you lock in a grid — a word added after that could
only ever land on somebody else's.

Implementation: [`server/app/moderation.py`](server/app/moderation.py) — a single
`claude-opus-5` call with a JSON-schema-constrained response. **Every failure path
degrades to `pending`, never to approval**: no API key configured, a refusal, a network
error, a malformed response — all of them route the word to the admin queue instead. The
feature therefore works without an `ANTHROPIC_API_KEY`; it just becomes manual.

## Administering

Administrators have **no account** — the console PIN is the whole credential. `/#/admin`
asks for it, exchanges it for a signed admin token, and that token unlocks everything
below. Participants never see the Admin link, and never see the API reference either: the
integration surface is deliberately invisible to anyone taking part.

| Tab                 | What it does                                                          |
| ------------------- | --------------------------------------------------------------------- |
| **Overview**        | fleet metrics, integration snippet, recent activity                    |
| **Word pool**       | add / edit / bulk-import words, aliases, rarity, strict-match, disable |
| **Suggestions**     | the moderation queue — approve or reject what the judge deferred       |
| **Meetings**           | create, start, pause, end, reset, delete                               |
| **Grids**           | every participant's grid in every meeting, live                                |
| **Transcript feed** | inject transcript through the real ingest endpoint                     |
| **Participants**         | every nickname in every meeting, with rename and removal                  |
| **API keys**        | mint and revoke ingest keys (hashed at rest, shown once)               |
| **Audit log**       | every administrative mutation, with actor and timestamp                |

**Only administrators create meetings.** Participants join what exists; they cannot spin up rooms.

Deleting a word that is already dealt onto a grid **deactivates** it instead, so live
meetings keep working.

---

## Configuration

Copy `.env.example` to `.env`. Every value has a working default for local development.

| Variable                 | Default                 | Notes                                                 |
| ------------------------ | ----------------------- | ----------------------------------------------------- |
| `SECRET_KEY`             | *generated, then saved* | Set explicitly in production — see below               |
| `DATABASE_URL`           | `server/data/jargon.db`  | SQLite path, or `:memory:`                             |
| `ADMIN_PIN`              | `2165`                  | The admin credential. Empty **disables** the console   |
| `PROTECT_API_DOCS`       | `true`                  | Gate `/api/docs` behind HTTP Basic using the PIN       |
| `INGEST_REQUIRE_KEY`     | `true`                  | Require `X-API-Key` on `/api/ingest`                   |
| `ANTHROPIC_API_KEY`      | *(empty)*               | Enables the word judge; unset ⇒ suggestions queue      |
| `MODERATION_MODEL`       | `claude-opus-5`         | Model that judges proposed words                       |
| `SUGGESTIONS_PER_PARTICIPANT` | `10`                    | Word proposals allowed per participant, per meeting            |
| `CORS_ORIGINS`           | `localhost:5173`        | Comma-separated browser origins                        |
| `ENVIRONMENT`            | `development`           | `production` disables autoreload                       |

The API reference at `/api/docs` sits behind HTTP Basic with the admin PIN as the
password (any username). Participants who go looking find a 401, not the ingest contract.

### A note on the security model

The brief called for password-free play, and that is what this implements. There are two
kinds of identity and they are not comparable:

- **Participants** hold a signed token naming a per-meeting participant row. It is an **identity
  claim, not a secret** — HMAC-signed so nobody can promote themselves by editing
  `localStorage`, but anyone who can reach the server can claim any free nickname. That
  is correct for a party meeting and wrong for the public internet.
- **Administrators** hold a signed token minted only in exchange for `ADMIN_PIN`, which
  is compared with `hmac.compare_digest`. There is no admin row to impersonate and no
  password reset to phish. Leaving the PIN empty **denies every admin attempt** rather
  than opening the console — the failure mode is locked out, not wide open.

Ingest API keys are independent of both, stored as SHA-256 hashes and revocable, so a
transcription vendor never holds a participant or admin credential.

**On `SECRET_KEY`.** Both token types are signed with it, so changing it invalidates
every session at once. When it is unset, the server generates one on first boot and
writes it to `.secret_key` beside the database (mode `600`) rather than keeping it in
memory — a per-process key meant that any restart, including the autoreload that fires
when you edit `.env`, silently signed out every participant mid-draft. Set it explicitly in
production anyway: it belongs with your other secrets, and a multi-process deployment
needs every worker to agree on it.

---

## Development

```bash
npm run dev        # API (:8000) + Vite dev server (:5173) together
npm test           # 173 backend tests
npm run typecheck  # strict TypeScript, no emit
cd server && .venv/bin/python -m ruff check app tests
```

### Layout

```
server/app/
  lexicon.py      normalisation, stemming, match keys      ← the interesting bit
  engine.py       grid geometry, match index, ingest, win detection
  moderation.py   the LLM buzzword judge (fails closed to the admin queue)
  realtime.py     WebSocket fan-out
  security.py     PIN check, signed admin/participant tokens, API keys, dependencies
  models.py       Pydantic contracts (these become the OpenAPI schema)
  routers/        auth · words · meetings · ingest · admin · stream
  seed.py         idempotent starter pool + demo meeting
web/src/
  lib/            typed API client, hooks (routing, sockets, async)
  components/     design-system primitives, completion grid, ticker, word proposals
  views/          Landing · Join · Meeting (play + draft) · AdminGate · Admin
```

### Testing

The suite covers the layers most likely to break in embarrassing ways:

- **`test_lexicon.py`** — 81 cases. Every word matches itself; `-s`/`-ed`/`-ly`/`-ing`
  forms match their base; and a false-positive guard pins pairs that must *never*
  collide (`synergy` ≠ `energy`, `pivot` ≠ `private`, `deep dive` ≠ `deep`).
- **`test_engine.py`** — grid geometry and the 14 completed patterns.
- **`test_moderation.py`** — the judge with a stubbed model: approvals, rejections,
  canonical-spelling rewrites, and every fallback (no key, refusal, network error,
  malformed JSON) landing on `pending` rather than a silent approval.
- **`test_api.py`** — the full lifecycle end to end, plus authorization boundaries (a
  wrong PIN is refused; participants cannot create meetings, add words directly, read another
  participant's grid, cross meetings with one token, or reach admin routes) and the
  phrase-across-multiple-calls behaviour that live captioning depends on.

### Docker

```bash
docker build -t jargon-watch .
docker run -p 8000:8000 -e SECRET_KEY=$(openssl rand -hex 32) jargon-watch
```

---

## Design notes

**Why SQLite.** One writer, modest volume, and a strong ordering requirement. WAL mode
keeps the read-heavy grid and standings queries off the ingest path, and the whole
instance is a single file to back up. Swapping in Postgres means changing `db.py` only.

**Why an in-memory index.** Marking is the hot path: every token must be checked against
every square of every grid. `MeetingIndex` maps each match key to the cells carrying that
word, turning the check into a dict lookup rather than a scan over the whole meeting. It is
rebuilt lazily whenever grids or words change.

**Why the standings lists everyone.** Participants who have not hit completion still appear,
sorted below anyone who has, with a progress meter. Watching someone sit on 22/25 is most
of the fun.

## License

MIT
