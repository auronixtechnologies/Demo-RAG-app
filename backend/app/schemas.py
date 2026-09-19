from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    content_type: str
    size_bytes: int
    num_chunks: int
    num_chars: int
    status: str
    error: str
    created_at: datetime


class Source(BaseModel):
    document_id: int
    filename: str
    chunk_index: int
    text: str
    score: float


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: str
    content: str
    used_rag: bool
    model: str
    sources: list[Source] = []
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: int | None = None
    use_rag: bool = False
    model: str | None = None
    top_k: int = Field(default=4, ge=1, le=12)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)


class ModelInfo(BaseModel):
    id: str
    label: str
    description: str


class StatusOut(BaseModel):
    groq_configured: bool
    default_model: str
    documents: int
    chunks: int
    embedding_model: str
