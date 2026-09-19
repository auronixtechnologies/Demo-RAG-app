import { useState } from 'react'

export default function Sources({ sources }) {
  const [open, setOpen] = useState(false)
  if (!sources?.length) return null

  return (
    <div className="sources">
      <button className="sources-toggle" onClick={() => setOpen((v) => !v)}>
        <span className="chev" data-open={open}>›</span>
        {sources.length} source{sources.length === 1 ? '' : 's'} retrieved
      </button>

      {open && (
        <ol className="sources-list">
          {sources.map((s, i) => (
            <li key={`${s.document_id}-${s.chunk_index}`}>
              <div className="source-head">
                <span className="source-num">[{i + 1}]</span>
                <span className="source-file">{s.filename}</span>
                <span className="source-meta">
                  chunk {s.chunk_index} · {(s.score * 100).toFixed(0)}% match
                </span>
              </div>
              <p className="source-text">{s.text}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
