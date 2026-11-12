# Haumex Labs Inc. AI Voice Caller

A standalone voice receptionist for Haumex Labs Inc. The caller presses **Call**, the line rings for exactly five seconds, the receptionist picks up and greets them, and they talk naturally. The caller can interrupt the assistant mid-sentence. Answers about the company come only from the PDFs in `knowledge/pdfs/`.

## How it works

```
Browser (TypeScript, no framework)                         FastAPI backend
──────────────────────────────────────                     ──────────────────────────────────
mic ─▶ browser speech recognition ─▶ caller's words ──text──▶ retrieval (ChromaDB, your PDFs)
                                                              │
                                                              ▼
speaker ◀─ browser speech synthesis ◀─ reply sentences ◀─text─ OpenRouter (streamed text)
```

Speech-to-text and text-to-speech both happen in the caller's browser. The WebSocket carries only text, and OpenRouter only ever receives and returns text; no audio is sent to OpenRouter or to the backend.

It stays fast because everything streams: the browser shows and uses the caller's words while they are still speaking, the backend streams OpenRouter's reply and cuts it into sentences as it arrives, and the browser starts speaking the first sentence while the rest is still being written.

**Interruptions (barge-in).** As soon as the browser hears the caller say at least two words while the receptionist is talking, it stops speaking instantly and tells the backend, which cancels the OpenRouter request. The part of the reply that was already spoken stays in the conversation, marked as interrupted, so the next answer responds to the interruption instead of repeating itself.

**Echo.** The receptionist's own voice coming out of the speakers can be picked up by the microphone. The browser compares what it hears with what the receptionist is saying (and has said in the last two seconds) and ignores it when it is mostly the same words. Headphones remove the problem entirely.

## Requirements

Python 3.11+, Node.js 20.14+ (22 LTS recommended), and **Google Chrome or Microsoft Edge** for the call page. Their built-in speech recognition is what listens to the caller; Firefox has none, and Safari's is limited. Chrome and Edge perform that recognition with their vendor's online speech service, so the caller's computer needs internet access. Microphone access requires `localhost` or HTTPS.

## Setup on Windows (PowerShell)

```powershell
# 1. Backend
cd haumex-ai-caller\backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env                           # fill in the keys described below

# 2. Frontend (new PowerShell window)
cd haumex-ai-caller\frontend
npm install
```

The frontend uses no bundler or framework. `npm install` only installs the TypeScript compiler (plus jsdom and type definitions for the tests).

### Configuration (`backend/.env`)

OpenRouter is the only AI service, and it only handles text.

| Setting | What to put there |
|---|---|
| `OPENROUTER_API_KEY` | Your OpenRouter key. Lives only on the backend. |
| `OPENROUTER_MODEL` | Any chat model ID from https://openrouter.ai/models. It only needs text in and text out. |
| `SPEECH_LANGUAGE` | Language the browser listens and speaks in, for example `en-US` or `en-GB`. |
| `SPEECH_RATE` | Speaking speed, `1.0` is normal (0.5 to 2.0). |
| `ADMIN_API_KEY` | A long random string. Required to trigger PDF ingestion over HTTP. |

The receptionist's voice is the most natural voice installed in the browser for that language (Chrome and Edge include good online voices). PDF search uses a small open model that runs on your own computer (`fastembed`, `BAAI/bge-small-en-v1.5`); it needs no account or key and downloads about 130 MB once.

After editing `.env`, fully restart the backend (Ctrl+C, then start it again) and run `python ..\scripts\check_environment.py --live` from `backend\` to confirm the model ID exists on OpenRouter and the key works.

Every other setting has a working default and is documented in `.env.example`.

## Run

```powershell
# Terminal 1 - backend
cd haumex-ai-caller\backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2 - frontend
cd haumex-ai-caller\frontend
npm start
```

`npm start` compiles the TypeScript with `tsc` into `frontend/dist/`, then runs `frontend/server.mjs`, a small server built only on Node's standard library. Open http://localhost:5173. The server serves the page and forwards `/api` requests and the `/api/ws` WebSocket to the backend, so the browser only ever talks to one address and no secret reaches it. Optional settings: `$env:PORT=5174` to change the port, `$env:BACKEND_URL="http://127.0.0.1:8001"` if the backend runs elsewhere. Check health with:

```powershell
Invoke-RestMethod http://localhost:8000/api/health
python ..\scripts\check_environment.py --live     # from backend\ with the venv active
```

## Company knowledge

Put PDFs anywhere under `knowledge/pdfs/`, in any subfolders, with no categories needed. The backend indexes them at startup and re-checks the folder every 60 seconds (`KNOWLEDGE_WATCH_INTERVAL_SECONDS`). Only new or changed files are processed; deleted files are removed from the index; byte-identical copies are indexed once; changing the embedding model rebuilds the index automatically.

```powershell
# from the project root, backend venv active
python scripts\ingest_documents.py                 # incremental
python scripts\ingest_documents.py --force         # rebuild everything
python scripts\ingest_documents.py --via-api http://127.0.0.1:8000   # while the backend is running
Invoke-RestMethod http://localhost:8000/api/knowledge/status
```

Use `--via-api` (or `POST /api/knowledge/ingest` with header `X-Admin-Key`) while the backend is running, so only one process writes to the vector store. Scanned PDFs without a text layer are reported as "no extractable text"; OCR is not included.

## Tests

```powershell
cd backend;  python -m pytest -q          # 49 tests, no API keys needed
cd frontend; npm test                     # 48 tests (Node's built-in test runner + jsdom)
cd frontend; npm run typecheck            # strict type check of app and tests
```

All external services, browser speech included, are replaced with fakes in the tests. They cover a full call (greeting, caller text, streamed answer sentences), barge-in while the receptionist is speaking, the caller continuing before a reply starts, speaker-echo filtering, and a check that only text is ever sent to OpenRouter.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Service status and which providers are configured (never exposes secrets) |
| POST | `/api/calls` | Create a call; returns `call_id`, one-time `session_token`, WebSocket path |
| DELETE | `/api/calls/{call_id}` | End a call and close its socket |
| GET | `/api/knowledge/status` | Indexed documents, chunks, failures |
| POST | `/api/knowledge/ingest` | Re-index (requires `X-Admin-Key`) |
| WS | `/api/ws/calls/{call_id}` | Real-time call, text messages only. The first message must be `session.auth` with the token, so the token never appears in URLs or access logs. |

The message protocol is defined once per side, in `backend/app/schemas/message.py` and `frontend/src/types/websocket.ts`, and the two match field for field.

## Corrections and decisions relative to the brief

- **Company name.** All written branding uses "Haumex Labs Inc." The spoken greeting says "Haumex Labs" by default (`COMPANY_SPOKEN_NAME`), because speech engines read "Inc." awkwardly. Change either in `.env`.
- **The brief's example answers are not built in.** Lines like "Haumex Labs works across AI, software and scientific and engineering applications" would be invented facts if hard-coded. The assistant says only what the PDFs support and says plainly when it has no verified information.
- **Speech runs in the browser; OpenRouter is text only.** The browser's built-in speech recognition and speech synthesis do all listening and speaking. The backend and OpenRouter receive and return text only, and one `OPENROUTER_MODEL` does all the answering.
- **`CHUNK_SIZE` was ambiguous** (text or audio). It is now `RAG_CHUNK_SIZE` for PDF text chunks; the old name still works.
- **Frontend tests** were required but the structure had no folder for them, so `frontend/tests/` was added.
- **Files added to the structure** for clean dependencies: `backend/app/core/errors.py`, `backend/app/api/deps.py`, `backend/app/services/container.py`, `backend/app/tests/conftest.py`. Nothing was renamed or removed.
- **"Upload validation"** has no upload endpoint to apply to (documents are dropped into the folder), so it is applied at ingestion: PDF type, `MAX_PDF_SIZE_MB`, and no reading outside the knowledge folder.
- **Ringing is always exactly five seconds.** If the session is not ready when the ring ends, the screen shows "Connecting" for up to 10 more seconds; if setup fails during the ring, the ring stops immediately with a clear message.
- **Dropped connections end the call** with an explanation rather than silently reconnecting, since a half-restored voice session is worse than a clean redial.
- **No Vite or other bundler.** TypeScript is compiled by `tsc` and loaded by the browser as native ES modules; `frontend/server.mjs` (Node standard library only) serves the page and forwards the API and WebSocket. Tests run on Node's built-in `node:test`. Added files: `frontend/server.mjs`, `frontend/run-tests.mjs`, `frontend/tsconfig.test.json`.
- **The frontend server forwards to `127.0.0.1`**, not `localhost`, which avoids a common Windows problem where Node resolves `localhost` to IPv6 while Uvicorn listens on IPv4.

## Known limitations

- The call page needs Chrome or Edge, which recognize speech through their vendor's online service (internet required). Recognition quality, punctuation and pause detection are the browser's.
- The voice depends on what the browser offers. Chrome and Edge have natural online voices; plain offline system voices sound more robotic.
- Echo filtering compares words, so with loud speakers a caller who repeats the receptionist's exact words may be ignored. Headphones avoid this.
- Call sessions are held in memory, which suits one backend process. Running several workers would need a shared session store.
- `fastembed` downloads its model on first use; machines without internet access need the model cached in `data/vector_store/models`.
