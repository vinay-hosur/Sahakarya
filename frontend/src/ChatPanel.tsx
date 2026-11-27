import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { apiBaseUrl } from './api'

type Channel = { id: string; workspace_id: string; name: string; created_at: string; last_activity_at: string }
type Message = { id: string; channel_id: string; sender_user_id: string; sender_display_name: string; body: string; created_at: string }
type WorkspaceMember = { user_id: string; email: string; display_name: string }

function mergeMessages(...groups: Message[][]) {
  const byId = new Map<string, Message>()
  groups.flat().forEach((item) => byId.set(item.id, item))
  return [...byId.values()].sort((left, right) =>
    left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id),
  )
}

async function chatRequest(path: string, init: RequestInit = {}) {
  return fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: 'include',
    headers: { Accept: 'application/json', ...init.headers },
  })
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join('') || '?'
}

function messageTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(new Date(value))
}

export default function ChatPanel({ workspaceId, userId, canWrite }: { workspaceId: string; userId: string; canWrite: boolean }) {
  const [channels, setChannels] = useState<Channel[]>([])
  const [members, setMembers] = useState<WorkspaceMember[]>([])
  const [activeChannelId, setActiveChannelId] = useState('')
  const [messageState, setMessageState] = useState<{ channelId: string; rows: Message[] }>({ channelId: '', rows: [] })
  const [channelName, setChannelName] = useState('')
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [socketState, setSocketState] = useState<{ channelId: string; status: 'live' | 'polling' }>({ channelId: '', status: 'polling' })
  const [reconnectTick, setReconnectTick] = useState(0)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const messages = messageState.channelId === activeChannelId ? messageState.rows : []
  const connection = !activeChannelId ? 'polling' : socketState.channelId === activeChannelId ? socketState.status : 'connecting'

  const loadChannels = useCallback(async () => {
    const response = await chatRequest(`/api/v1/workspaces/${workspaceId}/chat/channels`)
    if (!response.ok) throw new Error('Could not load workspace channels.')
    const rows = await response.json() as Channel[]
    setChannels(rows)
    setActiveChannelId((current) => rows.some((row) => row.id === current) ? current : rows[0]?.id || '')
  }, [workspaceId])

  const loadMembers = useCallback(async () => {
    const response = await chatRequest(`/api/v1/workspaces/${workspaceId}/members`)
    if (!response.ok) throw new Error('Could not load workspace members.')
    setMembers(await response.json() as WorkspaceMember[])
  }, [workspaceId])

  const loadMessages = useCallback(async (channelId: string) => {
    if (!channelId) return
    const response = await chatRequest(`/api/v1/workspaces/${workspaceId}/chat/channels/${channelId}/messages`)
    if (!response.ok) throw new Error('Could not load channel messages.')
    const rows = await response.json() as Message[]
    setMessageState((current) => ({
      channelId,
      rows: mergeMessages(current.channelId === channelId ? current.rows : [], rows),
    }))
  }, [workspaceId])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadChannels().catch((error) => setMessage(error instanceof Error ? error.message : 'Chat could not load.'))
    }, 0)
    return () => window.clearTimeout(timer)
  }, [loadChannels])

  useEffect(() => {
    const refreshMembers = () => {
      void loadMembers().catch(() => undefined)
    }
    refreshMembers()
    window.addEventListener('focus', refreshMembers)
    return () => window.removeEventListener('focus', refreshMembers)
  }, [loadMembers])

  useEffect(() => {
    if (!activeChannelId) return
    const timer = window.setTimeout(() => {
      void loadMessages(activeChannelId).catch((error) => setMessage(error instanceof Error ? error.message : 'Messages could not load.'))
    }, 0)
    return () => window.clearTimeout(timer)
  }, [activeChannelId, loadMessages])

  useEffect(() => {
    if (!workspaceId || !activeChannelId) return
    let socket: WebSocket | null = null
    let stopped = false
    let reconnectTimer = 0
    const apiOrigin = new URL(apiBaseUrl || window.location.origin, window.location.origin)
    const scheme = apiOrigin.protocol === 'https:' ? 'wss:' : 'ws:'
    const socketUrl = `${scheme}//${apiOrigin.host}/api/v1/workspaces/${workspaceId}/chat/ws?channel_id=${activeChannelId}`
    try {
      socket = new WebSocket(socketUrl)
      socket.onopen = () => { if (!stopped) setSocketState({ channelId: activeChannelId, status: 'live' }) }
      socket.onmessage = (event) => {
        if (stopped) return
        try {
          const payload = JSON.parse(String(event.data)) as {
            type?: string
            message?: Message
            user_id?: string
            online?: boolean
          }
          if (payload.type === 'presence' && payload.online && payload.user_id !== userId) {
            void loadMembers().catch(() => undefined)
          }
          if (payload.type === 'message' && payload.message) {
            setMessageState((current) => ({
              channelId: activeChannelId,
              rows: mergeMessages(
                current.channelId === activeChannelId ? current.rows : [],
                [payload.message as Message],
              ),
            }))
          }
        } catch { /* Ignore malformed events and keep the conversation open. */ }
      }
      socket.onerror = () => { if (!stopped) setSocketState({ channelId: activeChannelId, status: 'polling' }) }
      socket.onclose = () => {
        if (!stopped) {
          setSocketState({ channelId: activeChannelId, status: 'polling' })
          reconnectTimer = window.setTimeout(() => setReconnectTick((tick) => tick + 1), 5000)
        }
      }
    } catch {
      window.setTimeout(() => setSocketState({ channelId: activeChannelId, status: 'polling' }), 0)
    }
    return () => { stopped = true; window.clearTimeout(reconnectTimer); socket?.close() }
  }, [activeChannelId, loadMembers, reconnectTick, userId, workspaceId])

  useEffect(() => {
    if (!activeChannelId || connection === 'live') return
    const timer = window.setInterval(() => { void loadMessages(activeChannelId).catch(() => undefined) }, 5000)
    return () => window.clearInterval(timer)
  }, [activeChannelId, connection, loadMessages])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messageState])

  async function createChannel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!channelName.trim()) return
    setBusy(true)
    setMessage('')
    try {
      const response = await chatRequest(`/api/v1/workspaces/${workspaceId}/chat/channels`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: channelName }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Could not create the channel.')
      const channel = payload as Channel
      setChannels((current) => [channel, ...current.filter((row) => row.id !== channel.id)])
      setActiveChannelId(channel.id)
      setChannelName('')
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Could not create the channel.') }
    finally { setBusy(false) }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!draft.trim() || !activeChannelId) return
    setBusy(true)
    setMessage('')
    try {
      const response = await chatRequest(`/api/v1/workspaces/${workspaceId}/chat/channels/${activeChannelId}/messages`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body: draft }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Message could not be sent.')
      const sent = payload as Message
      setMessageState((current) => ({
        channelId: activeChannelId,
        rows: mergeMessages(current.channelId === activeChannelId ? current.rows : [], [sent]),
      }))
      setChannels((current) => current.map((item) => item.id === activeChannelId ? { ...item, last_activity_at: sent.created_at } : item))
      setDraft('')
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Message could not be sent.') }
    finally { setBusy(false) }
  }

  const activeChannel = channels.find((channel) => channel.id === activeChannelId)

  return (
    <section className="chat-section" id="messages" aria-labelledby="chat-title">
      <div className="section-heading"><div><div className="card-kicker">WORKSPACE CONVERSATIONS</div><h2 id="chat-title">Messages</h2></div><p>Talk with your workspace. Messages are saved and appear in the activity timeline.</p></div>
      <div className="chat-layout">
        <aside className="chat-channel-panel" aria-label="Conversations">
          <div className="chat-channel-heading"><strong>Channels</strong><span>{channels.length}</span></div>
          {channels.length > 0 && <ul className="chat-channel-list">{channels.map((channel) => {
            const latest = activeChannelId === channel.id ? messages[messages.length - 1] : undefined
            return <li key={channel.id}><button type="button" className={activeChannelId === channel.id ? 'selected' : ''} onClick={() => setActiveChannelId(channel.id)}><strong># {channel.name}</strong><small>{activeChannelId === channel.id && latest ? latest.body : 'Workspace conversation'}</small><time>{messageTime(channel.last_activity_at)}</time></button></li>
          })}</ul>}
          {canWrite && <form className="chat-channel-create" onSubmit={(event) => void createChannel(event)}><label htmlFor="new-channel">Create a channel</label><div><input id="new-channel" value={channelName} onChange={(event) => setChannelName(event.target.value)} placeholder="e.g. general" minLength={2} maxLength={80} required /><button type="submit" disabled={busy}>Add</button></div></form>}
        </aside>
        <div className="chat-conversation">
          <header className="chat-conversation-heading"><div><strong>{activeChannel ? `# ${activeChannel.name}` : 'Start a conversation'}</strong><span>Workspace members</span></div><span className={`chat-connection ${connection}`}><i />{connection === 'live' ? 'Live' : connection === 'connecting' ? 'Connecting' : 'Polling fallback'}</span></header>
          <div className="chat-messages" aria-live="polite">
            {!activeChannel && <div className="chat-empty"><strong>No channels yet</strong><span>Create a channel such as general to start team chat.</span></div>}
            {activeChannel && messages.length === 0 && <div className="chat-empty"><strong>Welcome to #{activeChannel.name}</strong><span>Send the first message to start this conversation.</span></div>}
            {messages.map((item) => <article key={item.id} className={`chat-message ${item.sender_user_id === userId ? 'mine' : ''}`}><span className="chat-avatar" aria-hidden="true">{initials(item.sender_display_name)}</span><div className="chat-message-content"><div className="chat-message-meta"><strong>{item.sender_user_id === userId ? 'You' : item.sender_display_name}</strong><time>{messageTime(item.created_at)}</time></div><p>{item.body}</p></div></article>)}
            <div ref={bottomRef} />
          </div>
          {canWrite ? <form className="chat-compose" onSubmit={(event) => void sendMessage(event)}><div className="chat-compose-fields"><label className="sr-only" htmlFor="chat-draft">Write a message</label><textarea id="chat-draft" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={activeChannel ? `Message #${activeChannel.name}` : 'Create a channel to start messaging'} maxLength={4000} rows={2} disabled={!activeChannel || busy} /><select aria-label="Mention a teammate" defaultValue="" disabled={!activeChannel || busy} onChange={(event) => { const email = event.target.value; if (email) setDraft((current) => `${current}${current.trim() ? ' ' : ''}@${email} `); event.target.value = '' }}><option value="">Mention teammate…</option>{members.filter((member) => member.user_id !== userId).map((member) => <option key={member.user_id} value={member.email}>{member.display_name}</option>)}</select></div><button type="submit" disabled={!activeChannel || busy || !draft.trim()}>{busy ? 'Sending…' : 'Send'}</button></form> : <p className="chat-readonly">You can read messages, but viewer access is read-only.</p>}
          {canWrite && <p className="chat-mention-hint">Choose a teammate above or type their `@email` to notify them.</p>}
          {message && <p role="status" className="chat-status-message">{message}</p>}
        </div>
      </div>
    </section>
  )
}
