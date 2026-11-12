# Haumex Labs Inc. AI Voice Caller

A standalone voice receptionist for Haumex Labs Inc. The caller presses **Call**, the line rings for exactly five seconds, the receptionist picks up and greets them, and they talk naturally. The caller can interrupt the assistant mid-sentence. Answers about the company come only from the PDFs in `knowledge/pdfs/`.

## How it works

```
Browser (TypeScript, no framework)                  FastAPI backend
──────────────────────────────────                  ─────────────────────────────────────────────
mic ─ AudioWorklet (16 kHz PCM16) ──── WebSocket ──▶ VAD (turn-taking + barge-in)
                                                     │  speech ended
                                                     ▼
                                                    STT ─▶ retrieval (ChromaDB) ─▶ OpenRouter (stream)
                                                                                     │ sentence by sentence
speaker ◀─ gapless scheduler ◀─ PCM packets ◀──────────────────────────────────── TTS (stream)
```

Latency is kept low by streaming at every stage: the LLM response is split into sentences as it streams, each sentence goes to TTS immediately, and TTS audio is forwarded to the browser chunk by chunk, so the caller hears the first sentence while the rest is still being generated.

**Interruptions (barge-in).** Voice activity detection runs on the server. When the caller starts speaking while the assistant is generating or audible, the server cancels the response task (which aborts the in-flight OpenRouter and TTS HTTP streams), tells the browser to flush its audio queue, and keeps the part of the reply that was actually spoken in the conversation, marked as interrupted, so the model answers the interruption rather than repeating itself. Every audio packet carries its response id, so stale audio that was already in flight is discarded.

**Echo.** Three layers keep the assistant's own voice from triggering a false interruption: the browser's acoustic echo cancellation (the microphone is opened with `echoCancellation`, and assistant audio is played through an `<audio>` element fed by a MediaStream, which lets Chrome's echo canceller see it); a stricter loudness threshold while the assistant is audible; and a longer minimum speech duration (320 ms) before a barge-in counts. Headphones remove the problem entirely. With loud laptop speakers and no echo cancellation, raise `VAD_BARGE_IN_THRESHOLD_DB`.

## Requirements

Python 3.11+, Node.js 20+ (22 recommended), and a current Chrome, Edge, Firefox or Safari. Microphone access requires `localhost` or HTTPS.

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

### Configuration (`backend/.env`)

| Setting | What to put there |
|---|---|
| `OPENROUTER_API_KEY` | Your OpenRouter key. Lives only on the backend. |
| `OPENROUTER_MODEL` | Any chat model ID from openrouter.ai/models, for example `openai/gpt-4o-mini`. `python scripts/check_environment.py --live` confirms the ID exists. |
| `STT_API_KEY`, `TTS_API_KEY` | Keys for the speech provider. The defaults use OpenAI's `gpt-4o-mini-transcribe` and `gpt-4o-mini-tts`. Any host with the same `/audio/transcriptions` and `/audio/speech` API works by changing `STT_BASE_URL` / `TTS_BASE_URL`. |
| `ADMIN_API_KEY` | A long random string. Required to trigger ingestion over HTTP. |
| `EMBEDDING_PROVIDER` | `fastembed` (default, local, no key; downloads ~130 MB once) or `openai_compatible` with `EMBEDDING_API_KEY`. |

Every other setting has a working default and is documented in `.env.example`. A project-root `.env` also works; `backend/.env` wins when both exist.

## Run

```powershell
# Terminal 1 - backend
cd haumex-ai-caller\backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2 - frontend
cd haumex-ai-caller\frontend
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` and the `/api/ws` WebSocket to the backend, so the browser only ever talks to one origin. Check health with:

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
cd backend;  python -m pytest -q          # 55 tests, no API keys needed
cd frontend; npm test                     # 34 tests (Vitest + jsdom)
cd frontend; npm run build                # strict type check + production build
```

All external services are replaced with fakes in the tests. The backend suite includes a full WebSocket call (greeting, speech, transcription, streamed answer, audio) and a barge-in test that talks over a slow greeting and checks that it is cancelled and recorded as interrupted.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Service status and which providers are configured (never exposes secrets) |
| POST | `/api/calls` | Create a call; returns `call_id`, one-time `session_token`, WebSocket path |
| DELETE | `/api/calls/{call_id}` | End a call and close its socket |
| GET | `/api/knowledge/status` | Indexed documents, chunks, failures |
| POST | `/api/knowledge/ingest` | Re-index (requires `X-Admin-Key`) |
| WS | `/api/ws/calls/{call_id}` | Real-time call. The first message must be `session.auth` with the token, so the token never appears in URLs or access logs. |

The message protocol is defined once per side, in `backend/app/schemas/message.py` and `frontend/src/types/websocket.ts`, and the two match field for field.

## Corrections and decisions relative to the brief

- **Company name.** All written branding uses "Haumex Labs Inc." The spoken greeting says "Haumex Labs" by default (`COMPANY_SPOKEN_NAME`), because speech engines read "Inc." awkwardly. Change either in `.env`.
- **The brief's example answers are not built in.** Lines like "Haumex Labs works across AI, software and scientific and engineering applications" would be invented facts if hard-coded. The assistant says only what the PDFs support and says plainly when it has no verified information.
- **OpenRouter is used for what it documents.** Chat completions run on OpenRouter. Speech-to-text, text-to-speech and embeddings are separate, swappable adapters rather than assumed OpenRouter endpoints. An optional `STT_PROVIDER=openrouter` adapter sends audio to OpenRouter as an `input_audio` content part; only enable it with a model whose listing on openrouter.ai shows audio input.
- **`CHUNK_SIZE` was ambiguous** (text or audio). Text chunking is `RAG_CHUNK_SIZE` (the old name still works); audio framing is `AUDIO_FRAME_MS`.
- **Frontend tests** were required but the structure had no folder for them, so `frontend/tests/` was added.
- **Files added to the structure** for clean dependencies: `backend/app/core/errors.py`, `backend/app/api/deps.py`, `backend/app/services/container.py`, `backend/app/tests/conftest.py`. Nothing was renamed or removed.
- **"Upload validation"** has no upload endpoint to apply to (documents are dropped into the folder), so it is applied at ingestion: PDF type, `MAX_PDF_SIZE_MB`, and no reading outside the knowledge folder.
- **Ringing is always exactly five seconds.** If the session is not ready when the ring ends, the screen shows "Connecting" for up to 10 more seconds; if setup fails during the ring, the ring stops immediately with a clear message.
- **Dropped connections end the call** with an explanation rather than silently reconnecting, since a half-restored voice session is worse than a clean redial.
- **Vite proxies to `127.0.0.1`**, not `localhost`, which avoids a common Windows problem where Node resolves `localhost` to IPv6 while Uvicorn listens on IPv4.

## Known limitations

- Voice activity detection is energy-based with an adaptive noise floor. It works well with a headset or normal laptop echo cancellation; very noisy rooms may need higher `VAD_SPEECH_THRESHOLD_DB`.
- Call sessions are held in memory, which suits one backend process. Running several workers would need a shared session store.
- `fastembed` downloads its model on first use; machines without internet access need the model cached in `data/vector_store/models` or `EMBEDDING_PROVIDER=openai_compatible`.
