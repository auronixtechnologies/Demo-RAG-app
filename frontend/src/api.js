const BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')

const url = (path) => `${BASE}${path}`

// fetch() rejects with a bare TypeError("Failed to fetch") for anything that
// fails below HTTP: backend down, dev server down, DNS, CORS. That message tells
// you nothing, so name the likely cause and the address actually being called.
function networkError(err, path) {
  if (err instanceof TypeError) {
    const target = BASE || window.location.origin
    return new Error(
      `Cannot reach the API at ${target}${path}. ` +
        (BASE
          ? 'Check the backend is running and VITE_API_URL is correct.'
          : 'The Vite dev server proxies /api to port 8000 — check that both ' +
            '`npm run dev` and the backend are still running.')
    )
  }
  return err
}

async function request(path, options = {}) {
  let res
  try {
    res = await fetch(url(path), options)
  } catch (err) {
    throw networkError(err, path)
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  status: () => request('/api/status'),
  models: () => request('/api/models'),

  listDocuments: () => request('/api/documents'),
  deleteDocument: (id) => request(`/api/documents/${id}`, { method: 'DELETE' }),
  clearDocuments: () => request('/api/documents', { method: 'DELETE' }),
  uploadDocument: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/api/documents/upload', { method: 'POST', body: form })
  },

  listConversations: () => request('/api/conversations'),
  getMessages: (id) => request(`/api/conversations/${id}/messages`),
  deleteConversation: (id) => request(`/api/conversations/${id}`, { method: 'DELETE' }),
}

/**
 * POST /api/chat and consume the SSE stream.
 *
 * EventSource can't send a POST body, so we read the response stream by hand.
 * Handlers: { onMeta, onDelta, onDone, onError }
 * Returns an AbortController so the caller can stop generation.
 */
export function streamChat(payload, handlers = {}) {
  const controller = new AbortController()

  ;(async () => {
    try {
      const res = await fetch(url('/api/chat'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      })

      if (!res.ok) {
        let detail = `Request failed (${res.status})`
        try {
          const body = await res.json()
          if (body?.detail) detail = body.detail
        } catch {
          /* ignore */
        }
        handlers.onError?.(detail)
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE frames are separated by a blank line.
        let split
        while ((split = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, split)
          buffer = buffer.slice(split + 2)
          dispatch(frame, handlers)
        }
      }
      if (buffer.trim()) dispatch(buffer, handlers)
    } catch (err) {
      if (err.name === 'AbortError') {
        handlers.onDone?.({ aborted: true })
      } else {
        handlers.onError?.(networkError(err, '/api/chat').message || String(err))
      }
    }
  })()

  return controller
}

function dispatch(frame, handlers) {
  let event = 'message'
  const dataLines = []

  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!dataLines.length) return

  let data
  try {
    data = JSON.parse(dataLines.join('\n'))
  } catch {
    return
  }

  if (event === 'meta') handlers.onMeta?.(data)
  else if (event === 'delta') handlers.onDelta?.(data.text)
  else if (event === 'done') handlers.onDone?.(data)
  else if (event === 'error') handlers.onError?.(data.detail)
}
