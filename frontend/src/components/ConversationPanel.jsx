export default function ConversationPanel({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
}) {
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Chats</h2>
        <button className="link-btn" onClick={onNew}>
          + New
        </button>
      </header>

      <ul className="conv-list">
        {conversations.map((conv) => (
          <li
            key={conv.id}
            className={`conv-item ${conv.id === activeId ? 'active' : ''}`}
            onClick={() => onSelect(conv.id)}
          >
            <span className="conv-title" title={conv.title}>
              {conv.title}
            </span>
            <button
              className="icon-btn"
              title="Delete chat"
              onClick={(e) => {
                e.stopPropagation()
                onDelete(conv.id)
              }}
            >
              ×
            </button>
          </li>
        ))}
        {conversations.length === 0 && (
          <li className="empty-hint">No chats yet.</li>
        )}
      </ul>
    </section>
  )
}
