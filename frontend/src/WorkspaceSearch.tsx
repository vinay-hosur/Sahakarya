import { useState, type FormEvent } from 'react'
import { apiBaseUrl } from './api'
import { EvidenceBreakdown, type EvidenceAssessment } from './AssistantPanel'

type Project = { id: string; name: string }
type Provider = { id: string; label: string; model: string }
type SearchResult = {
  document_id: string
  title: string
  project_id: string | null
  category: string
  source_filename: string | null
  excerpt: string
  relevance: number
  updated_at: string
}
type SearchAnswer = {
  query: string
  results: SearchResult[]
  answer: {
    answer: string
    citation_ids: string[]
    evidence_assessment?: EvidenceAssessment
    needs_more_context: boolean
    provider: string
    model: string
  } | null
  detail: string | null
}

function detailFrom(payload: unknown, fallback: string) {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
  }
  return fallback
}

export default function WorkspaceSearch({ workspaceId, projects, providers, defaultProvider }: {
  workspaceId: string
  projects: Project[]
  providers: Provider[]
  defaultProvider: string
}) {
  const [query, setQuery] = useState('')
  const [projectId, setProjectId] = useState('')
  const [selectedProvider, setSelectedProvider] = useState(defaultProvider)
  const [results, setResults] = useState<SearchResult[]>([])
  const [answer, setAnswer] = useState<SearchAnswer['answer']>(null)
  const [detail, setDetail] = useState('')
  const [busy, setBusy] = useState(false)
  const endpoint = `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/documents`

  const provider = providers.some((item) => item.id === selectedProvider)
    ? selectedProvider
    : defaultProvider || providers[0]?.id || ''

  async function runSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setAnswer(null)
    setDetail('Searching native workspace documents…')
    try {
      const params = new URLSearchParams({ q: query.trim(), limit: '10' })
      if (projectId) params.set('project_id', projectId)
      const response = await fetch(`${endpoint}/search?${params}`, { credentials: 'include', headers: { Accept: 'application/json' } })
      const payload = await response.json()
      if (!response.ok) throw new Error(detailFrom(payload, 'Could not search documents.'))
      const rows = payload as SearchResult[]
      setResults(rows)
      setDetail(rows.length ? `${rows.length} matching document${rows.length === 1 ? '' : 's'} found.` : 'No matching passages were found.')
    } catch (error) {
      setResults([])
      setDetail(error instanceof Error ? error.message : 'Could not search documents.')
    } finally {
      setBusy(false)
    }
  }

  async function askDocuments() {
    setBusy(true)
    setAnswer(null)
    setDetail('Preparing a grounded answer from matching excerpts…')
    try {
      const response = await fetch(`${endpoint}/search/answer`, {
        method: 'POST', credentials: 'include', headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim(), project_id: projectId || null, limit: 10, provider: provider || undefined }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(detailFrom(payload, 'Could not ask the documents.'))
      const result = payload as SearchAnswer
      setResults(result.results)
      setAnswer(result.answer)
      setDetail(result.detail || (result.answer ? 'Grounded answer ready.' : 'No answer was generated.'))
    } catch (error) {
      setDetail(error instanceof Error ? error.message : 'Could not ask the documents.')
    } finally {
      setBusy(false)
    }
  }

  const citedResults = answer ? results.filter((result) => answer.citation_ids.includes(result.document_id)) : []
  const evidenceCitations = citedResults.map((result) => ({
    id: result.document_id,
    title: result.title,
    provider: 'native_document',
    source_url: null,
  }))
  return (
    <section className="workspace-search" aria-labelledby="workspace-search-title">
      <div className="section-heading">
        <div><div className="card-kicker">NATIVE KNOWLEDGE SEARCH</div><h2 id="workspace-search-title">Find answers in workspace documents</h2></div>
        <p>Search extracted passages from uploaded files and native documents. AI answers use matching excerpts only when you request one.</p>
      </div>
      <form className="workspace-search-form" onSubmit={(event) => void runSearch(event)}>
        <label className="workspace-search-query">Question or keywords<input value={query} onChange={(event) => setQuery(event.target.value)} minLength={2} maxLength={1000} placeholder="e.g. How does authentication work?" required /></label>
        <label>Project filter<select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">All workspace documents</option>{projects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}</select></label>
        <label>AI model<select value={provider} onChange={(event) => setSelectedProvider(event.target.value)} disabled={!providers.length}>{providers.length ? providers.map((item) => <option value={item.id} key={item.id}>{item.label} · {item.model}</option>) : <option value="">No model configured</option>}</select></label>
        <div className="workspace-search-actions"><button type="submit" disabled={busy}>{busy ? 'Searching…' : 'Search documents'}</button><button type="button" disabled={busy || query.trim().length < 2} onClick={() => void askDocuments()}>Ask AI from excerpts</button></div>
      </form>
      <p className="workspace-search-privacy">“Ask AI from excerpts” sends up to 8 matched passages to the selected model provider. Plain document search stays in SAHAKARYA.</p>
      {detail && <p className="workspace-search-status" role="status">{detail}</p>}
      {answer && <article className="workspace-search-answer"><span className="card-kicker">GROUNDED ANSWER · {answer.model}</span><p>{answer.answer}</p>{answer.needs_more_context && <strong>More document evidence may be needed to answer this fully.</strong>}{answer.evidence_assessment && <EvidenceBreakdown assessment={answer.evidence_assessment} citations={evidenceCitations} />}{citedResults.length > 0 && <div className="workspace-answer-citations"><strong>Sources</strong>{citedResults.map((result) => <a key={result.document_id} href={`#knowledge`}>{result.title}{result.source_filename ? ` · ${result.source_filename}` : ''}</a>)}</div>}</article>}
      {results.length > 0 && <div className="workspace-search-results" aria-label="Matching document passages">{results.map((result) => <article className="workspace-search-result" key={`${result.document_id}:${result.excerpt}`}><div><strong>{result.title}</strong><span>{result.category}{result.source_filename ? ` · ${result.source_filename}` : ''}</span></div><p>{result.excerpt}</p></article>)}</div>}
    </section>
  )
}
