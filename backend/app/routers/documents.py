from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import vectorstore
from ..config import settings
from ..database import get_db
from ..ingest import SUPPORTED_EXTENSIONS, UnsupportedFileError, chunk_text, extract_text
from ..models import Document
from ..schemas import DocumentOut

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)):
    stmt = select(Document).order_by(Document.created_at.desc())
    return db.scalars(stmt).all()


@router.post("/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    raw = await file.read()

    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than the {settings.max_upload_mb} MB limit.",
        )
    if not raw:
        raise HTTPException(status_code=400, detail="File is empty.")

    filename = file.filename or "upload"
    if not any(filename.lower().endswith(ext) for ext in SUPPORTED_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    try:
        text = extract_text(filename, raw)
    except UnsupportedFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Could not read the file: {exc}"
        ) from exc

    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail=(
                "No text could be extracted. If this is a scanned PDF it needs "
                "OCR before it can be indexed."
            ),
        )

    chunks = chunk_text(text)
    if not chunks:
        raise HTTPException(status_code=422, detail="File produced no usable chunks.")

    # Create the row first so chunk metadata can reference a real document id.
    doc = Document(
        filename=filename,
        content_type=file.content_type or "",
        size_bytes=len(raw),
        num_chars=len(text),
        num_chunks=0,
        status="ready",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    try:
        stored = vectorstore.add_chunks(doc.id, filename, chunks)
    except Exception as exc:
        doc.status = "failed"
        doc.error = str(exc)[:1000]
        db.commit()
        db.refresh(doc)
        raise HTTPException(
            status_code=500, detail=f"Embedding failed: {exc}"
        ) from exc

    doc.num_chunks = stored
    db.commit()
    db.refresh(doc)

    # Keep the original around for download/debugging.
    try:
        (settings.upload_path / f"{doc.id}_{filename}").write_bytes(raw)
    except OSError:
        pass  # non-fatal: the indexed text is what matters

    return doc


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    vectorstore.delete_document(document_id)

    for path in settings.upload_path.glob(f"{document_id}_*"):
        try:
            path.unlink()
        except OSError:
            pass

    db.delete(doc)
    db.commit()


@router.delete("", status_code=204)
def clear_documents(db: Session = Depends(get_db)):
    """Wipe the whole knowledge base — handy for demos."""
    vectorstore.reset_all()
    for doc in db.scalars(select(Document)).all():
        db.delete(doc)
    db.commit()
    for path in settings.upload_path.glob("*"):
        try:
            path.unlink()
        except OSError:
            pass


@router.post("/search")
def search_chunks(query: str, top_k: int = 4):
    """Retrieval-only endpoint. Useful for showing what RAG would pull in."""
    if not query.strip():
        raise HTTPException(status_code=400, detail="Query is required.")
    return vectorstore.search(query, top_k=top_k)
