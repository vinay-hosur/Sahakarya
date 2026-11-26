import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { apiBaseUrl } from './api'

type Project = { id: string; name: string }
type Member = { user_id: string; display_name: string; email: string; role: string }
type DocumentOption = { id: string; title: string }
type TaskOption = { id: string; title: string; project_id: string }
type DecisionReference = {
  id: string
  document_id: string | null
  task_id: string | null
  source_url: string | null
  label: string
  notes: string
}
type Decision = {
  id: string
  project_id: string
  owner_user_id: string
  owner_display_name: string
  title: string
  description: string
  reasoning: string
  alternatives: string[]
  decision_date: string
  status: 'proposed' | 'approved' | 'rejected' | 'superseded'
  version_number: number
  references: DecisionReference[]
}
type HistoryEntry = {
  id: string
  version_number: number
  snapshot: Record<string, unknown>
  edited_by_name: string
  created_at: string
}

async function request(path: string, init: RequestInit = {}) {
  return fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: 'include',
    headers: { Accept: 'application/json', ...init.headers },
  })
}

function apiMessage(payload: unknown, fallback: string) {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
  }
  return fallback
}

function localDate() {
  const date = new Date()
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

export default function DecisionBoard({
  workspaceId,
  projects,
  userId,
  canWrite,
}: {
  workspaceId: string
  projects: Project[]
  userId: string
  canWrite: boolean
}) {
  const [projectId, setProjectId] = useState('')
  const activeProjectId = projectId || projects[0]?.id || ''
  const [documents, setDocuments] = useState<DocumentOption[]>([])
  const [tasks, setTasks] = useState<TaskOption[]>([])
  const [members, setMembers] = useState<Member[]>([])
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [detailMessage, setDetailMessage] = useState('')
  const selected = decisions.find((decision) => decision.id === selectedId) || null
  const projectEndpoint = `/api/v1/workspaces/${workspaceId}/projects/${activeProjectId}/decisions`
  const currentMembers = useMemo(() => members.filter((member) => member.role !== 'former'), [members])

  useEffect(() => {
    let current = true
    const loadOptions = async () => {
      const documentResponse = await request(`/api/v1/workspaces/${workspaceId}/documents`)
      const documentPayload = await documentResponse.json()
      if (!documentResponse.ok) throw new Error(apiMessage(documentPayload, 'Could not load document references.'))
      const memberResponse = await request(`/api/v1/workspaces/${workspaceId}/members`)
      const memberPayload = await memberResponse.json()
      if (!memberResponse.ok) throw new Error(apiMessage(memberPayload, 'Could not load decision owners.'))
      const taskResults = await Promise.all(projects.map(async (project) => {
        const response = await request(`/api/v1/workspaces/${workspaceId}/projects/${project.id}/tasks`)
        if (!response.ok) return [] as TaskOption[]
        return (await response.json() as Array<{ id: string; title: string; project_id: string }>)
      }))
      if (current) {
        setDocuments((documentPayload as Array<{ id: string; title: string }>).map(({ id, title }) => ({ id, title })))
        setTasks(taskResults.flat())
        setMembers(memberPayload as Member[])
      }
    }
    void loadOptions().catch((error) => {
      if (current) setMessage(error instanceof Error ? error.message : 'Could not load decision references.')
    })
    return () => { current = false }
  }, [workspaceId, projects])

  useEffect(() => {
    if (!activeProjectId) return
    let current = true
    void request(projectEndpoint).then(async (response) => {
      const payload = await response.json()
      if (!response.ok) throw new Error(apiMessage(payload, 'Could not load decisions.'))
      if (current) {
        const rows = payload as Decision[]
        setDecisions(rows)
        setSelectedId((existing) => rows.some((decision) => decision.id === existing) ? existing : '')
      }
    }).catch((error) => {
      if (current) setMessage(error instanceof Error ? error.message : 'Could not load decisions.')
    })
    return () => { current = false }
  }, [projectEndpoint, activeProjectId])

  useEffect(() => {
    if (!selectedId) return
    let current = true
    void request(`${projectEndpoint}/${selectedId}/history`).then(async (response) => {
      const payload = await response.json()
      if (!response.ok) throw new Error(apiMessage(payload, 'Could not load decision history.'))
      if (current) setHistory(payload as HistoryEntry[])
    }).catch((error) => {
      if (current) setDetailMessage(error instanceof Error ? error.message : 'Could not load decision history.')
    })
    return () => { current = false }
  }, [projectEndpoint, selectedId])

  function buildReferences(values: FormData): Array<Record<string, string>> {
    const references: Array<Record<string, string>> = []
    const documentId = String(values.get('document_id') || '')
    const taskId = String(values.get('task_id') || '')
    const sourceUrl = String(values.get('source_url') || '').trim()
    const evidenceNotes = String(values.get('evidence_notes') || '').trim()
    const evidenceLabel = String(values.get('evidence_label') || '').trim()
    if (documentId) {
      references.push({
        document_id: documentId,
        label: documents.find((document) => document.id === documentId)?.title || 'Related document',
        notes: evidenceNotes,
      })
    }
    if (taskId) {
      references.push({
        task_id: taskId,
        label: tasks.find((task) => task.id === taskId)?.title || 'Related task',
        notes: evidenceNotes,
      })
    }
    if (sourceUrl) references.push({ source_url: sourceUrl, label: evidenceLabel || 'Supporting source', notes: evidenceNotes })
    return references
  }

  async function createDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!activeProjectId) return
    const form = event.currentTarget
    const values = new FormData(form)
    setBusy(true)
    setMessage('Recording decision…')
    try {
      const response = await request(projectEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: String(values.get('title') || ''),
          description: String(values.get('description') || ''),
          owner_user_id: String(values.get('owner_user_id') || userId),
          decision_date: String(values.get('decision_date') || localDate()),
          reasoning: String(values.get('reasoning') || ''),
          alternatives: String(values.get('alternatives') || '').split('\n').map((item) => item.trim()).filter(Boolean),
          status: String(values.get('status') || 'proposed'),
          references: buildReferences(values),
        }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(apiMessage(payload, 'Could not save decision.'))
      const decision = payload as Decision
      setDecisions((current) => [decision, ...current])
      setSelectedId(decision.id)
      form.reset()
      setMessage('Decision recorded with a history snapshot.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not save decision.')
    } finally {
      setBusy(false)
    }
  }

  async function updateDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selected) return
    const values = new FormData(event.currentTarget)
    setBusy(true)
    setDetailMessage('Saving decision history…')
    try {
      const response = await request(`${projectEndpoint}/${selected.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: String(values.get('title') || ''),
          description: String(values.get('description') || ''),
          owner_user_id: String(values.get('owner_user_id') || userId),
          decision_date: String(values.get('decision_date') || selected.decision_date),
          reasoning: String(values.get('reasoning') || ''),
          alternatives: String(values.get('alternatives') || '').split('\n').map((item) => item.trim()).filter(Boolean),
          status: String(values.get('status') || selected.status),
        }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(apiMessage(payload, 'Could not update decision.'))
      const decision = payload as Decision
      setDecisions((current) => current.map((item) => item.id === decision.id ? decision : item))
      setEditing(false)
      const historyResponse = await request(`${projectEndpoint}/${decision.id}/history`)
      if (historyResponse.ok) setHistory((await historyResponse.json()) as HistoryEntry[])
      setDetailMessage('Decision updated and a new history snapshot saved.')
    } catch (error) {
      setDetailMessage(error instanceof Error ? error.message : 'Could not update decision.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="decision-section" id="decisions" aria-labelledby="decisions-title">
      <div className="section-heading"><div><div className="card-kicker">NATIVE DECISION INTELLIGENCE</div><h2 id="decisions-title">Keep the why with the decision</h2></div><p>Record the owner, date, alternatives, reasoning, and the project evidence that supports each decision.</p></div>
      {projects.length === 0 ? <div className="project-empty-state"><strong>Create a project first.</strong><span>Decisions are always attached to a project.</span></div> : <>
        <label className="decision-project-select">Project<select value={activeProjectId} onChange={(event) => { setProjectId(event.target.value); setSelectedId(''); setEditing(false) }}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
        {canWrite && <form className="decision-create-form" onSubmit={(event) => void createDecision(event)}>
          <div className="decision-form-heading"><strong>Record a decision</strong><span>Each creation is saved as version 1.</span></div>
          <label>Title<input name="title" minLength={2} maxLength={240} required placeholder="e.g. Rotate refresh tokens" /></label>
          <label>Owner<select name="owner_user_id" defaultValue={userId}>{currentMembers.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name} · {member.role}</option>)}</select></label>
          <label>Date<input name="decision_date" type="date" defaultValue={localDate()} required /></label>
          <label>Status<select name="status" defaultValue="proposed"><option value="proposed">Proposed</option><option value="approved">Approved</option><option value="rejected">Rejected</option><option value="superseded">Superseded</option></select></label>
          <label className="decision-wide">Description<textarea name="description" rows={2} maxLength={20000} required /></label>
          <label>Reasoning<textarea name="reasoning" rows={3} maxLength={20000} /></label>
          <label>Alternatives (one per line)<textarea name="alternatives" rows={3} maxLength={10000} placeholder="Short-lived tokens only\nLong-lived refresh token" /></label>
          <label>Related document<select name="document_id" defaultValue=""><option value="">None</option>{documents.map((document) => <option key={document.id} value={document.id}>{document.title}</option>)}</select></label>
          <label>Related task<select name="task_id" defaultValue=""><option value="">None</option>{tasks.filter((task) => task.project_id === activeProjectId).map((task) => <option key={task.id} value={task.id}>{task.title}</option>)}</select></label>
          <label>Evidence URL<input name="source_url" type="url" placeholder="https://…" /></label>
          <label>Evidence label<input name="evidence_label" maxLength={240} placeholder="Supporting source" /></label>
          <label className="decision-wide">Evidence notes<textarea name="evidence_notes" rows={2} maxLength={5000} placeholder="Why this source matters" /></label>
          <button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save decision'}</button>
        </form>}
      </>}
      {message && <p className="decision-message" role="status">{message}</p>}
      <div className="decision-layout">
        <div className="decision-list" aria-label="Project decisions">
          {decisions.length === 0 ? <div className="knowledge-empty"><strong>No decisions recorded.</strong><span>Save the first decision above.</span></div> : decisions.map((decision) => (
            <button type="button" key={decision.id} className={`decision-list-option ${selectedId === decision.id ? 'selected' : ''}`} onClick={() => { setSelectedId(decision.id); setEditing(false) }}>
              <strong>{decision.title}</strong><span>{decision.status} · {decision.decision_date}</span><small>Owner: {decision.owner_display_name}</small>
            </button>
          ))}
        </div>
        <article className="decision-detail">
          {!selected ? <div className="knowledge-empty"><strong>Select a decision</strong><span>Decision reasoning and history appear here.</span></div> : <>
            <div className="decision-detail-heading"><div><span className={`decision-status status-${selected.status}`}>{selected.status}</span><h3>{selected.title}</h3><p>{selected.owner_display_name} · {selected.decision_date} · v{selected.version_number}</p></div>{canWrite && <button type="button" className="knowledge-edit-button" onClick={() => setEditing((current) => !current)}>{editing ? 'Cancel' : 'Edit decision'}</button>}</div>
            {editing ? <form className="decision-edit-form" onSubmit={(event) => void updateDecision(event)}>
              <label>Title<input name="title" defaultValue={selected.title} minLength={2} maxLength={240} required /></label>
              <label>Owner<select name="owner_user_id" defaultValue={selected.owner_user_id}>{currentMembers.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name} · {member.role}</option>)}</select></label>
              <label>Date<input name="decision_date" type="date" defaultValue={selected.decision_date} required /></label>
              <label>Status<select name="status" defaultValue={selected.status}><option value="proposed">Proposed</option><option value="approved">Approved</option><option value="rejected">Rejected</option><option value="superseded">Superseded</option></select></label>
              <label className="decision-wide">Description<textarea name="description" defaultValue={selected.description} rows={2} maxLength={20000} required /></label>
              <label>Reasoning<textarea name="reasoning" defaultValue={selected.reasoning} rows={3} maxLength={20000} /></label>
              <label>Alternatives (one per line)<textarea name="alternatives" defaultValue={selected.alternatives.join('\n')} rows={3} maxLength={10000} /></label>
              <button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save new version'}</button>
            </form> : <>
              <p className="decision-description">{selected.description}</p>
              <div className="decision-subsection"><h4>Reasoning</h4><p>{selected.reasoning || 'No reasoning recorded.'}</p></div>
              <div className="decision-subsection"><h4>Alternatives</h4>{selected.alternatives.length ? <ul>{selected.alternatives.map((alternative, index) => <li key={`${alternative}-${index}`}>{alternative}</li>)}</ul> : <p>No alternatives recorded.</p>}</div>
              <div className="decision-subsection"><h4>Evidence and related work</h4>{selected.references.length ? selected.references.map((reference) => <article className="decision-reference" key={reference.id}><strong>{reference.label}</strong>{reference.notes && <p>{reference.notes}</p>}{reference.source_url && <a href={reference.source_url} target="_blank" rel="noreferrer">Open source</a>}</article>) : <p>No related evidence.</p>}</div>
              <div className="decision-subsection"><h4>Decision history</h4>{history.map((entry) => <details className="knowledge-version" key={entry.id}><summary>Version {entry.version_number} · {entry.edited_by_name} · {formatTime(entry.created_at)}</summary><p>{String(entry.snapshot.description || '')}</p></details>)}</div>
            </>}
          </>}
          {detailMessage && <p className="decision-message" role="status">{detailMessage}</p>}
        </article>
      </div>
    </section>
  )
}
