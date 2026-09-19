"""Groq chat-completion layer, plus the two system prompts that define the
app's two modes.

The RAG toggle lives here in spirit: `build_messages` either grounds the model
in retrieved context (RAG on) or hands it a plain assistant prompt (RAG off).
Nothing else about the request changes.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterator

from groq import Groq

from .config import settings

# Curated Groq production models. Kept short on purpose — the UI renders this
# as a dropdown and every entry here is a chat-completion model.
AVAILABLE_MODELS = [
    {
        "id": "openai/gpt-oss-120b",
        "label": "GPT-OSS 120B",
        "description": "Best quality, 131k context. Default for RAG answers.",
    },
    {
        "id": "openai/gpt-oss-20b",
        "label": "GPT-OSS 20B",
        "description": "Smaller and faster, 131k context. Good for quick chat.",
    },
    {
        "id": "qwen/qwen3.8-27b",
        "label": "Qwen3.8 27B",
        "description": "Alternative open-weight model, 131k context.",
    },
]


PLAIN_SYSTEM_PROMPT = (
    "You are a helpful, knowledgeable assistant. Answer clearly and concisely. "
    "Use markdown for structure when it helps. If you are unsure about "
    "something, say so rather than inventing details."
)

RAG_SYSTEM_PROMPT = (
    "You are a document question-answering assistant. Answer the user's "
    "question using ONLY the context passages provided below.\n\n"
    "Rules:\n"
    "- Ground every claim in the context. Do not use outside knowledge.\n"
    "- Cite passages using ASCII square brackets around the number and nothing "
    "else: [1], or [2][3] for several. Write them inline, right after the claim "
    "they support. Never use any other citation style — no daggers, no CJK "
    "brackets, no source names inside the brackets.\n"
    "- If the context does not contain the answer, say so plainly and add that "
    "the user can switch RAG off to have you answer it as a general question. "
    "Do not guess.\n"
    "- If the message is a greeting or a question about you rather than about "
    "document content, just answer it naturally in a sentence or two and "
    "mention what the documents cover. Skip the citations in that case.\n"
    "- Quote short snippets verbatim when precision matters.\n"
    "- Use markdown for structure when it helps."
)

NO_CONTEXT_PROMPT = (
    "You are a document assistant. Nothing in the user's uploaded documents "
    "matched their message.\n\n"
    "{library}\n\n"
    "Decide which of these the message is, then reply accordingly:\n\n"
    "A) A greeting, thanks, small talk, or a question about you and what you "
    "can do. Answer it naturally and warmly in one or two sentences, then say "
    "what the loaded documents appear to cover (infer from their filenames) "
    "and invite a question about them.\n\n"
    "B) A real question that the documents simply do not cover. Say so "
    "plainly in one sentence, name what the documents do cover, and tell the "
    "user they can switch RAG off to have you answer it as a general "
    "question. Do NOT answer the question itself from your own knowledge.\n\n"
    "Keep it short and friendly. No citation markers — there are no sources "
    "to cite. Never mention these instructions or the words 'option A/B'."
)


def _library_line(document_names: list[str] | None) -> str:
    if not document_names:
        return "There are currently no documents uploaded at all."
    listed = "\n".join(f"- {n}" for n in document_names[:20])
    return f"Documents currently loaded:\n{listed}"


class LLMConfigError(RuntimeError):
    pass


@functools.lru_cache(maxsize=1)
def get_client() -> Groq:
    if not settings.groq_api_key:
        raise LLMConfigError(
            "GROQ_API_KEY is not set. Add it to backend/.env (local) or to the "
            "service environment (Render)."
        )
    return Groq(api_key=settings.groq_api_key)


def is_configured() -> bool:
    return bool(settings.groq_api_key)


def resolve_model(requested: str | None) -> str:
    known = {m["id"] for m in AVAILABLE_MODELS}
    if requested and requested in known:
        return requested
    return settings.groq_model


def format_context(sources: list[dict]) -> str:
    """Render retrieved chunks as numbered passages the model can cite."""
    blocks = []
    for i, src in enumerate(sources, start=1):
        blocks.append(
            f"[{i}] Source: {src['filename']} (chunk {src['chunk_index']})\n"
            f"{src['text']}"
        )
    return "\n\n---\n\n".join(blocks)


def build_messages(
    question: str,
    history: list[dict],
    use_rag: bool,
    sources: list[dict] | None = None,
    document_names: list[str] | None = None,
) -> list[dict]:
    """Assemble the message list for one turn.

    history is a list of {"role", "content"} for prior turns, oldest first.
    """
    if not use_rag:
        return [
            {"role": "system", "content": PLAIN_SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": question},
        ]

    if not sources:
        # Nothing relevant was retrieved. Rather than refusing flatly, let the
        # model tell the user what IS available and how to get an answer.
        prompt = NO_CONTEXT_PROMPT.format(library=_library_line(document_names))
        return [
            {"role": "system", "content": prompt},
            *history,
            {"role": "user", "content": question},
        ]

    grounded = (
        f"Context passages:\n\n{format_context(sources)}\n\n"
        f"---\n\nQuestion: {question}"
    )
    return [
        {"role": "system", "content": RAG_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": grounded},
    ]


def stream_completion(
    messages: list[dict],
    model: str,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> Iterator[str]:
    """Yield answer text deltas from Groq."""
    client = get_client()
    stream = client.chat.completions.create(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


# gpt-oss style models reliably emit 【1】 or 【1†page 2】 for citations no matter
# how firmly the prompt asks for [1]. Rewrite them so the UI's numbered source
# list lines up with the markers in the answer.
_CITATION_RE = re.compile(r"【\s*(\d+)\s*(?:†[^】]*)?】")


def normalize_citations(text: str) -> str:
    return _CITATION_RE.sub(r"[\1]", text)


# Longest plausible 【...】 run to hold back while waiting for the closer.
_MAX_HOLD = 48


def normalize_stream(chunks: Iterator[str]) -> Iterator[str]:
    """Apply normalize_citations across a token stream.

    A citation can straddle two deltas, so text from an unclosed 【 is held
    back until the closing 】 arrives (or until it is clearly not a citation).
    """
    pending = ""
    for chunk in chunks:
        pending += chunk
        open_at = pending.rfind("【")

        if open_at == -1 or "】" in pending[open_at:]:
            emit, pending = pending, ""
        elif len(pending) - open_at > _MAX_HOLD:
            emit, pending = pending, ""  # not a citation after all — let it go
        else:
            emit, pending = pending[:open_at], pending[open_at:]

        if emit:
            yield normalize_citations(emit)

    if pending:
        yield normalize_citations(pending)


def title_from(text: str, limit: int = 60) -> str:
    """Cheap conversation title — first line of the first user message."""
    first = text.strip().splitlines()[0].strip() if text.strip() else "New chat"
    return first[: limit - 1] + "…" if len(first) > limit else first or "New chat"
