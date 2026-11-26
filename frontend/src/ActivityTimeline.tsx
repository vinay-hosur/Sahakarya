import { useEffect, useState } from 'react'
import { apiBaseUrl } from './api'

type Project = { id: string; name: string }
type Activity = {
  id: string
  project_id: string | null
  actor_display_name: string
  event_type: string
  object_type: string
  object_id: string | null
  summary: string
  created_at: string
}

async function request(path: string) {
  return fetch(`${apiBaseUrl}${path}`, { credentials: 'include', headers: { Accept: 'application/json' } })
}

export default function ActivityTimeline({ workspaceId, projects }: { workspaceId: string; projects: Project[] }) {
  const [projectId, setProjectId] = useState('')
  const [entries, setEntries] = useState<Activity[]>([])
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshKey, setRefreshKey] = useState(0)
  const projectQuery = projectId ? `&project_id=${encodeURIComponent(projectId)}` : ''

  useEffect(() => {
    let current = true
    void request(`/api/v1/workspaces/${workspaceId}/activity?limit=50${projectQuery}`).then(async (response) => {
      const payload = await response.json()
      if (!response.ok) {
        const detail = payload && typeof payload.detail === 'string' ? payload.detail : 'Could not load activity.'
        throw new Error(detail)
      }
      if (current) {
        setEntries(payload as Activity[])
        setMessage('')
        setLoading(false)
      }
    }).catch((error) => {
      if (current) {
        setMessage(error instanceof Error ? error.message : 'Could not load activity.')
        setLoading(false)
      }
    })
    return () => { current = false }
  }, [workspaceId, projectQuery, refreshKey])

  return (
    <section className="activity-section" id="activity" aria-labelledby="activity-title">
      <div className="section-heading"><div><div className="card-kicker">SHARED WORKSPACE HISTORY</div><h2 id="activity-title">Recent activity</h2></div><p>Project, task, comment, document, and decision events in chronological order.</p></div>
      <div className="activity-controls"><label>Scope<select value={projectId} onChange={(event) => { setProjectId(event.target.value); setLoading(true) }}><option value="">All workspace activity</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label><button type="button" onClick={() => { setLoading(true); setRefreshKey((key) => key + 1) }}>Refresh activity</button></div>
      {message && <p className="activity-message" role="status">{message}</p>}
      {loading ? <p className="activity-message">Loading activity…</p> : entries.length === 0 ? <div className="knowledge-empty"><strong>No activity yet.</strong><span>Project work will appear here as it happens.</span></div> : <ol className="activity-list">
        {entries.map((entry) => <li className="activity-entry" key={entry.id}><span className="activity-marker" aria-hidden="true" /><div className="activity-entry-main"><div className="activity-entry-top"><strong>{entry.actor_display_name}</strong><time dateTime={entry.created_at}>{new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(entry.created_at))}</time></div><p>{entry.summary}</p><span className="activity-event-type">{entry.event_type} · {entry.object_type}</span></div></li>)}
      </ol>}
    </section>
  )
}
