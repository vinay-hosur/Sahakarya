import { useEffect, useState, type FormEvent } from 'react'
import { apiBaseUrl } from './api'

type Workspace = { id: string; name: string; description: string; slug: string; role: string }
type Project = { id: string; name: string }
type Member = { user_id: string; display_name: string; email: string; role: string }
type Activity = {
  id: string
  actor_display_name: string
  summary: string
  created_at: string
}

async function request(path: string, init: RequestInit = {}) {
  return fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: 'include',
    headers: { Accept: 'application/json', ...init.headers },
  })
}

function errorDetail(payload: unknown, fallback: string) {
  return payload && typeof payload === 'object' && 'detail' in payload && typeof payload.detail === 'string'
    ? payload.detail
    : fallback
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

export default function WorkspaceOverview({
  workspace,
  projects,
  canEdit,
  onWorkspaceUpdated,
}: {
  workspace: Workspace
  projects: Project[]
  canEdit: boolean
  onWorkspaceUpdated: (workspace: Workspace) => void
}) {
  const [members, setMembers] = useState<Member[]>([])
  const [activity, setActivity] = useState<Activity[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [description, setDescription] = useState(workspace.description || '')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    let current = true
    void Promise.all([
      request(`/api/v1/workspaces/${workspace.id}/members`),
      request(`/api/v1/workspaces/${workspace.id}/activity?limit=5`),
    ]).then(async ([memberResponse, activityResponse]) => {
      const [memberPayload, activityPayload] = await Promise.all([
        memberResponse.json(),
        activityResponse.json(),
      ])
      if (!memberResponse.ok) throw new Error(errorDetail(memberPayload, 'Could not load workspace members.'))
      if (!activityResponse.ok) throw new Error(errorDetail(activityPayload, 'Could not load workspace activity.'))
      if (current) {
        setMembers(memberPayload as Member[])
        setActivity(activityPayload as Activity[])
        setLoadError('')
        setLoading(false)
      }
    }).catch((error: unknown) => {
      if (current) {
        setLoadError(error instanceof Error ? error.message : 'Could not load workspace overview.')
        setLoading(false)
      }
    })
    return () => { current = false }
  }, [workspace.id])

  async function saveDescription(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSaving(true)
    setMessage('Saving workspace description…')
    try {
      const response = await request(`/api/v1/workspaces/${workspace.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(errorDetail(payload, 'Could not save the workspace description.'))
      onWorkspaceUpdated(payload as Workspace)
      setEditing(false)
      setMessage('Workspace description saved.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not save the workspace description.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="workspace-overview" aria-label={`${workspace.name} workspace overview`}>
      <div className="workspace-overview-copy">
        <div>
          <div className="card-kicker">WORKSPACE OVERVIEW</div>
          <h3>{workspace.name}</h3>
          {!editing ? <p>{workspace.description || 'Add a short description so teammates know what this workspace is for.'}</p> : null}
        </div>
        {canEdit && !editing && <button type="button" className="secondary-action" onClick={() => setEditing(true)}>{workspace.description ? 'Edit description' : 'Add description'}</button>}
      </div>
      {editing && <form className="workspace-description-form" onSubmit={(event) => void saveDescription(event)}>
        <label htmlFor="workspace-description">Workspace description<textarea id="workspace-description" value={description} onChange={(event) => setDescription(event.target.value)} maxLength={2000} rows={3} placeholder="What does this team work on?" /></label>
        <div><button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save description'}</button><button type="button" className="secondary-action" onClick={() => { setDescription(workspace.description || ''); setEditing(false); setMessage('') }} disabled={saving}>Cancel</button></div>
      </form>}
      <div className="workspace-overview-metrics" aria-label="Workspace counts">
        <div><strong>{loading ? '…' : loadError ? '—' : members.length}</strong><span>Team members</span></div>
        <div><strong>{projects.length}</strong><span>Projects</span></div>
        <div><strong>{loading ? '…' : loadError ? '—' : activity.length}</strong><span>Recent updates</span></div>
      </div>
      <div className="workspace-overview-bottom">
        <section aria-labelledby="workspace-recent-title">
          <div className="workspace-recent-heading"><h4 id="workspace-recent-title">Recent activity</h4><a href="#activity">View activity</a></div>
          {loadError ? <p className="workspace-overview-message" role="status">{loadError}</p> : loading ? <p className="workspace-overview-message">Loading workspace activity…</p> : activity.length === 0 ? <p className="workspace-overview-message">Workspace changes will appear here.</p> : <ol className="workspace-recent-list">
            {activity.slice(0, 4).map((entry) => <li key={entry.id}><strong>{entry.actor_display_name}</strong><span>{entry.summary}</span><time dateTime={entry.created_at}>{formatTime(entry.created_at)}</time></li>)}
          </ol>}
        </section>
        <nav className="workspace-quick-actions" aria-label="Workspace quick actions">
          <a href="#projects">Open projects</a>
          <a href="#team">Manage team</a>
          <a href="#messages">Open messages</a>
        </nav>
      </div>
      {message && <p className="workspace-overview-message" role="status">{message}</p>}
    </div>
  )
}
