import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Sources from './Sources.jsx'

// gpt-oss models emit 【1】 / 【1†page 2】 instead of [1]. The backend rewrites
// this before saving; we repeat it here so the live stream reads correctly too.
const CITATION_RE = /【\s*(\d+)\s*(?:†[^】]*)?】/g
const normalizeCitations = (text) => (text || '').replace(CITATION_RE, '[$1]')

export default function Message({ message, streaming, onAnswerWithoutRag }) {
  const isUser = message.role === 'user'
  const ragFoundNothing =
    !isUser && message.used_rag && (message.sources?.length ?? 0) === 0

  return (
    <div className={`msg ${isUser ? 'msg-user' : 'msg-assistant'}`}>
      <div className="msg-avatar">{isUser ? 'You' : 'AI'}</div>

      <div className="msg-body">
        {!isUser && (
          <div className="msg-tags">
            <span className={`tag ${message.used_rag ? 'tag-rag' : 'tag-plain'}`}>
              {message.used_rag ? 'RAG' : 'Chat'}
            </span>
            {message.model && <span className="tag tag-model">{message.model}</span>}
          </div>
        )}

        {isUser ? (
          <p className="msg-text">{message.content}</p>
        ) : (
          <div className="msg-text markdown">
            <Markdown remarkPlugins={[remarkGfm]}>
              {normalizeCitations(message.content)}
            </Markdown>
            {streaming && <span className="caret" />}
          </div>
        )}

        {!isUser && <Sources sources={message.sources} />}

        {ragFoundNothing && !streaming && (
          <div className="no-match">
            <span className="no-match-text">
              No passage in your documents matched this closely enough to cite.
            </span>
            {onAnswerWithoutRag && (
              <button className="link-btn" onClick={onAnswerWithoutRag}>
                Answer without RAG
              </button>
            )}
          </div>
        )}

        {message.error && <div className="msg-error">{message.error}</div>}
      </div>
    </div>
  )
}
