import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { apiBaseUrl } from './api'

type Project = { id: string; name: string }
type Provider = { id: string; label: string; model: string }
type Citation = { id: string; title: string; provider: string; source_url: string | null }
type EvidenceClaim = { statement: string; citations: string[] }
export type EvidenceAssessment = {
  facts: EvidenceClaim[]
  interpretations: EvidenceClaim[]
  recommendations: EvidenceClaim[]
  unknowns: string[]
  conflicts: { topic: string; views: EvidenceClaim[] }[]
}
type Message = {
  id: string
  role: 'user' | 'assistant'
  content: string
  author_display_name: string | null
  provider: string | null
  model: string | null
  citations: Citation[]
  evidence_assessment: EvidenceAssessment | null
  created_at: string
}
type Memory = {
  id: string
  project_id: string | null
  content: string
  created_by_name: string
  created_at: string
}

function errorDetail(payload: unknown, fallback: string) {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
  }
  return fallback
}

function shortDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(value))
}

function EvidenceLinks({ ids, citations }: { ids: string[]; citations: Citation[] }) {
  const sources = ids.map((id) => citations.find((citation) => citation.id === id)).filter((item): item is Citation => Boolean(item))
  if (!sources.length) return null
  return <span className="assistant-evidence-links">{sources.map((citation) => citation.source_url?.startsWith('http')
    ? <a key={citation.id} href={citation.source_url} target="_blank" rel="noreferrer">{citation.title}</a>
    : <a key={citation.id} href="#knowledge">{citation.title}</a>)}</span>
}

export function EvidenceBreakdown({ assessment, citations }: { assessment: EvidenceAssessment; citations: Citation[] }) {
  const hasEvidence = assessment.facts.length + assessment.interpretations.length + assessment.recommendations.length + assessment.unknowns.length + assessment.conflicts.length > 0
  if (!hasEvidence) return null
  return <details className="assistant-evidence-breakdown">
    <summary>Evidence breakdown</summary>
    {assessment.facts.length > 0 && <section><h4>Facts</h4><ul>{assessment.facts.map((claim, index) => <li key={`fact-${index}`}>{claim.statement}<EvidenceLinks ids={claim.citations} citations={citations} /></li>)}</ul></section>}
    {assessment.interpretations.length > 0 && <section><h4>Interpretations</h4><ul>{assessment.interpretations.map((claim, index) => <li key={`interpretation-${index}`}>{claim.statement}<EvidenceLinks ids={claim.citations} citations={citations} /></li>)}</ul></section>}
    {assessment.recommendations.length > 0 && <section><h4>Recommendations</h4><ul>{assessment.recommendations.map((claim, index) => <li key={`recommendation-${index}`}>{claim.statement}<EvidenceLinks ids={claim.citations} citations={citations} /></li>)}</ul></section>}
    {assessment.unknowns.length > 0 && <section><h4>Unknowns</h4><ul>{assessment.unknowns.map((unknown, index) => <li key={`unknown-${index}`}>{unknown}</li>)}</ul></section>}
    {assessment.conflicts.length > 0 && <section><h4>Conflicting evidence</h4>{assessment.conflicts.map((conflict, index) => <div className="assistant-evidence-conflict" key={`conflict-${index}`}><strong>{conflict.topic}</strong><ul>{conflict.views.map((view, viewIndex) => <li key={`view-${viewIndex}`}>{view.statement}<EvidenceLinks ids={view.citations} citations={citations} /></li>)}</ul></div>)}</section>}
  </details>
}

export default function AssistantPanel({
  workspaceId,
  projects,
  providers,
  defaultProvider,
  canWrite,
}: {
  workspaceId: string
  projects: Project[]
  providers: Provider[]
  defaultProvider: string
  canWrite: boolean
}) {
  const [messages, setMessages] = useState<Message[]>([])
  const [memories, setMemories] = useState<Memory[]>([])
  const [selectedProvider, setSelectedProvider] = useState('')
  const [projectId, setProjectId] = useState('')
  const [prompt, setPrompt] = useState('')
  const [memoryDraft, setMemoryDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [memoryBusy, setMemoryBusy] = useState(false)
  const [detail, setDetail] = useState('')
  const endpoint = `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/assistant`

  const provider = providers.some((item) => item.id === selectedProvider)
    ? selectedProvider
    : defaultProvider || providers[0]?.id || ''

  const loadWorkspaceAssistant = useCallback(async () => {
    if (!workspaceId) return
    try {
      const [messageResponse, memoryResponse] = await Promise.all([
        fetch(`${endpoint}/messages?limit=100`, { credentials: 'include', headers: { Accept: 'application/json' } }),
        fetch(`${endpoint}/memories`, { credentials: 'include', headers: { Accept: 'application/json' } }),
      ])
      if (messageResponse.ok) setMessages((await messageResponse.json()) as Message[])
      if (memoryResponse.ok) setMemories((await memoryResponse.json()) as Memory[])
    } catch {
      setDetail('Could not load workspace assistant history.')
    }
  }, [endpoint, workspaceId])

  useEffect(() => {
    const timer = window.setTimeout(() => void loadWorkspaceAssistant(), 0)
    return () => window.clearTimeout(timer)
  }, [loadWorkspaceAssistant])

  async function sendMessage(event?: FormEvent<HTMLFormElement>, suggestedPrompt?: string) {
    event?.preventDefault()
    const question = (suggestedPrompt || prompt).trim()
    if (!question || !provider || busy) return
    setBusy(true)
    setDetail('Grounding a response in workspace projects, tasks, documents, decisions, and saved memory…')
    try {
      const response = await fetch(`${endpoint}/chat`, {
        method: 'POST', credentials: 'include',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: question, project_id: projectId || null, provider }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(errorDetail(payload, 'The assistant could not answer this request.'))
      setPrompt('')
      await loadWorkspaceAssistant()
      setDetail('Reply saved to this workspace conversation.')
    } catch (error) {
      setDetail(error instanceof Error ? error.message : 'The assistant could not answer this request.')
    } finally {
      setBusy(false)
    }
  }

  async function saveMemory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const content = memoryDraft.trim()
    if (!content || memoryBusy) return
    setMemoryBusy(true)
    setDetail('Saving workspace memory…')
    try {
      const response = await fetch(`${endpoint}/memories`, {
        method: 'POST', credentials: 'include',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, project_id: projectId || null }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(errorDetail(payload, 'Could not save this memory.'))
      setMemoryDraft('')
      await loadWorkspaceAssistant()
      setDetail('Memory saved. The assistant will use it in future conversations.')
    } catch (error) {
      setDetail(error instanceof Error ? error.message : 'Could not save this memory.')
    } finally {
      setMemoryBusy(false)
    }
  }

  async function removeMemory(memoryId: string) {
    setMemoryBusy(true)
    try {
      const response = await fetch(`${endpoint}/memories/${memoryId}`, { method: 'DELETE', credentials: 'include' })
      if (!response.ok) {
        const payload = await response.json()
        throw new Error(errorDetail(payload, 'Could not remove this memory.'))
      }
      setMemories((current) => current.filter((item) => item.id !== memoryId))
      setDetail('Saved memory removed.')
    } catch (error) {
      setDetail(error instanceof Error ? error.message : 'Could not remove this memory.')
    } finally {
      setMemoryBusy(false)
    }
  }

  const prompts = [
    'Write an onboarding brief for this project using the workspace knowledge.',
    'Summarize open tasks, risks, and the next useful steps.',
    'Draft a concise project update from the latest workspace evidence.',
  ]
  const projectName = (id: string | null) => projects.find((project) => project.id === id)?.name || 'Workspace-wide'

  return (
    <section className="assistant-workbench" aria-labelledby="assistant-title">
      <header className="assistant-heading">
        <div>
          <div className="card-kicker">TEAM COPILOT · PERSISTENT WORKSPACE MEMORY</div>
          <h2 id="assistant-title">Ask your workspace</h2>
          <p>Get onboarding briefs, project updates, and next steps grounded in your team’s saved work.</p>
        </div>
        <div className="assistant-controls">
          <label>Project context<select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">Entire workspace</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
          <label>AI model<select value={provider} onChange={(event) => setSelectedProvider(event.target.value)} disabled={!providers.length}>{providers.length ? providers.map((item) => <option key={item.id} value={item.id}>{item.label} · {item.model}</option>) : <option value="">No model configured</option>}</select></label>
        </div>
      </header>

      <div className="assistant-grid">
        <div className="assistant-conversation">
          <div className="assistant-conversation-top"><div><strong>Workspace conversation</strong><span>Shared with workspace members · retained between visits</span></div><span className="assistant-history-count">{messages.length} messages</span></div>
          <div className="assistant-messages" aria-live="polite">
            {messages.length === 0 && <div className="assistant-empty"><span className="assistant-spark">✦</span><strong>Bring the project context together</strong><p>Ask about team decisions, documents, tasks, or what a new contributor should know.</p></div>}
            {messages.map((message) => <article key={message.id} className={`assistant-message ${message.role}`}>
              <div className="assistant-message-meta"><strong>{message.role === 'assistant' ? 'SAHAKARYA' : message.author_display_name || 'Workspace member'}</strong><time>{shortDate(message.created_at)}</time>{message.provider && <span>{message.provider} · {message.model}</span>}</div>
              <p>{message.content}</p>
              {message.evidence_assessment && <EvidenceBreakdown assessment={message.evidence_assessment} citations={message.citations} />}
              {message.citations.length > 0 && <div className="assistant-citations"><span>Evidence</span>{message.citations.map((citation) => citation.source_url?.startsWith('http')
                ? <a key={citation.id} href={citation.source_url} target="_blank" rel="noreferrer">{citation.title} ↗</a>
                : <a key={citation.id} href="#knowledge">{citation.title} · {citation.provider}</a>)}</div>}
            </article>)}
          </div>
          <div className="assistant-prompt-suggestions" aria-label="Suggested prompts">{prompts.map((item) => <button type="button" key={item} onClick={() => setPrompt(item)} disabled={busy || !providers.length}>{item.startsWith('Write') ? 'Create onboarding brief' : item.startsWith('Summarize') ? 'Summarize open work' : 'Draft project update'}</button>)}</div>
          <form className="assistant-compose" onSubmit={(event) => void sendMessage(event)}>
            <label className="sr-only" htmlFor="assistant-prompt">Ask SAHAKARYA</label>
            <textarea id="assistant-prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={3000} rows={3} placeholder="Ask for a project brief, decision summary, or next-step recommendation…" disabled={!providers.length || busy} />
            <button type="submit" disabled={!prompt.trim() || !provider || busy}>{busy ? 'Thinking…' : 'Send'}</button>
          </form>
          <p className="assistant-privacy">The selected model receives your prompt and a bounded set of matching workspace records. API keys stay on the server.</p>
        </div>

        <aside className="assistant-memory-panel" aria-labelledby="memory-title">
          <div className="assistant-memory-heading"><div><div className="card-kicker">REUSABLE TEAM CONTEXT</div><h3 id="memory-title">Workspace memory</h3></div><span>{memories.length}/100</span></div>
          <p className="assistant-memory-intro">Save conventions, constraints, or decisions the assistant should remember across conversations.</p>
          {canWrite && <form className="assistant-memory-form" onSubmit={(event) => void saveMemory(event)}><label className="sr-only" htmlFor="memory-draft">Add a memory</label><textarea id="memory-draft" value={memoryDraft} onChange={(event) => setMemoryDraft(event.target.value)} maxLength={1000} rows={3} placeholder="e.g. The API must stay backwards compatible through the Q2 migration." /><button type="submit" disabled={memoryBusy || memoryDraft.trim().length < 3}>{memoryBusy ? 'Saving…' : 'Save memory'}</button></form>}
          <div className="assistant-memory-list">
            {memories.length === 0 && <p className="assistant-memory-empty">No saved memory yet. Add a useful fact and the assistant will carry it forward.</p>}
            {memories.map((memory) => <article className="assistant-memory-item" key={memory.id}><div><span>{projectName(memory.project_id)}</span><small>Added by {memory.created_by_name}</small></div><p>{memory.content}</p>{canWrite && <button type="button" onClick={() => void removeMemory(memory.id)} disabled={memoryBusy} aria-label="Remove memory">Remove</button>}</article>)}
          </div>
          <p className="assistant-memory-scope">Memory is shared with workspace members. Project-specific memories are included only when that project is selected.</p>
        </aside>
      </div>
      {detail && <p className="assistant-status" role="status">{detail}</p>}
    </section>
  )
}
