import { useEffect, useState, type FormEvent } from 'react'
import { apiBaseUrl } from './api'

type Project = { id: string; name: string }
type TelegramStatus = {
  integration_id: string
  status: string
  bot_username: string | null
  bot_name: string | null
  last_checked_at: string | null
  last_error: string | null
}
type TelegramChat = {
  id: string
  title: string
  chat_type: string
  username: string | null
  project_id: string | null
  status: 'pending' | 'connected' | 'paused'
  last_message_at: string | null
  created_at: string
}

async function request(path: string, init: RequestInit = {}) {
  return fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: 'include',
    headers: { Accept: 'application/json', ...init.headers },
  })
}

function detail(payload: unknown, fallback: string) {
  return payload && typeof payload === 'object' && 'detail' in payload && typeof payload.detail === 'string'
    ? payload.detail
    : fallback
}

function formatDate(value: string | null, empty = 'No messages received yet') {
  return value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : empty
}

export default function TelegramIntegrationPanel({
  workspaceId,
  projects,
  canWrite,
}: {
  workspaceId: string
  projects: Project[]
  canWrite: boolean
}) {
  const [status, setStatus] = useState<TelegramStatus | null>(null)
  const [chats, setChats] = useState<TelegramChat[]>([])
  const [projectSelections, setProjectSelections] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  const base = `/api/v1/workspaces/${workspaceId}/integrations/telegram`

  async function load() {
    const [statusResponse, chatsResponse] = await Promise.all([
      request(`${base}/status`),
      request(`${base}/chats`),
    ])
    const statusPayload = await statusResponse.json()
    const chatsPayload = await chatsResponse.json()
    if (!statusResponse.ok) throw new Error(detail(statusPayload, 'Could not load Telegram status.'))
    if (!chatsResponse.ok) throw new Error(detail(chatsPayload, 'Could not load Telegram groups.'))
    setStatus(statusPayload as TelegramStatus)
    const rows = chatsPayload as TelegramChat[]
    setChats(rows)
    setProjectSelections((current) => Object.fromEntries(rows.map((chat) => [chat.id, current[chat.id] ?? chat.project_id ?? ''])))
  }

  useEffect(() => {
    let current = true
    void (async () => {
      try {
        const [statusResponse, chatsResponse] = await Promise.all([
          request(`${base}/status`),
          request(`${base}/chats`),
        ])
        const statusPayload = await statusResponse.json()
        const chatsPayload = await chatsResponse.json()
        if (!statusResponse.ok) throw new Error(detail(statusPayload, 'Could not load Telegram status.'))
        if (!chatsResponse.ok) throw new Error(detail(chatsPayload, 'Could not load Telegram groups.'))
        if (current) {
          setStatus(statusPayload as TelegramStatus)
          const rows = chatsPayload as TelegramChat[]
          setChats(rows)
          setProjectSelections(Object.fromEntries(rows.map((chat) => [chat.id, chat.project_id || ''])))
        }
      } catch (error) {
        if (current) setMessage(error instanceof Error ? error.message : 'Could not load Telegram status.')
      }
    })()
    return () => { current = false }
  }, [base])

  async function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const token = String(new FormData(form).get('bot_token') || '').trim()
    setBusy(true)
    setMessage('Validating the bot token and registering the webhook…')
    try {
      const response = await request(`${base}/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bot_token: token }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(detail(payload, 'Telegram connection failed.'))
      setStatus(payload as TelegramStatus)
      form.reset()
      setMessage('Telegram bot connected. Add it to a group, then send one message so SAHAKARYA can discover that group.')
      await load()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Telegram connection failed.')
    } finally {
      setBusy(false)
    }
  }

  async function updateGroup(chat: TelegramChat) {
    setBusy(true)
    setMessage(`Saving ${chat.title}…`)
    try {
      const response = await request(`${base}/chats/${chat.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectSelections[chat.id] || null,
          enabled: chat.status !== 'connected',
        }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(detail(payload, 'Could not update Telegram group.'))
      setChats((current) => current.map((row) => row.id === chat.id ? payload as TelegramChat : row))
      setMessage(chat.status === 'connected' ? 'Telegram group paused.' : 'Telegram group connected to project context.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not update Telegram group.')
    } finally {
      setBusy(false)
    }
  }

  async function disconnect() {
    if (!window.confirm('Disconnect the Telegram bot? Previously captured group messages will remain in SAHAKARYA.')) return
    setBusy(true)
    setMessage('Removing the Telegram webhook…')
    try {
      const response = await request(base, { method: 'DELETE' })
      if (!response.ok) {
        const payload = await response.json()
        throw new Error(detail(payload, 'Could not disconnect Telegram.'))
      }
      setStatus(null)
      setChats([])
      setMessage('Telegram disconnected.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not disconnect Telegram.')
    } finally {
      setBusy(false)
    }
  }

  const connected = status?.status === 'connected'

  return (
    <article className="integration-card telegram-card">
      <div className="integration-card-top">
        <div className="integration-mark telegram">TG</div>
        <div><div className="card-kicker">BOT API · NEW MESSAGES</div><h3>Telegram</h3></div>
        <span className={`integration-status ${connected ? 'connected' : 'not_configured'}`}><span className="small-dot" />{connected ? 'Connected' : 'Not connected'}</span>
      </div>
      {connected ? <>
        <p className="integration-help">Bot {status?.bot_username ? `@${status.bot_username}` : status?.bot_name || ''} is connected. Last verified {formatDate(status?.last_checked_at || null, 'not available')}. Only groups you enable below become searchable context.</p>
        {status?.last_error && <p className="integration-message" role="status">Latest integration error: {status.last_error}</p>}
        <div className="telegram-group-list" aria-live="polite">
          <h4>Discovered groups</h4>
          {chats.length === 0 ? <p className="telegram-empty">No group has reached the bot yet. Add it to a test group and send a message.</p> : chats.map((chat) => (
            <div className="telegram-group-row" key={chat.id}>
              <div><strong>{chat.title}</strong><span>{chat.chat_type} · {chat.status === 'connected' ? 'enabled' : chat.status === 'paused' ? 'paused' : 'waiting for you to connect'} · {formatDate(chat.last_message_at)}</span></div>
              <label>Project<select aria-label={`Project for ${chat.title}`} value={projectSelections[chat.id] || ''} onChange={(event) => setProjectSelections((current) => ({ ...current, [chat.id]: event.target.value }))} disabled={!canWrite || busy}><option value="">Workspace-wide context</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
              {canWrite && <button type="button" className="secondary-action" onClick={() => void updateGroup(chat)} disabled={busy}>{chat.status === 'connected' ? 'Pause group' : chat.status === 'paused' ? 'Resume group' : 'Connect group'}</button>}
            </div>
          ))}
        </div>
        {canWrite && <button type="button" className="integration-disconnect" onClick={() => void disconnect()} disabled={busy}>Disconnect Telegram</button>}
      </> : canWrite ? <>
        {status?.last_error && <p className="integration-message" role="status">Latest integration error: {status.last_error}</p>}
        {status?.status === 'not_configured' && status.bot_username && <p className="integration-help">The bot is disconnected. Reconnect it below to resume message capture.</p>}
        <p className="integration-help">Create a bot with Telegram’s official BotFather, then enter its token here. SAHAKARYA validates it with getMe and registers a private webhook.</p>
        <form className="telegram-connect-form" onSubmit={(event) => void connect(event)}>
          <label>BotFather token<input type="password" name="bot_token" autoComplete="off" minLength={20} maxLength={512} required /></label>
          <button type="submit" disabled={busy}>{busy ? 'Connecting…' : 'Validate and connect bot'}</button>
        </form>
      </> : <p className="integration-help">A workspace admin must connect the Telegram bot.</p>}
      <details className="telegram-instructions">
        <summary>Setup and Telegram limitations</summary>
        <ol>
          <li>In Telegram, open <strong>@BotFather</strong>, create a bot, and copy its token directly into this form.</li>
          <li>Add the bot to the intended group. To receive ordinary group messages, turn off bot privacy mode using BotFather’s <code>/setprivacy</code>; otherwise Telegram sends only commands and mentions.</li>
          <li>Send a new text message in the group. It appears as pending until an admin associates it with a project or the workspace.</li>
        </ol>
        <p>The Telegram Bot API does not provide historical group-message sync. SAHAKARYA stores new text and captions only after a group is explicitly enabled. Bot privacy and Telegram group permissions control what the bot receives.</p>
      </details>
      {message && <p className="integration-message" role="status">{message}</p>}
    </article>
  )
}
