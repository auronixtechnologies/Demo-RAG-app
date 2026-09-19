import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm, vectorstore
from ..database import SessionLocal, get_db
from ..models import Conversation, Document, Message, utcnow
from ..schemas import ChatRequest, ConversationOut, MessageOut, ModelInfo

router = APIRouter(prefix="/api", tags=["chat"])

# How many prior turns to replay to the model.
HISTORY_TURNS = 8


def _serialise(msg: Message) -> dict:
    data = MessageOut.model_validate(msg).model_dump(mode="json")
    data["sources"] = json.loads(msg.sources_json) if msg.sources_json else []
    return data


def _history(db: Session, conversation_id: int) -> list[dict]:
    """Recent turns as plain role/content pairs.

    Prior RAG answers are replayed without their context blocks — only the
    answer text — so the window stays small and old context can't leak into a
    new question's grounding.
    """
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(HISTORY_TURNS * 2)
    )
    rows = list(db.scalars(stmt).all())[::-1]
    return [{"role": m.role, "content": m.content} for m in rows]


@router.get("/models", response_model=list[ModelInfo])
def list_models():
    return llm.AVAILABLE_MODELS


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(db: Session = Depends(get_db)):
    stmt = select(Conversation).order_by(Conversation.updated_at.desc())
    return db.scalars(stmt).all()


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(db: Session = Depends(get_db)):
    conv = Conversation(title="New chat")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


@router.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return [_serialise(m) for m in db.scalars(stmt).all()]


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    db.delete(conv)
    db.commit()


@router.post("/chat")
def chat(req: ChatRequest):
    """Streaming chat over Server-Sent Events.

    Event sequence:
      meta   -> {conversation_id, model, used_rag, sources, user_message_id}
      delta  -> {text}                (repeated)
      done   -> {message_id}
      error  -> {detail}              (terminal, instead of done)
    """
    if not llm.is_configured():
        raise HTTPException(
            status_code=503,
            detail="GROQ_API_KEY is not configured on the server.",
        )

    model = llm.resolve_model(req.model)

    # The DB session is opened inside the generator: the response outlives the
    # request scope, so a Depends-provided session would already be closed.
    def event_stream() -> Iterator[str]:
        db = SessionLocal()
        try:
            conv = (
                db.get(Conversation, req.conversation_id)
                if req.conversation_id
                else None
            )
            if conv is None:
                conv = Conversation(title=llm.title_from(req.message))
                db.add(conv)
                db.commit()
                db.refresh(conv)
            elif conv.title == "New chat":
                conv.title = llm.title_from(req.message)

            history = _history(db, conv.id)

            user_msg = Message(
                conversation_id=conv.id,
                role="user",
                content=req.message,
                used_rag=req.use_rag,
                model=model,
            )
            db.add(user_msg)
            db.commit()
            db.refresh(user_msg)

            # --- Retrieval happens only when the RAG toggle is on ---
            sources: list[dict] = []
            if req.use_rag:
                try:
                    sources = vectorstore.search(req.message, top_k=req.top_k)
                except Exception as exc:
                    yield _sse("error", {"detail": f"Retrieval failed: {exc}"})
                    return

            yield _sse(
                "meta",
                {
                    "conversation_id": conv.id,
                    "conversation_title": conv.title,
                    "model": model,
                    "used_rag": req.use_rag,
                    "sources": sources,
                    "user_message_id": user_msg.id,
                },
            )

            # When retrieval comes back empty the model still needs to know what
            # the user *could* ask about, so it can say something useful.
            document_names: list[str] = []
            if req.use_rag and not sources:
                document_names = list(
                    db.scalars(
                        select(Document.filename).order_by(Document.created_at.desc())
                    ).all()
                )

            messages = llm.build_messages(
                question=req.message,
                history=history,
                use_rag=req.use_rag,
                sources=sources,
                document_names=document_names,
            )

            parts: list[str] = []
            try:
                raw = llm.stream_completion(
                    messages, model=model, temperature=req.temperature
                )
                for delta in llm.normalize_stream(raw):
                    parts.append(delta)
                    yield _sse("delta", {"text": delta})
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                # Persist whatever arrived so the transcript isn't lost.
                if parts:
                    partial = llm.normalize_citations("".join(parts))
                    _save_assistant(db, conv, partial, req, model, sources)
                yield _sse("error", {"detail": detail})
                return

            answer = llm.normalize_citations("".join(parts).strip()) or "(empty response)"
            assistant = _save_assistant(db, conv, answer, req, model, sources)
            yield _sse("done", {"message_id": assistant.id})
        finally:
            db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # stops nginx/proxy buffering the stream
        },
    )


def _save_assistant(
    db: Session,
    conv: Conversation,
    content: str,
    req: ChatRequest,
    model: str,
    sources: list[dict],
) -> Message:
    msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content=content,
        used_rag=req.use_rag,
        model=model,
        sources_json=json.dumps(sources) if sources else "",
    )
    db.add(msg)
    conv.updated_at = utcnow()  # bumps the conversation to the top of the list
    db.commit()
    db.refresh(msg)
    return msg


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
