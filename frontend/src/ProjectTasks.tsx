import { useEffect, useState, type FormEvent } from 'react'
import { apiBaseUrl } from './api'

type TaskStatus = 'todo' | 'in_progress' | 'blocked' | 'done'
type TaskPriority = 'low' | 'medium' | 'high' | 'urgent'
type Task = {
  id: string
  title: string
  description: string
  status: TaskStatus
  priority: TaskPriority
  due_date: string | null
  assignee_id: string | null
  labels: string[]
  depends_on_task_ids: string[]
}
type TaskComment = {
  id: string
  author_display_name: string
  body: string
  created_at: string
}
type WorkspaceMember = { user_id: string; display_name: string; role: string }
type TaskHistoryEntry = {
  id: string
  actor_display_name: string
  event_type: string
  changes: Record<string, unknown>
  created_at: string
}
type ReasoningProvider = { id: string; label: string; model: string }
type ProposalCitation = { id: string; title: string; provider: string; source_url: string | null }
type ProposedTask = {
  draft_id: string
  title: string
  description: string
  priority: TaskPriority
  labels: string[]
  citations: ProposalCitation[]
}
type TaskProposal = {
  provider: string
  model: string
  summary: string
  needs_more_context: boolean
  tasks: ProposedTask[]
}

const statuses: TaskStatus[] = ['todo', 'in_progress', 'blocked', 'done']

async function taskRequest(path: string, init: RequestInit = {}) {
  return fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: 'include',
    headers: { Accept: 'application/json', ...init.headers },
  })
}

export default function ProjectTasks({ workspaceId, projectId, canEdit, providers, defaultProvider }: {
  workspaceId: string
  projectId: string
  canEdit: boolean
  providers: ReasoningProvider[]
  defaultProvider: string
}) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [members, setMembers] = useState<WorkspaceMember[]>([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [proposalPrompt, setProposalPrompt] = useState('')
  const [proposalProvider, setProposalProvider] = useState('')
  const [proposal, setProposal] = useState<TaskProposal | null>(null)
  const [selectedProposalIds, setSelectedProposalIds] = useState<string[]>([])
  const [proposalBusy, setProposalBusy] = useState(false)
  const [creatingProposals, setCreatingProposals] = useState(false)
  const [proposalMessage, setProposalMessage] = useState('')

  const endpoint = `/api/v1/workspaces/${workspaceId}/projects/${projectId}/tasks`
  const activeProposalProvider = providers.some((provider) => provider.id === proposalProvider)
    ? proposalProvider
    : providers.some((provider) => provider.id === defaultProvider)
      ? defaultProvider
      : providers[0]?.id || ''

  useEffect(() => {
    let current = true
    void taskRequest(endpoint).then(async (response) => {
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not load tasks.')
      if (current) setTasks(result as Task[])
    }).catch((error) => {
      if (current) setMessage(error instanceof Error ? error.message : 'Could not load tasks.')
    })
    return () => { current = false }
  }, [endpoint])

  useEffect(() => {
    let current = true
    void taskRequest(`/api/v1/workspaces/${workspaceId}/members`).then(async (response) => {
      if (!response.ok) return
      const result = await response.json() as WorkspaceMember[]
      if (current) setMembers(result)
    }).catch(() => undefined)
    return () => { current = false }
  }, [workspaceId])

  async function suggestTasks(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!proposalPrompt.trim() || !activeProposalProvider) return
    setProposalBusy(true)
    setProposalMessage('Gathering project evidence…')
    try {
      const response = await taskRequest(
        `/api/v1/workspaces/${workspaceId}/assistant/projects/${projectId}/task-proposals`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: proposalPrompt.trim(), provider: activeProposalProvider }),
        },
      )
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not draft tasks.')
      const value = result as Omit<TaskProposal, 'tasks'> & { tasks: Array<Omit<ProposedTask, 'draft_id'>> }
      const drafts = value.tasks.map((task) => ({ ...task, draft_id: crypto.randomUUID() }))
      setProposal({ ...value, tasks: drafts })
      setSelectedProposalIds(drafts.map((task) => task.draft_id))
      setProposalMessage(drafts.length ? 'Review and edit the evidence-backed task drafts before creating them.' : 'No task drafts were supported by the current evidence.')
    } catch (error) {
      setProposalMessage(error instanceof Error ? error.message : 'Could not draft tasks.')
    } finally {
      setProposalBusy(false)
    }
  }

  function updateProposedTask(draftId: string, update: Partial<ProposedTask>) {
    setProposal((current) => current ? {
      ...current,
      tasks: current.tasks.map((task) => task.draft_id === draftId ? { ...task, ...update } : task),
    } : current)
  }

  async function createSelectedProposals() {
    if (!proposal) return
    const selected = proposal.tasks.filter((task) => selectedProposalIds.includes(task.draft_id))
    if (!selected.length) return
    setCreatingProposals(true)
    setProposalMessage('Creating the selected tasks…')
    let createdCount = 0
    try {
      for (const draft of selected) {
        const response = await taskRequest(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: draft.title,
            description: draft.description,
            priority: draft.priority,
            labels: draft.labels,
          }),
        })
        const result = await response.json()
        if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'A task could not be created.')
        const created = result as Task
        setTasks((current) => [created, ...current])
        setProposal((current) => current ? {
          ...current,
          tasks: current.tasks.filter((task) => task.draft_id !== draft.draft_id),
        } : current)
        setSelectedProposalIds((current) => current.filter((id) => id !== draft.draft_id))
        createdCount += 1
      }
      setProposalMessage(`${createdCount} ${createdCount === 1 ? 'task' : 'tasks'} created from your reviewed drafts.`)
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'A task could not be created.'
      setProposalMessage(`${createdCount ? `${createdCount} task${createdCount === 1 ? '' : 's'} created. ` : ''}${detail} Remaining drafts are still available to review.`)
    } finally {
      setCreatingProposals(false)
    }
  }

  async function createTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const values = new FormData(form)
    setBusy(true)
    setMessage('Creating task…')
    try {
      const response = await taskRequest(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: String(values.get('task_title') || ''),
          priority: String(values.get('task_priority') || 'medium'),
          due_date: String(values.get('task_due_date') || '') || null,
          assignee_id: String(values.get('task_assignee') || '') || null,
          labels: String(values.get('task_labels') || '').split(',').map((label) => label.trim()).filter(Boolean),
          depends_on_task_ids: values.getAll('task_dependencies').map(String),
        }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not create task.')
      setTasks((current) => [result as Task, ...current])
      form.reset()
      setMessage('Task added to this project.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not create task.')
    } finally {
      setBusy(false)
    }
  }

  async function changeStatus(task: Task, status: TaskStatus) {
    setBusy(true)
    setMessage('Saving task…')
    try {
      const response = await taskRequest(`${endpoint}/${task.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not update task.')
      setTasks((current) => current.map((item) => item.id === task.id ? result as Task : item))
      setMessage('Task status saved.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not update task.')
    } finally {
      setBusy(false)
    }
  }

  async function changeAssignee(task: Task, assignee_id: string) {
    setBusy(true)
    setMessage('Saving task assignment…')
    try {
      const response = await taskRequest(`${endpoint}/${task.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ assignee_id: assignee_id || null }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not update assignment.')
      setTasks((current) => current.map((item) => item.id === task.id ? result as Task : item))
      setMessage('Task assignment saved.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not update assignment.')
    } finally {
      setBusy(false)
    }
  }

  async function changeDependencies(task: Task, depends_on_task_ids: string[]) {
    setBusy(true)
    setMessage('Saving task dependencies…')
    try {
      const response = await taskRequest(`${endpoint}/${task.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ depends_on_task_ids }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not update dependencies.')
      setTasks((current) => current.map((item) => item.id === task.id ? result as Task : item))
      setMessage('Task dependencies saved.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not update dependencies.')
    } finally {
      setBusy(false)
    }
  }

  async function changeLabels(event: FormEvent<HTMLFormElement>, task: Task) {
    event.preventDefault()
    const labels = String(new FormData(event.currentTarget).get('task_labels') || '')
      .split(',').map((label) => label.trim()).filter(Boolean)
    setBusy(true)
    setMessage('Saving task labels…')
    try {
      const response = await taskRequest(`${endpoint}/${task.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ labels }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not update labels.')
      setTasks((current) => current.map((item) => item.id === task.id ? result as Task : item))
      setMessage('Task labels saved.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not update labels.')
    } finally {
      setBusy(false)
    }
  }

  async function removeTask(task: Task) {
    if (!window.confirm(`Delete “${task.title}” and its comments? Tasks that depend on it must be updated first.`)) return
    setBusy(true)
    setMessage('Deleting task…')
    try {
      const response = await taskRequest(`${endpoint}/${task.id}`, { method: 'DELETE' })
      if (!response.ok) {
        const result = await response.json()
        throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not delete task.')
      }
      setTasks((current) => current.filter((item) => item.id !== task.id))
      setMessage('Task deleted.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not delete task.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="project-tasks">
      <div className="project-tasks-heading"><strong>Project tasks</strong><span>{tasks.length} {tasks.length === 1 ? 'task' : 'tasks'}</span></div>
      <section className="task-proposal-panel" aria-label="AI task suggestions">
        <div className="card-kicker">EVIDENCE-BACKED TASK DRAFTS</div>
        <p>Ask the selected model to turn this project’s saved context into editable task drafts.</p>
        <form className="task-proposal-form" onSubmit={(event) => void suggestTasks(event)}>
          <textarea value={proposalPrompt} onChange={(event) => setProposalPrompt(event.target.value)} maxLength={2000} rows={2} placeholder="e.g. Break the launch risks into the next implementation tasks…" aria-label="Task drafting request" />
          <label>AI model
            <select value={activeProposalProvider} onChange={(event) => setProposalProvider(event.target.value)} disabled={!providers.length || proposalBusy}>
              {providers.length ? providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.label} · {provider.model}</option>) : <option value="">No model configured</option>}
            </select>
          </label>
          <button type="submit" disabled={!proposalPrompt.trim() || !activeProposalProvider || proposalBusy || creatingProposals}>{proposalBusy ? 'Drafting…' : 'Suggest tasks'}</button>
        </form>
        {!providers.length && <small className="task-proposal-note">Configure an AI provider and model on the server to enable task suggestions.</small>}
        {proposalMessage && <p className="task-proposal-message" role="status">{proposalMessage}</p>}
        {proposal && <div className="task-proposal-results">
          <div className="task-proposal-result-heading"><strong>{proposal.provider} · {proposal.model}</strong><span>{proposal.tasks.length} drafts</span></div>
          <p>{proposal.summary}</p>
          {proposal.needs_more_context && <p className="task-proposal-note">More project context is needed before the assistant can suggest tasks.</p>}
          {proposal.tasks.map((draft) => <article className="task-proposal-card" key={draft.draft_id}>
            <label className="task-proposal-select"><input type="checkbox" checked={selectedProposalIds.includes(draft.draft_id)} onChange={(event) => setSelectedProposalIds((current) => event.target.checked ? [...current, draft.draft_id] : current.filter((id) => id !== draft.draft_id))} /><span>Include this draft</span></label>
            <input aria-label="Proposed task title" maxLength={256} minLength={2} value={draft.title} onChange={(event) => updateProposedTask(draft.draft_id, { title: event.target.value })} />
            <textarea aria-label="Proposed task description" maxLength={10000} rows={2} value={draft.description} onChange={(event) => updateProposedTask(draft.draft_id, { description: event.target.value })} />
            <div className="task-proposal-edit-row">
              <label>Priority<select value={draft.priority} onChange={(event) => updateProposedTask(draft.draft_id, { priority: event.target.value as TaskPriority })}>{(['low', 'medium', 'high', 'urgent'] as TaskPriority[]).map((priority) => <option key={priority} value={priority}>{priority}</option>)}</select></label>
              <label>Labels<input value={draft.labels.join(', ')} maxLength={330} onChange={(event) => updateProposedTask(draft.draft_id, { labels: event.target.value.split(',').map((label) => label.trim()).filter(Boolean).slice(0, 10) })} /></label>
            </div>
            <div className="task-proposal-evidence"><span>Evidence</span>{draft.citations.map((citation) => <span key={citation.id}>{citation.title} · {citation.provider}</span>)}</div>
          </article>)}
          {canEdit && proposal.tasks.length > 0 && <button className="task-proposal-create" type="button" disabled={!selectedProposalIds.length || creatingProposals || proposalBusy || proposal.tasks.some((task) => selectedProposalIds.includes(task.draft_id) && task.title.trim().length < 2)} onClick={() => void createSelectedProposals()}>{creatingProposals ? 'Creating…' : `Create ${selectedProposalIds.length || ''} selected task${selectedProposalIds.length === 1 ? '' : 's'}`}</button>}
          {!canEdit && proposal.tasks.length > 0 && <p className="task-proposal-note">You can review these drafts. A workspace owner or member must create them.</p>}
        </div>}
      </section>
      {canEdit && <form className="task-create-form" onSubmit={(event) => void createTask(event)}>
        <input name="task_title" aria-label="Task title" placeholder="Add a task…" minLength={2} maxLength={256} required />
        <select name="task_priority" aria-label="Task priority" defaultValue="medium">
          <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="urgent">Urgent</option>
        </select>
        <input name="task_due_date" aria-label="Due date" type="date" />
        <select name="task_assignee" aria-label="Assign task" defaultValue="">
          <option value="">Unassigned</option>
          {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
        </select>
        <input name="task_labels" aria-label="Task labels" placeholder="Labels (comma separated)" maxLength={330} />
        <select name="task_dependencies" aria-label="Task dependencies" multiple size={2}>
          {tasks.map((task) => <option key={task.id} value={task.id}>{task.title}</option>)}
        </select>
        <button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Add'}</button>
      </form>}
      {message && <p className="task-message" role="status">{message}</p>}
      {tasks.length === 0 ? <p className="task-empty">No tasks yet. Add the first task above.</p> : (
        <ul className="task-list">
          {tasks.map((task) => (
            <li className="task-row" key={task.id}>
              <div className="task-row-title"><strong>{task.title}</strong><span className={`task-priority priority-${task.priority}`}>{task.priority}</span></div>
              {task.labels.length > 0 && <div className="task-label-list" aria-label="Task labels">{task.labels.map((label) => <span className="task-label" key={label}>{label}</span>)}</div>}
              <div className="task-row-meta">{task.due_date ? <time dateTime={task.due_date}>Due {task.due_date}</time> : <span>No due date</span>}{task.assignee_id && <span>Assigned to {members.find((member) => member.user_id === task.assignee_id)?.display_name || 'workspace member'}</span>}</div>
              {canEdit && <label className="task-assignee-label">Assignee
                <select aria-label={`Assignee for ${task.title}`} value={task.assignee_id || ''} disabled={busy} onChange={(event) => void changeAssignee(task, event.target.value)}>
                  <option value="">Unassigned</option>
                  {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
                </select>
              </label>}
              {canEdit ? <label className="task-status-label">Status
                  <select value={task.status} disabled={busy} onChange={(event) => void changeStatus(task, event.target.value as TaskStatus)}>
                    {statuses.map((status) => <option key={status} value={status}>{status.replace('_', ' ')}</option>)}
                  </select>
                </label> : <div className="task-status-label"><span>Status</span><strong>{task.status.replace('_', ' ')}</strong></div>}
              {canEdit && <form className="task-label-edit" onSubmit={(event) => void changeLabels(event, task)}>
                <label htmlFor={`task-labels-${task.id}`}>Labels</label>
                <input id={`task-labels-${task.id}`} name="task_labels" defaultValue={task.labels.join(', ')} placeholder="bug, backend, release" maxLength={330} />
                <button type="submit" disabled={busy}>Save</button>
              </form>}
              <label className="task-dependency-label">Blocked by
                <select
                  aria-label={`Dependencies for ${task.title}`}
                  multiple
                  size={Math.min(3, Math.max(1, tasks.length - 1))}
                  value={task.depends_on_task_ids}
                  disabled={!canEdit || busy}
                  onChange={(event) => void changeDependencies(task, Array.from(event.target.selectedOptions, (option) => option.value))}
                >
                  {tasks.filter((candidate) => candidate.id !== task.id).map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.title}</option>)}
                </select>
              </label>
              {task.depends_on_task_ids.length > 0 && <p className="task-dependency-summary">Depends on: {task.depends_on_task_ids.map((id) => tasks.find((candidate) => candidate.id === id)?.title || 'removed task').join(', ')}</p>}
              {canEdit && <button className="task-delete-button" type="button" disabled={busy} onClick={() => void removeTask(task)}>Delete task</button>}
              <TaskHistory workspaceId={workspaceId} projectId={projectId} taskId={task.id} />
              <TaskComments workspaceId={workspaceId} projectId={projectId} taskId={task.id} canEdit={canEdit} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function TaskHistory({ workspaceId, projectId, taskId }: { workspaceId: string; projectId: string; taskId: string }) {
  const [expanded, setExpanded] = useState(false)
  const [entries, setEntries] = useState<TaskHistoryEntry[]>([])
  const [loaded, setLoaded] = useState(false)
  const [message, setMessage] = useState('')
  const endpoint = `/api/v1/workspaces/${workspaceId}/projects/${projectId}/tasks/${taskId}/history`

  useEffect(() => {
    if (!expanded || loaded) return
    let current = true
    void taskRequest(endpoint).then(async (response) => {
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not load task history.')
      if (current) {
        setEntries(result as TaskHistoryEntry[])
        setLoaded(true)
        setMessage('')
      }
    }).catch((error) => {
      if (current) setMessage(error instanceof Error ? error.message : 'Could not load task history.')
    })
    return () => { current = false }
  }, [endpoint, expanded, loaded])

  return (
    <div className="task-history">
      <button type="button" className="task-history-toggle" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
        {expanded ? 'Hide history' : 'History'}{loaded ? ` (${entries.length})` : ''}
      </button>
      {expanded && <div className="task-history-list">
        {entries.length === 0 && loaded && <p className="task-empty">No history yet.</p>}
        {entries.map((entry) => <article className="task-history-entry" key={entry.id}>
          <strong>{entry.event_type === 'created' ? 'Created' : 'Updated'}</strong>
          <span>by {entry.actor_display_name}</span>
          <time dateTime={entry.created_at}>{new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(entry.created_at))}</time>
          {entry.event_type === 'updated' && <p>{Object.entries(entry.changes).map(([field, change]) => {
            const values = change as { before?: unknown; after?: unknown }
            const display = (value: unknown) => Array.isArray(value) ? value.join(', ') || 'none' : value == null ? 'none' : String(value)
            return `${field.replaceAll('_', ' ')}: ${display(values.before)} → ${display(values.after)}`
          }).join(' · ')}</p>}
        </article>)}
        {message && <p className="task-message" role="status">{message}</p>}
      </div>}
    </div>
  )
}

function TaskComments({ workspaceId, projectId, taskId, canEdit }: {
  workspaceId: string
  projectId: string
  taskId: string
  canEdit: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [comments, setComments] = useState<TaskComment[]>([])
  const [draft, setDraft] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const endpoint = `/api/v1/workspaces/${workspaceId}/projects/${projectId}/tasks/${taskId}/comments`

  useEffect(() => {
    if (!expanded || loaded) return
    let current = true
    void taskRequest(endpoint).then(async (response) => {
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not load comments.')
      if (current) {
        setComments(result as TaskComment[])
        setLoaded(true)
        setMessage('')
      }
    }).catch((error) => {
      if (current) setMessage(error instanceof Error ? error.message : 'Could not load comments.')
    })
    return () => { current = false }
  }, [endpoint, expanded, loaded])

  async function submitComment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setMessage('Posting comment…')
    try {
      const response = await taskRequest(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body: draft }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not post comment.')
      setComments((current) => [...current, result as TaskComment])
      setDraft('')
      setMessage('Comment posted.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not post comment.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="task-comments">
      <button type="button" className="task-comments-toggle" aria-expanded={expanded} onClick={() => setExpanded((current) => !current)}>
        {expanded ? 'Hide comments' : 'Comments'}{loaded ? ` (${comments.length})` : ''}
      </button>
      {expanded && <div className="task-comments-panel">
        {comments.length === 0 && loaded && <p className="task-empty">No comments yet.</p>}
        {comments.map((comment) => (
          <article className="task-comment" key={comment.id}>
            <div><strong>{comment.author_display_name}</strong><time>{new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(comment.created_at))}</time></div>
            <p>{comment.body}</p>
          </article>
        ))}
        {canEdit && <form className="task-comment-form" onSubmit={(event) => void submitComment(event)}>
          <textarea value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={10000} rows={2} aria-label="Task comment" placeholder="Write a comment…" required />
          <small>Use @teammate@email to notify a workspace member.</small>
          <button type="submit" disabled={busy || !draft.trim()}>{busy ? 'Posting…' : 'Post comment'}</button>
        </form>}
        {message && <p className="task-message" role="status">{message}</p>}
      </div>}
    </div>
  )
}
