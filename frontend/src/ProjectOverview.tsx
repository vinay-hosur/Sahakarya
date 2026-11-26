import { useEffect, useState } from 'react'
import { apiBaseUrl } from './api'

type OverviewTask = { id: string; title: string; status: string; priority: string; due_date: string | null }
type OverviewDecision = { id: string; title: string; status: string; decision_date: string }
type ProjectOverviewData = {
  project_id: string
  generated_at: string
  total_tasks: number
  status_counts: Record<string, number>
  overdue_tasks: OverviewTask[]
  due_soon_tasks: OverviewTask[]
  blocked_tasks: OverviewTask[]
  high_priority_tasks: OverviewTask[]
  recent_decisions: OverviewDecision[]
}

export default function ProjectOverview({ workspaceId, projectId }: { workspaceId: string; projectId: string }) {
  const [overview, setOverview] = useState<ProjectOverviewData | null>(null)
  const [message, setMessage] = useState('Loading project signals…')
  const endpoint = `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/projects/${projectId}/overview`

  useEffect(() => {
    let active = true
    void fetch(endpoint, { credentials: 'include', headers: { Accept: 'application/json' } }).then(async (response) => {
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not load project overview.')
      if (active) {
        setOverview(result as ProjectOverviewData)
        setMessage('')
      }
    }).catch((error) => {
      if (active) setMessage(error instanceof Error ? error.message : 'Could not load project overview.')
    })
    return () => { active = false }
  }, [endpoint])

  if (!overview) return <div className="project-overview" role="status">{message}</div>

  const count = (status: string) => overview.status_counts[status] || 0
  const sections: Array<[string, OverviewTask[]]> = [
    ['Overdue', overview.overdue_tasks],
    ['Due in the next 7 days', overview.due_soon_tasks],
    ['Blocked', overview.blocked_tasks],
    ['High priority', overview.high_priority_tasks],
  ]

  return (
    <section className="project-overview" aria-label="Project overview">
      <div className="project-overview-counts">
        <div><strong>{overview.total_tasks}</strong><span>Total tasks</span></div>
        <div><strong>{count('todo')}</strong><span>To do</span></div>
        <div><strong>{count('in_progress')}</strong><span>In progress</span></div>
        <div><strong>{count('done')}</strong><span>Done</span></div>
      </div>
      <div className="project-overview-lists">
        {sections.map(([title, tasks]) => <div key={title}>
          <h4>{title} <span>{tasks.length}</span></h4>
          {tasks.length === 0 ? <p className="project-overview-empty">None</p> : <ul>
            {tasks.map((task) => <li key={task.id}><span>{task.title}</span><small>{task.due_date || task.priority}</small></li>)}
          </ul>}
        </div>)}
      </div>
      <div className="project-overview-decisions">
        <h4>Recent decisions</h4>
        {overview.recent_decisions.length === 0 ? <p className="project-overview-empty">No decisions recorded.</p> : <ul>
          {overview.recent_decisions.map((decision) => <li key={decision.id}><span>{decision.title}</span><small>{decision.status} · {decision.decision_date}</small></li>)}
        </ul>}
      </div>
      <p className="project-overview-footnote">Calculated from stored project records · updated {new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(overview.generated_at))}</p>
    </section>
  )
}
