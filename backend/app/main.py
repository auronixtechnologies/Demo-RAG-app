import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from . import llm, vectorstore
from .config import settings
from .database import SessionLocal, init_db
from .models import Document
from .routers import chat, documents
from .schemas import StatusOut

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ragapp")

app = FastAPI(
    title="RAG Chat API",
    version="1.0.0",
    description=(
        "FastAPI + SQLite + ChromaDB + Groq. Chat with the RAG toggle off for a "
        "plain chatbot, or on to answer from uploaded documents."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(chat.router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    log.info("SQLite ready at %s", settings.sqlite_url)
    log.info("Chroma persisting to %s", settings.chroma_path)
    if not llm.is_configured():
        log.warning("GROQ_API_KEY is not set — chat endpoints will return 503.")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/status", response_model=StatusOut)
def status():
    db = SessionLocal()
    try:
        doc_count = db.scalar(select(func.count()).select_from(Document)) or 0
    finally:
        db.close()
    return StatusOut(
        groq_configured=llm.is_configured(),
        default_model=settings.groq_model,
        documents=doc_count,
        chunks=vectorstore.count(),
        embedding_model=vectorstore.EMBEDDING_MODEL,
    )


# --- Optional single-service mode -------------------------------------------
# If a built frontend is present next to the backend, serve it from the same
# process. On Render we deploy the frontend as a separate static site instead,
# so this block is simply skipped there.
_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=_static_dir / "assets"),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        # Never let the SPA fallback answer for the API — an unknown /api path
        # must still 404 as JSON rather than returning index.html.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")

        candidate = (_static_dir / full_path).resolve()
        # Guard against ../ traversal out of the static directory.
        if (
            full_path
            and candidate.is_file()
            and candidate.is_relative_to(_static_dir.resolve())
        ):
            return FileResponse(candidate)
        return FileResponse(_static_dir / "index.html")
