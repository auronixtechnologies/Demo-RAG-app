"""Chroma wrapper.

Embeddings are computed locally with Chroma's bundled ONNX all-MiniLM-L6-v2
model. That matters here: Groq only serves chat completions, it has no
embeddings endpoint, so the vector side has to stand on its own with no API key.
The model (~80MB) is downloaded once on first use and cached under the data dir.
"""

from __future__ import annotations

import functools
import logging
import threading

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.utils import embedding_functions

from .config import settings

log = logging.getLogger(__name__)

COLLECTION_NAME = "documents"
EMBEDDING_MODEL = "all-MiniLM-L6-v2 (ONNX, local)"

_lock = threading.Lock()


@functools.lru_cache(maxsize=1)
def _client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(
        path=settings.chroma_path,
        settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
    )


@functools.lru_cache(maxsize=1)
def _embedding_function():
    """Chroma's local ONNX MiniLM, with its model cache moved onto our data dir.

    By default the ~80MB model downloads to ``Path.home()/.cache/chroma``, which
    is not persisted on Render — that means a fresh download on every cold
    start. Pointing DOWNLOAD_PATH at the mounted disk downloads it once.
    """
    # DefaultEmbeddingFunction is a lazy protocol stub with no settable cache
    # path; ONNXMiniLM_L6_V2 is the concrete class behind it and exposes one.
    onnx_cls = getattr(embedding_functions, "ONNXMiniLM_L6_V2", None)
    if onnx_cls is None:
        log.warning("ONNXMiniLM_L6_V2 unavailable; using the default cache path.")
        return embedding_functions.DefaultEmbeddingFunction()

    ef = onnx_cls()
    try:
        cache_dir = settings.data_path / "onnx_models" / onnx_cls.MODEL_NAME
        cache_dir.mkdir(parents=True, exist_ok=True)
        ef.DOWNLOAD_PATH = cache_dir
        log.info("ONNX embedding model cached at %s", cache_dir)
    except Exception as exc:  # chroma internals changed — keep the default path
        log.warning("Could not relocate the ONNX model cache: %s", exc)
    return ef


@functools.lru_cache(maxsize=1)
def get_collection():
    # Cosine similarity, not Chroma's L2 default — the score shown in the UI
    # assumes a 0..2 cosine distance.
    kwargs = {"configuration": {"hnsw": {"space": "cosine"}}}
    try:
        return _client().get_or_create_collection(
            name=COLLECTION_NAME, embedding_function=_embedding_function(), **kwargs
        )
    except TypeError:
        # Older chroma releases only accept the legacy metadata form.
        return _client().get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=_embedding_function(),
            metadata={"hnsw:space": "cosine"},
        )


def add_chunks(document_id: int, filename: str, chunks: list[str]) -> int:
    """Embed and store chunks. Returns the number stored."""
    if not chunks:
        return 0

    collection = get_collection()
    ids = [f"doc{document_id}-chunk{i}" for i in range(len(chunks))]
    metadatas = [
        {"document_id": document_id, "filename": filename, "chunk_index": i}
        for i in range(len(chunks))
    ]

    # Batch so a large PDF doesn't blow up memory during embedding.
    batch = 64
    with _lock:
        for start in range(0, len(chunks), batch):
            end = start + batch
            collection.add(
                ids=ids[start:end],
                documents=chunks[start:end],
                metadatas=metadatas[start:end],
            )
    return len(chunks)


def delete_document(document_id: int) -> None:
    with _lock:
        get_collection().delete(where={"document_id": document_id})


def reset_all() -> None:
    with _lock:
        client = _client()
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:  # collection may not exist yet
            pass
    get_collection.cache_clear()
    get_collection()


def count() -> int:
    try:
        return get_collection().count()
    except Exception:
        return 0


def search(
    query: str, top_k: int = 4, min_score: float | None = None
) -> list[dict]:
    """Return the most similar chunks as dicts the API can serialise.

    Hits scoring below ``min_score`` are discarded: Chroma returns top_k rows
    regardless of quality, so without this an unrelated question still comes
    back with k "sources" and the model is handed pure noise.
    """
    if min_score is None:
        min_score = settings.min_similarity

    collection = get_collection()
    total = collection.count()
    if total == 0:
        return []

    result = collection.query(
        query_texts=[query],
        n_results=min(top_k, total),
        include=["documents", "metadatas", "distances"],
    )

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    hits: list[dict] = []
    for text, meta, distance in zip(documents, metadatas, distances):
        meta = meta or {}
        # cosine distance -> rough 0..1 similarity for display
        score = round(max(0.0, 1.0 - float(distance)), 4)
        if score < min_score:
            continue
        hits.append(
            {
                "document_id": int(meta.get("document_id", 0)),
                "filename": str(meta.get("filename", "unknown")),
                "chunk_index": int(meta.get("chunk_index", 0)),
                "text": text or "",
                "score": score,
            }
        )
    return hits
