import { useCallback, useEffect, useRef, useState } from 'react'
import { api, streamChat } from './api.js'
import ConversationPanel from './components/ConversationPanel.jsx'
import DocumentPanel from './components/DocumentPanel.jsx'
import Message from './components/Message.jsx'

export default function App() {
  const [status, setStatus] = useState(null)
  const [models, setModels] = useState([])
  const [model, setModel] = useState('')

  const [documents, setDocuments] = useState([])
  const [conversations, setConversations] = useState([])
  const [conversationId, setConversationId] = useState(null)
  const [messages, setMessages] = useState([])

  // The RAG switch. false => plain chatbot, true => answer from documents.
  const [useRag, setUseRag] = useState(false)
  const [topK, setTopK] = useState(4)

  const [sidebarOpen, setSidebarOpen] = useState(
    () => localStorage.getItem('sidebarOpen') !== 'false'
  )

  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [toast, setToast] = useState(null)

  const abortRef = useRef(null)
  const scrollRef = useRef(null)
  const textareaRef = useRef(null)

  const notify = useCallback((text, kind = 'error') => {
    setToast({ text, kind })
    setTimeout(() => setToast(null), 5000)
  }, [])

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await api.status())
    } catch {
      setStatus(null)
    }
  }, [])

  const refreshDocuments = useCallback(async () => {
    try {
      setDocuments(await api.listDocuments())
    } catch (err) {
      notify(err.message)
    }
  }, [notify])

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await api.listConversations())
    } catch (err) {
      notify(err.message)
    }
  }, [notify])

  useEffect(() => {
    refreshStatus()
    refreshDocuments()
    refreshConversations()
    api
      .models()
      .then((list) => {
        setModels(list)
        setModel((m) => m || list[0]?.id || '')
      })
      .catch(() => {})
  }, [refreshStatus, refreshDocuments, refreshConversations])

  useEffect(() => {
    localStorage.setItem('sidebarOpen', String(sidebarOpen))
  }, [sidebarOpen])

  // Keep the transcript pinned to the bottom as tokens arrive.
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  const selectConversation = async (id) => {
    if (streaming) return
    setConversationId(id)
    try {
      setMessages(await api.getMessages(id))
    } catch (err) {
      notify(err.message)
    }
  }

  const newConversation = () => {
    if (streaming) return
    setConversationId(null)
    setMessages([])
    textareaRef.current?.focus()
  }

  const deleteConversation = async (id) => {
    try {
      await api.deleteConversation(id)
      if (id === conversationId) {
        setConversationId(null)
        setMessages([])
      }
      refreshConversations()
    } catch (err) {
      notify(err.message)
    }
  }

  const uploadDocument = async (file) => {
    setUploading(true)
    try {
      const doc = await api.uploadDocument(file)
      notify(`Indexed ${doc.filename} into ${doc.num_chunks} chunks`, 'success')
      await refreshDocuments()
      await refreshStatus()
      setUseRag(true) // uploading a doc almost always means "now use it"
    } catch (err) {
      notify(err.message)
    } finally {
      setUploading(false)
    }
  }

  const deleteDocument = async (id) => {
    try {
      await api.deleteDocument(id)
      await refreshDocuments()
      await refreshStatus()
    } catch (err) {
      notify(err.message)
    }
  }

  const clearDocuments = async () => {
    if (!confirm('Remove all documents and their embeddings?')) return
    try {
      await api.clearDocuments()
      await refreshDocuments()
      await refreshStatus()
    } catch (err) {
      notify(err.message)
    }
  }

  const stopStreaming = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setStreaming(false)
  }

  // overrideText / overrideRag let "Answer without RAG" resend the same
  // question with retrieval off, without touching the composer or the toggle.
  const send = (overrideText, overrideRag) => {
    const text = (typeof overrideText === 'string' ? overrideText : input).trim()
    const rag = typeof overrideRag === 'boolean' ? overrideRag : useRag
    if (!text || streaming) return

    if (rag && documents.length === 0) {
      notify(
        'RAG is on but no documents are indexed. Upload one, or turn RAG off.',
        'warning'
      )
      return
    }

    if (typeof overrideText !== 'string') setInput('')
    setStreaming(true)

    const userMsg = {
      id: `tmp-user-${Date.now()}`,
      role: 'user',
      content: text,
      used_rag: rag,
    }
    const assistantMsg = {
      id: `tmp-ai-${Date.now()}`,
      role: 'assistant',
      content: '',
      used_rag: rag,
      model,
      sources: [],
    }
    setMessages((prev) => [...prev, userMsg, assistantMsg])

    const patchAssistant = (patch) =>
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsg.id
            ? { ...m, ...(typeof patch === 'function' ? patch(m) : patch) }
            : m
        )
      )

    abortRef.current = streamChat(
      {
        message: text,
        conversation_id: conversationId,
        use_rag: rag,
        model,
        top_k: topK,
      },
      {
        onMeta: (meta) => {
          setConversationId(meta.conversation_id)
          patchAssistant({ sources: meta.sources || [], model: meta.model })
        },
        onDelta: (delta) => {
          patchAssistant((m) => ({ content: m.content + delta }))
        },
        onDone: () => {
          setStreaming(false)
          abortRef.current = null
          refreshConversations()
        },
        onError: (detail) => {
          patchAssistant({ error: detail })
          setStreaming(false)
          abortRef.current = null
        },
      }
    )
  }

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const ragDisabled = documents.length === 0

  return (
    <div className="app" data-sidebar={sidebarOpen ? 'open' : 'closed'}>
      <aside className="sidebar" aria-hidden={!sidebarOpen}>
        <div className="brand">
          <h1>RAG Chat</h1>
          <p>FastAPI · SQLite · Chroma · Groq</p>
        </div>

        <DocumentPanel
          documents={documents}
          onUpload={uploadDocument}
          onDelete={deleteDocument}
          onClear={clearDocuments}
          busy={uploading}
        />

        <ConversationPanel
          conversations={conversations}
          activeId={conversationId}
          onSelect={selectConversation}
          onNew={newConversation}
          onDelete={deleteConversation}
        />

        {status ? (
          <footer className="sidebar-foot">
            <div>
              <span className={`dot ${status.groq_configured ? 'ok' : 'bad'}`} />
              {status.groq_configured ? 'Groq connected' : 'GROQ_API_KEY missing'}
            </div>
            <div className="muted">Embeddings: {status.embedding_model}</div>
            <div className="muted">
              {status.documents} docs · {status.chunks} vectors
            </div>
          </footer>
        ) : (
          // Don't fail silently: a missing status means the API never answered.
          <footer className="sidebar-foot">
            <div>
              <span className="dot bad" />
              API unreachable
            </div>
            <div className="muted">
              Check the backend is running, then reload this page.
            </div>
            <button className="link-btn" onClick={refreshStatus}>
              Retry
            </button>
          </footer>
        )}
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="sidebar-toggle"
              onClick={() => setSidebarOpen((v) => !v)}
              title={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
              aria-label={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
              aria-expanded={sidebarOpen}
            >
              <span />
              <span />
              <span />
            </button>

            <label className={`switch ${ragDisabled ? 'disabled' : ''}`}>
              <input
                type="checkbox"
                checked={useRag}
                disabled={ragDisabled}
                onChange={(e) => setUseRag(e.target.checked)}
              />
              <span className="track">
                <span className="thumb" />
              </span>
              <span className="switch-label">
                <strong>{useRag ? 'RAG mode' : 'Chat mode'}</strong>
                <small>
                  {useRag
                    ? 'Answers grounded in your documents, with citations'
                    : ragDisabled
                      ? 'Upload a document to enable RAG'
                      : 'Plain LLM — documents are ignored'}
                </small>
              </span>
            </label>
          </div>

          <div className="controls">
            {useRag && (
              <label className="control">
                Top-K
                <input
                  type="number"
                  min="1"
                  max="12"
                  value={topK}
                  onChange={(e) => setTopK(Number(e.target.value))}
                />
              </label>
            )}
            <label className="control">
              Model
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                {models.map((m) => (
                  <option key={m.id} value={m.id} title={m.description}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </header>

        <div className="transcript" ref={scrollRef}>
          {messages.length === 0 ? (
            <div className="welcome">
              <h2>{useRag ? 'Ask your documents' : 'Ask anything'}</h2>
              <p>
                {useRag
                  ? 'Questions are answered only from the documents you uploaded, with numbered citations.'
                  : 'RAG is off, so this behaves like a normal chatbot. Flip the switch to ground answers in your documents.'}
              </p>
            </div>
          ) : (
            messages.map((m, i) => {
              const isLast = i === messages.length - 1
              // A RAG reply that retrieved nothing is a dead end — offer the
              // same question again with retrieval off.
              const askedButFoundNothing =
                m.role === 'assistant' &&
                m.used_rag &&
                (m.sources?.length ?? 0) === 0 &&
                messages[i - 1]?.role === 'user' &&
                !(streaming && isLast)

              return (
                <Message
                  key={m.id ?? i}
                  message={m}
                  streaming={streaming && isLast && m.role === 'assistant'}
                  onAnswerWithoutRag={
                    askedButFoundNothing
                      ? () => send(messages[i - 1].content, false)
                      : undefined
                  }
                />
              )
            })
          )}
        </div>

        <div className="composer">
          <textarea
            ref={textareaRef}
            value={input}
            placeholder={
              useRag ? 'Ask a question about your documents…' : 'Send a message…'
            }
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
          />
          {streaming ? (
            <button className="btn stop" onClick={stopStreaming}>
              Stop
            </button>
          ) : (
            <button className="btn send" onClick={() => send()} disabled={!input.trim()}>
              Send
            </button>
          )}
        </div>
      </main>

      {toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}
    </div>
  )
}
