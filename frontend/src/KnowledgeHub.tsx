import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { apiBaseUrl } from './api'

type Project = { id: string; name: string }
type Document = {
  id: string
  workspace_id: string
  project_id: string | null
  owner_user_id: string
  owner_display_name: string
  title: string
  content: string
  category: string
  tags: string[]
  version_number: number
  source_filename: string | null
  created_at: string
  updated_at: string
}
type Version = {
  id: string
  version_number: number
  snapshot: Record<string, unknown>
  created_by_name: string
  created_at: string
}
type DocumentComment = {
  id: string
  author_display_name: string
  body: string
  created_at: string
}

const MAX_UPLOAD_BYTES = 50 * 1024 * 1024
const DATABASE_UPLOAD_LIMIT_BYTES = 5 * 1024 * 1024

async function request(path: string, init: RequestInit = {}) {
  return fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: 'include',
    headers: { Accept: 'application/json', ...init.headers },
  })
}

function messageFrom(payload: unknown, fallback: string) {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
  }
  return fallback
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(
    new Date(value),
  )
}

export default function KnowledgeHub({
  workspaceId,
  projects,
  userId,
  isWorkspaceOwner,
  canWrite,
  objectStorageConfigured,
}: {
  workspaceId: string
  projects: Project[]
  userId: string
  isWorkspaceOwner: boolean
  canWrite: boolean
  objectStorageConfigured: boolean
}) {
  const [documents, setDocuments] = useState<Document[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [versions, setVersions] = useState<Version[]>([])
  const [comments, setComments] = useState<DocumentComment[]>([])
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [detailsMessage, setDetailsMessage] = useState('')
  const [editing, setEditing] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)

  const endpoint = `/api/v1/workspaces/${workspaceId}/documents`
  const selected = documents.find((document) => document.id === selectedId) || null
  const mayEditSelected = Boolean(
    selected && canWrite && (isWorkspaceOwner || selected.owner_user_id === userId),
  )
  const visibleDocuments = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    if (!query) return documents
    return documents.filter((document) =>
      `${document.title} ${document.content} ${document.category} ${document.tags.join(' ')}`
        .toLocaleLowerCase()
        .includes(query),
    )
  }, [documents, search])

  useEffect(() => {
    let current = true
    void request(endpoint).then(async (response) => {
      const payload = await response.json()
      if (!response.ok) throw new Error(messageFrom(payload, 'Could not load documents.'))
      if (current) {
        const rows = payload as Document[]
        setDocuments(rows)
        setSelectedId((existing) => rows.some((document) => document.id === existing) ? existing : '')
      }
    }).catch((error) => {
      if (current) setMessage(error instanceof Error ? error.message : 'Could not load documents.')
    })
    return () => { current = false }
  }, [endpoint])

  useEffect(() => {
    if (!selectedId) return
    let current = true
    const load = async () => {
      setDetailsMessage('Loading document history…')
      const [versionResponse, commentResponse] = await Promise.all([
        request(`${endpoint}/${selectedId}/versions`),
        request(`${endpoint}/${selectedId}/comments`),
      ])
      const [versionPayload, commentPayload] = await Promise.all([
        versionResponse.json(),
        commentResponse.json(),
      ])
      if (!versionResponse.ok) throw new Error(messageFrom(versionPayload, 'Could not load versions.'))
      if (!commentResponse.ok) throw new Error(messageFrom(commentPayload, 'Could not load comments.'))
      if (current) {
        setVersions(versionPayload as Version[])
        setComments(commentPayload as DocumentComment[])
        setDetailsMessage('')
      }
    }
    void load().catch((error) => {
      if (current) setDetailsMessage(error instanceof Error ? error.message : 'Could not load document history.')
    })
    return () => { current = false }
  }, [endpoint, selectedId])

  async function createDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const values = new FormData(form)
    setBusy(true)
    setMessage('Creating document…')
    try {
      const response = await request(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: String(values.get('title') || ''),
          content: String(values.get('content') || ''),
          category: String(values.get('category') || 'General'),
          tags: String(values.get('tags') || '').split(',').map((tag) => tag.trim()).filter(Boolean),
          project_id: String(values.get('project_id') || '') || null,
        }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(messageFrom(payload, 'Could not create document.'))
      const document = payload as Document
      setDocuments((current) => [document, ...current])
      setSelectedId(document.id)
      form.reset()
      setMessage('Document created. Version 1 is saved.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not create document.')
    } finally {
      setBusy(false)
    }
  }

  async function uploadDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const values = new FormData(form)
    if (!values.get('project_id')) values.delete('project_id')
    const selectedFile = values.get('file')
    if (!(selectedFile instanceof File) || selectedFile.size === 0) {
      setMessage('Choose a PDF, DOCX, Markdown, or TXT file first.')
      return
    }
    if (selectedFile.size > MAX_UPLOAD_BYTES) {
      setMessage('Files must be 50 MiB or smaller.')
      return
    }
    if (!objectStorageConfigured && selectedFile.size > DATABASE_UPLOAD_LIMIT_BYTES) {
      setMessage('Files above 5 MiB need Cloudflare R2 storage. Ask the workspace administrator to finish file storage setup.')
      return
    }
    setBusy(true)
    setUploading(true)
    setUploadProgress(0)
    setMessage('Uploading file…')
    try {
      const payload = await new Promise<unknown>((resolve, reject) => {
        const xhr = new XMLHttpRequest()
        xhr.open('POST', `${apiBaseUrl}${endpoint}/upload`)
        xhr.withCredentials = true
        xhr.setRequestHeader('Accept', 'application/json')
        xhr.upload.onprogress = (progress) => {
          if (progress.lengthComputable) setUploadProgress(Math.round((progress.loaded / progress.total) * 100))
        }
        xhr.onload = () => {
          let responsePayload: unknown = {}
          try { responsePayload = JSON.parse(xhr.responseText) as unknown } catch { /* handled below */ }
          if (xhr.status >= 200 && xhr.status < 300) resolve(responsePayload)
          else reject(new Error(messageFrom(responsePayload, `Upload failed (${xhr.status}).`)))
        }
        xhr.onerror = () => reject(new Error('The upload connection was interrupted. Try again; incomplete uploads are discarded.'))
        xhr.onabort = () => reject(new Error('Upload was cancelled.'))
        xhr.send(values)
      })
      const document = payload as Document
      setDocuments((current) => [document, ...current])
      setSelectedId(document.id)
      form.reset()
      setMessage('File uploaded, text extracted, and searchable content indexed.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not upload document.')
    } finally {
      setBusy(false)
      setUploading(false)
      setUploadProgress(0)
    }
  }

  async function updateDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selected) return
    const values = new FormData(event.currentTarget)
    setBusy(true)
    setDetailsMessage('Saving a new document version…')
    try {
      const response = await request(`${endpoint}/${selected.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: String(values.get('title') || ''),
          content: String(values.get('content') || ''),
          category: String(values.get('category') || 'General'),
          tags: String(values.get('tags') || '').split(',').map((tag) => tag.trim()).filter(Boolean),
          project_id: String(values.get('project_id') || '') || null,
        }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(messageFrom(payload, 'Could not update document.'))
      const document = payload as Document
      setDocuments((current) => current.map((item) => item.id === document.id ? document : item))
      setEditing(false)
      setDetailsMessage('Document updated and version history saved.')
      const history = await request(`${endpoint}/${document.id}/versions`)
      if (history.ok) setVersions((await history.json()) as Version[])
    } catch (error) {
      setDetailsMessage(error instanceof Error ? error.message : 'Could not update document.')
    } finally {
      setBusy(false)
    }
  }

  async function addComment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selected) return
    const form = event.currentTarget
    const body = String(new FormData(form).get('comment') || '')
    setBusy(true)
    setDetailsMessage('Posting comment…')
    try {
      const response = await request(`${endpoint}/${selected.id}/comments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(messageFrom(payload, 'Could not post comment.'))
      setComments((current) => [...current, payload as DocumentComment])
      form.reset()
      setDetailsMessage('Comment posted.')
    } catch (error) {
      setDetailsMessage(error instanceof Error ? error.message : 'Could not post comment.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="knowledge-section" id="knowledge" aria-labelledby="knowledge-title">
      <div className="section-heading">
        <div><div className="card-kicker">NATIVE KNOWLEDGE HUB</div><h2 id="knowledge-title">Keep project knowledge versioned</h2></div>
        <p>Documents, tags, comments, and edit history live in this workspace and can be linked to its projects.</p>
      </div>
      {canWrite && <form className="knowledge-create-form" onSubmit={(event) => void createDocument(event)}>
        <label>Title<input name="title" minLength={2} maxLength={240} placeholder="e.g. Authentication design" required /></label>
        <label>Category<input name="category" maxLength={100} defaultValue="General" required /></label>
        <label>Project<select name="project_id" defaultValue=""><option value="">Workspace document</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
        <label>Tags<input name="tags" placeholder="architecture, onboarding" maxLength={500} /></label>
        <label className="knowledge-content-field">Document<textarea name="content" rows={4} maxLength={100000} placeholder="Write the knowledge your team should keep…" required /></label>
        <button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Create document'}</button>
      </form>}
      {canWrite && <form className="knowledge-upload-form" onSubmit={(event) => void uploadDocument(event)}>
        <div><strong>Import a file</strong><span>PDF, DOCX, Markdown, or TXT · up to 50 MiB{objectStorageConfigured ? ' · Cloudflare R2 storage ready' : ' · up to 5 MiB until object storage is configured'} · text extraction only</span></div>
        <label>File<input name="file" type="file" accept=".pdf,.docx,.md,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/markdown,text/plain" required /></label>
        <label>Title override<input name="title" maxLength={240} placeholder="Defaults to the filename" /></label>
        <label>Category<input name="category" maxLength={100} defaultValue="General" required /></label>
        <label>Project<select name="project_id" defaultValue=""><option value="">Workspace document</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
        <label>Tags<input name="tags" maxLength={500} placeholder="architecture, onboarding" /></label>
        {uploading && <div className="upload-progress"><progress max={100} value={uploadProgress} aria-label="File upload progress" /><span>{uploadProgress}% uploaded · indexing starts after transfer</span></div>}
        <button type="submit" disabled={busy}>{uploading ? 'Uploading and indexing…' : 'Upload and index'}</button>
      </form>}
      {message && <p className="knowledge-message" role="status">{message}</p>}
      <div className="knowledge-layout">
        <aside className="knowledge-library" aria-label="Document library">
          <label className="knowledge-search">Search documents<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search title, content, category or tag" /></label>
          {visibleDocuments.length === 0 ? <div className="knowledge-empty"><strong>No matching documents.</strong><span>Create a document above or adjust your search.</span></div> : visibleDocuments.map((document) => (
            <button className={`knowledge-document-option ${selectedId === document.id ? 'selected' : ''}`} type="button" key={document.id} onClick={() => { setSelectedId(document.id); setEditing(false) }}>
              <strong>{document.title}</strong><span>{document.category} · v{document.version_number}</span><small>{document.tags.join(' · ') || 'No tags'}</small>
            </button>
          ))}
        </aside>
        <article className="knowledge-detail">
          {!selected ? <div className="knowledge-empty"><strong>Select a document</strong><span>Document content and revision history will appear here.</span></div> : <>
            <div className="knowledge-detail-heading">
              <div><span className="knowledge-category">{selected.category}</span><h3>{selected.title}</h3><p>Owned by {selected.owner_display_name} · Updated {formatDate(selected.updated_at)}</p>{selected.source_filename && <a className="knowledge-source-file" href={`${apiBaseUrl}${endpoint}/${selected.id}/file`}>Original file: {selected.source_filename} ↓</a>}</div>
              {mayEditSelected && <button type="button" className="knowledge-edit-button" onClick={() => setEditing((current) => !current)}>{editing ? 'Cancel edit' : 'Edit document'}</button>}
            </div>
            {editing ? <form className="knowledge-edit-form" onSubmit={(event) => void updateDocument(event)}>
              <label>Title<input name="title" defaultValue={selected.title} minLength={2} maxLength={240} required /></label>
              <label>Category<input name="category" defaultValue={selected.category} maxLength={100} required /></label>
              <label>Project<select name="project_id" defaultValue={selected.project_id || ''}><option value="">Workspace document</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
              <label>Tags<input name="tags" defaultValue={selected.tags.join(', ')} maxLength={500} /></label>
              <label>Content<textarea name="content" defaultValue={selected.content} rows={8} maxLength={100000} required /></label>
              <button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save new version'}</button>
            </form> : <div className="knowledge-document-content">{selected.content}</div>}
            <div className="knowledge-document-meta"><span>Project: {projects.find((project) => project.id === selected.project_id)?.name || 'Workspace-wide'}</span><span>Tags: {selected.tags.join(', ') || 'None'}</span></div>
            <div className="knowledge-subsection"><h4>Version history</h4>{versions.map((version) => <details className="knowledge-version" key={version.id}>
              <summary>Version {version.version_number} · {version.created_by_name} · {formatDate(version.created_at)}</summary>
              <strong>{String(version.snapshot.title || '')}</strong><p>{String(version.snapshot.content || '')}</p>
            </details>)}</div>
            <div className="knowledge-subsection"><h4>Comments ({comments.length})</h4>
              {comments.map((comment) => <article className="knowledge-comment" key={comment.id}><strong>{comment.author_display_name}</strong><time>{formatDate(comment.created_at)}</time><p>{comment.body}</p></article>)}
              {canWrite && <form className="knowledge-comment-form" onSubmit={(event) => void addComment(event)}><textarea name="comment" rows={2} maxLength={10000} placeholder="Add a document comment…" required /><button type="submit" disabled={busy}>{busy ? 'Posting…' : 'Post comment'}</button></form>}
            </div>
          </>}
          {detailsMessage && <p className="knowledge-message" role="status">{detailsMessage}</p>}
        </article>
      </div>
    </section>
  )
}
