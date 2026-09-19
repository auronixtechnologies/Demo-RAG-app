# RAG Chat

A document Q&A app with a **RAG toggle**: flip it off and it is an ordinary
chatbot, flip it on and the same model answers only from the documents you
uploaded, with citations.

**Stack:** FastAPI · SQLite · ChromaDB · Groq · React (Vite)

---

## How it works

```
                          ┌──────────── RAG OFF ────────────┐
  your question  ─────────┤  system prompt + chat history   ├──► Groq ──► answer
                          └─────────────────────────────────┘

                          ┌──────────── RAG ON ─────────────┐
  your question  ──► embed ──► Chroma top-K ──► numbered ────┤ grounded prompt  ├──► Groq ──► answer + [1][2]
                              (cosine)          passages     └─────────────────┘
```

The toggle is a single flag (`use_rag`) on the chat request. It changes two
things and nothing else: whether retrieval runs, and which system prompt is
used. Same model, same endpoint, same conversation history.

### Where each piece of data lives

| Data | Store |
|---|---|
| Documents, chunk counts, conversations, messages, cited sources | **SQLite** (`data/app.db`) |
| Chunk text + embedding vectors | **ChromaDB** (`data/chroma/`) |
| Original uploaded files | `data/uploads/` |
| ONNX embedding model (~80MB, cached once) | `data/onnx_models/` |

### Embeddings

Groq serves chat completions only — it has **no embeddings endpoint**. So
embeddings are computed locally with Chroma's bundled ONNX
`all-MiniLM-L6-v2`. No API key, no second provider, CPU-only. The model
downloads once on first upload and is cached under `DATA_DIR`.

---

## Running locally

### 1. Backend

```powershell
cd backend
python -m venv ..\.venv
..\.venv\Scripts\python.exe -m pip install -r requirements.txt

copy .env.example .env      # then put your Groq key in it
..\.venv\Scripts\python.exe run.py
```

API on <http://127.0.0.1:8000>, interactive docs at `/docs`.

> `python app/main.py` will **not** work — `app/` is a package using relative
> imports. Use `run.py`, or `uvicorn app.main:app --reload` from `backend/`.

Get a free Groq key at <https://console.groq.com/keys>.

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev
```

UI on <http://localhost:5173>. Vite proxies `/api` to port 8000, so there is
no CORS setup in dev.

### Optional: one server instead of two

Build the frontend into the backend and skip Node at runtime:

```powershell
cd frontend; npm run build
Copy-Item -Recurse -Force dist ..\backend\static
```

FastAPI then serves the UI at <http://127.0.0.1:8000>. Delete
`backend/static` to go back to the two-server dev flow. (Note the copy goes
stale whenever you change the frontend — it is for demos, not development.)

---

## Using it

1. Drop a PDF/DOCX/TXT/MD/CSV/JSON into the sidebar. It is extracted,
   chunked (1000 chars, 150 overlap, on paragraph boundaries) and embedded.
2. **RAG off** — normal chatbot, documents ignored.
3. **RAG on** — top-K chunks are retrieved and injected; the answer cites
   them as `[1]`, `[2]`, and the sources expand below each reply with the
   exact text and a match score.
4. If the documents do not cover the question, RAG mode says so rather than
   falling back on the model's own knowledge — and offers an **Answer without
   RAG** button to re-ask the same question with retrieval off.

Chroma returns `top_k` rows however poor the match, so retrieval is filtered
by `MIN_SIMILARITY` first. Anything weaker is dropped, which means "0 sources"
is honest rather than four irrelevant chunks being passed off as citations.
Greetings and "what can you do?" are answered naturally even in RAG mode,
rather than being refused as uncovered by the documents.

The sidebar collapses via the button left of the toggle, and the collapsed
state persists across reloads.

### Models

Picked per message from the dropdown. Defaults to `openai/gpt-oss-120b`.

> Groq's catalogue changes — models get retired. If you get
> `model_not_found`, check `GET https://api.groq.com/openai/v1/models` for
> what your account can actually use and update `AVAILABLE_MODELS` in
> [backend/app/llm.py](backend/app/llm.py).

---

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness |
| `GET` | `/api/status` | Key configured?, doc/vector counts |
| `GET` | `/api/models` | Selectable models |
| `GET` | `/api/documents` | List documents |
| `POST` | `/api/documents/upload` | Upload + index (multipart) |
| `DELETE` | `/api/documents/{id}` | Delete one document and its vectors |
| `DELETE` | `/api/documents` | Wipe the knowledge base |
| `POST` | `/api/documents/search` | Retrieval only, no LLM |
| `POST` | `/api/chat` | Chat (SSE stream) |
| `GET` | `/api/conversations` | List chats |
| `GET` | `/api/conversations/{id}/messages` | Full transcript with sources |
| `DELETE` | `/api/conversations/{id}` | Delete a chat |

`POST /api/chat` streams Server-Sent Events:

```
event: meta     data: {conversation_id, model, used_rag, sources, user_message_id}
event: delta    data: {text}          ← repeated
event: done     data: {message_id}
event: error    data: {detail}        ← terminal, instead of done
```

---

## Deploying to Render

`render.yaml` defines two services:

| Service | Type | Notes |
|---|---|---|
| `rag-chat-api` | Python web service (free) | SQLite + Chroma in the container — ephemeral |
| `rag-chat-web` | Static site (free) | React build, SPA rewrite |

Both are on free-tier resources, so applying the Blueprint does **not** ask for
a card. Render demands payment details the moment a blueprint contains a paid
resource — a persistent disk, or any plan above `free`.

1. Push this repo to GitHub.
2. Render Dashboard → **New → Blueprint** → pick the repo.
3. When prompted, set:
   - `GROQ_API_KEY` on the API service
   - `VITE_API_URL` on the web service — the API's full URL, e.g.
     `https://rag-chat-api.onrender.com`
4. After the first deploy, tighten `CORS_ORIGINS` on the API from `*` to the
   static site's URL.

**Nothing persists on free.** There is no disk, so SQLite, the Chroma vector
store and uploaded files live in the container. They are wiped on every deploy
and on every spin-down after 15 minutes of idle. The app comes back with an
empty corpus and answers "0 sources" until you re-upload.

**Cold starts are slow.** After a spin-down the next request waits ~50s for the
container, and then pays another ~10–20s because the ~80MB ONNX embedding model
has to download again — its cache was ephemeral too.

**512MB RAM.** chromadb plus onnxruntime fits, but a PDF near the 20MB
`MAX_UPLOAD_MB` limit can OOM the instance while embedding, which restarts it
and loses the corpus.

If you need any of that fixed, the footer of [render.yaml](render.yaml) has the
exact three edits to go back to Starter + a 1GB disk (~$7.25/mo).

---

## Configuration

Backend, via `backend/.env` or the environment:

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | — | Required for chat |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Default model |
| `DATA_DIR` | `backend/data` | SQLite, Chroma, uploads, model cache |
| `CHUNK_SIZE` | `1000` | Characters per chunk |
| `CHUNK_OVERLAP` | `150` | Overlap between chunks |
| `TOP_K` | `4` | Chunks retrieved per question |
| `MIN_SIMILARITY` | `0.25` | Chunks below this cosine similarity are dropped |
| `MAX_UPLOAD_MB` | `20` | Upload size limit |
| `CORS_ORIGINS` | `*` | Comma-separated origins |

Frontend, via `frontend/.env`:

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_URL` | empty | Backend URL. Leave empty in dev to use the Vite proxy. |

---

## Notes

- **Scanned PDFs** have no text layer and are rejected with a message saying
  so. OCR would be a separate step.
- **Citation style** — gpt-oss models emit `【1】` no matter what the prompt
  says, so the backend rewrites those to `[1]` mid-stream
  (`normalize_stream` in [backend/app/llm.py](backend/app/llm.py)).
- **Chroma 1.x** is required. Earlier versions pull in `chroma-hnswlib`,
  which has no prebuilt wheel for Python 3.13 on Windows and needs MSVC to
  compile. Chroma 1.x ships a prebuilt Rust core instead.
- **Cosine distance** is set explicitly on the collection; Chroma's default
  is L2, which would make the displayed match scores meaningless.
- `backend/.env` is gitignored. Keep real keys out of `.env.example`.
