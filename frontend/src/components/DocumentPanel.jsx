import { useRef, useState } from 'react'

const ACCEPT = '.pdf,.txt,.md,.markdown,.docx,.csv,.json'

export default function DocumentPanel({ documents, onUpload, onDelete, onClear, busy }) {
  const inputRef = useRef(null)
  const [dragging, setDragging] = useState(false)

  const handleFiles = async (fileList) => {
    for (const file of Array.from(fileList)) {
      await onUpload(file)
    }
    if (inputRef.current) inputRef.current.value = ''
  }

  const totalChunks = documents.reduce((sum, d) => sum + d.num_chunks, 0)

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Knowledge base</h2>
        {documents.length > 0 && (
          <button className="link-btn danger" onClick={onClear} disabled={busy}>
            Clear all
          </button>
        )}
      </header>

      <div
        className={`dropzone ${dragging ? 'dragging' : ''} ${busy ? 'busy' : ''}`}
        onClick={() => !busy && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          if (!busy) handleFiles(e.dataTransfer.files)
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={(e) => handleFiles(e.target.files)}
        />
        {busy ? (
          <>
            <strong>Indexing…</strong>
            <small>Extracting text, chunking and embedding</small>
          </>
        ) : (
          <>
            <strong>Drop a file or click to upload</strong>
            <small>PDF, DOCX, TXT, MD, CSV, JSON</small>
          </>
        )}
      </div>

      {documents.length > 0 && (
        <p className="kb-summary">
          {documents.length} document{documents.length === 1 ? '' : 's'} · {totalChunks} chunks indexed
        </p>
      )}

      <ul className="doc-list">
        {documents.map((doc) => (
          <li key={doc.id} className="doc-item">
            <div className="doc-info">
              <span className="doc-name" title={doc.filename}>
                {doc.filename}
              </span>
              <span className="doc-meta">
                {doc.num_chunks} chunks · {formatBytes(doc.size_bytes)}
              </span>
            </div>
            <button
              className="icon-btn"
              title="Remove document"
              onClick={() => onDelete(doc.id)}
            >
              ×
            </button>
          </li>
        ))}
        {documents.length === 0 && (
          <li className="empty-hint">
            No documents yet. RAG mode needs at least one.
          </li>
        )}
      </ul>
    </section>
  )
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
