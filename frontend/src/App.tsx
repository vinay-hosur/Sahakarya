import { useCallback, useEffect, useState, type FormEvent } from 'react'
import KnowledgeHub from './KnowledgeHub'
import WorkspaceSearch from './WorkspaceSearch'
import DecisionBoard from './DecisionBoard'
import ActivityTimeline from './ActivityTimeline'
import ProjectTasks from './ProjectTasks'
import ProjectOverview from './ProjectOverview'
import WorkspaceMembers from './WorkspaceMembers'
import ChatPanel from './ChatPanel'
import AssistantPanel from './AssistantPanel'
import TelegramIntegrationPanel from './TelegramIntegration'
import WorkspaceOverview from './WorkspaceOverview'
import { apiBaseUrl } from './api'
import './App.css'

type CheckStatus = 'loading' | 'available' | 'unavailable'
type AppView = 'overview' | 'workspace' | 'projects' | 'knowledge' | 'decisions' | 'activity' | 'team' | 'messages' | 'assistant' | 'integrations' | 'workflow'
const appViews: AppView[] = ['overview', 'workspace', 'projects', 'knowledge', 'decisions', 'activity', 'team', 'messages', 'assistant', 'integrations', 'workflow']

function readAppView(): AppView {
  const requested = window.location.hash.slice(1).split('?')[0] as AppView
  return appViews.includes(requested) ? requested : 'overview'
}

function readInvitationToken(): string {
  const hashQuery = window.location.hash.split('?')[1]
  return new URLSearchParams(hashQuery || window.location.search).get('invite') || ''
}

const slackOAuthMessages: Record<string, string> = {
  connected: 'Slack connected and its MCP tools were verified.',
  cancelled: 'Slack connection was cancelled.',
  invalid_state: 'Slack connection could not be verified. Start the connection again.',
  expired_state: 'Slack connection expired. Start the connection again.',
  configuration_error: 'Slack OAuth configuration is incomplete. Ask an administrator to review the app settings.',
  access_changed: 'Your workspace access changed before Slack could connect.',
  connect_error: 'Slack could not be connected. Check the app scopes and MCP access, then try again.',
}

function readSlackOAuthMessage(): string {
  const result = new URLSearchParams(window.location.search).get('slack_oauth')
  return result ? slackOAuthMessages[result] || 'Slack connection did not complete.' : ''
}

type RuntimeCheck = {
  status: CheckStatus
  detail: string
  checkedAt?: string
}

type HealthResponse = {
  status?: string
  dependencies?: Record<string, string>
  detail?: {
    status?: string
    dependencies?: Record<string, string>
  }
}

type UserProfile = { id: string; email: string; display_name: string }
type Workspace = { id: string; name: string; description: string; slug: string; role: string }
type Project = {
  id: string
  workspace_id: string
  name: string
  description: string
  status: 'active' | 'on_hold' | 'completed'
  created_at: string
  updated_at: string
}
type AuthResult = { user: UserProfile; workspaces: Workspace[] }
type Integration = {
  id: string
  provider: 'github' | 'slack'
  display_name: string
  endpoint: string
  status: 'not_configured' | 'connecting' | 'connected' | 'error' | 'unsupported'
  last_checked_at: string | null
  last_error: string | null
  capabilities: Array<{ name: string; title?: string; description?: string }>
  credential_configured: boolean
}
type ContextSource = {
  id: string
  provider: string
  tool_name: string
  title: string
  content_text: string
  source_url: string | null
  author: string | null
  occurred_at: string | null
  relevance_score: number
}
type ContextRequest = {
  request_id: string
  workspace_id: string
  prompt: string
  status: string
  created_at: string
  completed_at: string | null
  sources: ContextSource[]
  detail: string | null
  reasoning: {
    provider: string
    model: string
    answer: string
    citation_ids: string[]
    needs_more_context: boolean
    suggested_action: { title: string; description: string } | null
  } | null
}
type ContextRequestSummary = {
  request_id: string
  prompt: string
  status: string
  created_at: string
  completed_at: string | null
  source_count: number
}
type ActionProposal = {
  id: string
  request_id: string
  action_type: string
  target: { repository: string; action_reference: string; source_context_ids: string[] }
  payload: { title: string; body: string }
  editable_description: string
  payload_hash: string
  status: string
  approved_at: string | null
  execution: { status: string; external_id: string | null; external_url: string | null; error_code: string | null; completed_at: string | null } | null
}
type ReasoningProvider = { id: string; label: string; model: string }
type InstanceMeta = {
  reasoning_configured: boolean
  reasoning_model: string | null
  reasoning_provider: string | null
  reasoning_providers: ReasoningProvider[]
  slack_oauth_configured: boolean
  object_storage_configured: boolean
  max_upload_bytes: number
  database_upload_limit_bytes: number
  invitation_email_configured: boolean
}
type WorkspaceNotification = {
  id: string
  title: string
  body: string
  event_type: string
  actor_display_name: string | null
  href: string
  read_at: string | null
  created_at: string
}

async function apiRequest(path: string, init: RequestInit = {}) {
  return fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: 'include',
    headers: { Accept: 'application/json', ...init.headers },
  })
}

async function getHealth(path: string): Promise<HealthResponse> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(5000),
  })

  let payload: HealthResponse = {}
  try {
    payload = (await response.json()) as HealthResponse
  } catch {
    // Keep the status display useful when the service returns a non-JSON error.
  }

  if (!response.ok && response.status !== 503) {
    throw new Error(`Health endpoint returned ${response.status}`)
  }

  return { ...payload, status: response.ok ? payload.status : 'not_ready' }
}

type RuntimeRead = {
  api: RuntimeCheck
  database: RuntimeCheck
}

async function readRuntimeStatus(): Promise<RuntimeRead> {
  const checkedAt = new Date().toISOString()
  const [liveResult, readyResult] = await Promise.allSettled([
    getHealth('/health/live'),
    getHealth('/health/ready'),
  ])

  const api: RuntimeCheck =
    liveResult.status === 'fulfilled' && liveResult.value.status === 'live'
      ? { status: 'available', detail: 'FastAPI is responding', checkedAt }
      : {
          status: 'unavailable',
          detail: 'API did not respond. Start the backend and refresh.',
          checkedAt,
        }

  let database: RuntimeCheck
  if (readyResult.status === 'fulfilled' && readyResult.value.status === 'ready') {
    database = { status: 'available', detail: 'PostgreSQL is reachable', checkedAt }
  } else {
    const detail =
      readyResult.status === 'fulfilled'
        ? 'Database is not ready. Start PostgreSQL and apply migrations.'
        : 'Database check could not reach the API.'
    database = { status: 'unavailable', detail, checkedAt }
  }

  return { api, database }
}

function formatCheckTime(checkedAt?: string) {
  if (!checkedAt) return 'Not checked yet'
  return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(
    new Date(checkedAt),
  )
}

function formatNotificationTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function App() {
  const [activeView, setActiveView] = useState<AppView>(readAppView)
  const [apiCheck, setApiCheck] = useState<RuntimeCheck>({
    status: 'loading',
    detail: 'Checking the API process',
  })
  const [databaseCheck, setDatabaseCheck] = useState<RuntimeCheck>({
    status: 'loading',
    detail: 'Checking PostgreSQL readiness',
  })
  const [isRefreshing, setIsRefreshing] = useState(true)
  const [sessionState, setSessionState] = useState<'loading' | 'signed-out' | 'signed-in'>('loading')
  const [user, setUser] = useState<UserProfile | null>(null)
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState('')
  const selectedWorkspace = workspaces.find((workspace) => workspace.id === selectedWorkspaceId)
  const selectedWorkspaceRole = selectedWorkspace?.role
  const canManageWorkspace = selectedWorkspaceRole !== 'viewer'
  const canEditWorkspace = selectedWorkspaceRole === 'owner' || selectedWorkspaceRole === 'admin'
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login')
  const [authMessage, setAuthMessage] = useState('')
  const [authBusy, setAuthBusy] = useState(false)
  const [workspaceMessage, setWorkspaceMessage] = useState('')
  const [projects, setProjects] = useState<Project[]>([])
  const [projectBusy, setProjectBusy] = useState(false)
  const [projectMessage, setProjectMessage] = useState('')
  const [editingProjectId, setEditingProjectId] = useState('')
  const [expandedProjectId, setExpandedProjectId] = useState('')
  const [expandedOverviewProjectId, setExpandedOverviewProjectId] = useState('')
  const [integrations, setIntegrations] = useState<Integration[]>([])
  const [integrationBusy, setIntegrationBusy] = useState('')
  const [integrationMessage, setIntegrationMessage] = useState(readSlackOAuthMessage)
  const [slackOAuthConfigured, setSlackOAuthConfigured] = useState(false)
  const [notifications, setNotifications] = useState<WorkspaceNotification[]>([])
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [notificationMessage, setNotificationMessage] = useState('')
  const [contextRequests, setContextRequests] = useState<ContextRequestSummary[]>([])
  const [activeContextRequest, setActiveContextRequest] = useState<ContextRequest | null>(null)
  const [contextBusy, setContextBusy] = useState(false)
  const [contextMessage, setContextMessage] = useState('')
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([])
  const [activeProposal, setActiveProposal] = useState<ActionProposal | null>(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [actionMessage, setActionMessage] = useState('')
  const [reasoningConfigured, setReasoningConfigured] = useState(false)
  const [reasoningModel, setReasoningModel] = useState('')
  const [reasoningProviders, setReasoningProviders] = useState<ReasoningProvider[]>([])
  const [objectStorageConfigured, setObjectStorageConfigured] = useState(false)
  const [invitationEmailConfigured, setInvitationEmailConfigured] = useState(false)
  const [selectedReasoningProvider, setSelectedReasoningProvider] = useState('')
  const [issueTitle, setIssueTitle] = useState('')
  const [issueDescription, setIssueDescription] = useState('')
  const [issueRepository, setIssueRepository] = useState('')
  const [editingProposal, setEditingProposal] = useState(false)

  const applyStatus = useCallback(({ api, database }: RuntimeRead) => {
    setApiCheck(api)
    setDatabaseCheck(database)
    setIsRefreshing(false)
  }, [])

  const loadNotifications = useCallback(async (workspaceId: string) => {
    const response = await apiRequest(`/api/v1/workspaces/${workspaceId}/notifications?limit=50`)
    if (!response.ok) throw new Error('Could not load notifications.')
    return (await response.json()) as WorkspaceNotification[]
  }, [])

  const refreshHealth = useCallback(async () => {
    setIsRefreshing(true)
    applyStatus(await readRuntimeStatus())
  }, [applyStatus])

  useEffect(() => {
    let isCurrent = true
    void readRuntimeStatus().then((status) => {
      if (isCurrent) applyStatus(status)
    })
    return () => {
      isCurrent = false
    }
  }, [applyStatus])

  useEffect(() => {
    const syncRoute = () => setActiveView(readAppView())
    window.addEventListener('hashchange', syncRoute)
    window.addEventListener('popstate', syncRoute)
    return () => {
      window.removeEventListener('hashchange', syncRoute)
      window.removeEventListener('popstate', syncRoute)
    }
  }, [])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (!params.has('slack_oauth')) return
    params.delete('slack_oauth')
    const query = params.toString()
    window.history.replaceState({}, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
  }, [])

  useEffect(() => {
    let isCurrent = true
    void apiRequest('/api/v1/meta').then(async (response) => {
      if (!isCurrent || !response.ok) return
      const meta = (await response.json()) as InstanceMeta
      if (isCurrent) {
        setReasoningConfigured(meta.reasoning_configured)
        setReasoningModel(meta.reasoning_model || '')
        setReasoningProviders(meta.reasoning_providers || [])
        setSelectedReasoningProvider(meta.reasoning_provider || meta.reasoning_providers?.[0]?.id || '')
        setSlackOAuthConfigured(meta.slack_oauth_configured)
        setObjectStorageConfigured(meta.object_storage_configured)
        setInvitationEmailConfigured(meta.invitation_email_configured)
      }
    }).catch(() => undefined)
    return () => { isCurrent = false }
  }, [])

  const applyAuth = useCallback((result: AuthResult) => {
    setUser(result.user)
    setWorkspaces(result.workspaces)
    setSelectedWorkspaceId((current) => result.workspaces.some((workspace) => workspace.id === current)
      ? current
      : result.workspaces[0]?.id || '')
    setNotifications([])
    setNotificationsOpen(false)
    setSessionState('signed-in')
  }, [])

  useEffect(() => {
    let isCurrent = true
    void apiRequest('/api/v1/auth/me').then(async (response) => {
      if (!isCurrent) return
      if (response.ok) {
        applyAuth((await response.json()) as AuthResult)
      } else {
        setSessionState('signed-out')
      }
    }).catch(() => {
      if (isCurrent) setSessionState('signed-out')
    })
    return () => { isCurrent = false }
  }, [applyAuth])

  useEffect(() => {
    if (sessionState !== 'signed-in' || !selectedWorkspaceId) return
    let isCurrent = true
    void apiRequest(`/api/v1/workspaces/${selectedWorkspaceId}/projects`)
      .then(async (response) => {
        if (!isCurrent) return
        if (!response.ok) throw new Error('Could not load this workspace’s projects.')
        setProjects((await response.json()) as Project[])
      })
      .catch((error) => {
        if (isCurrent) setProjectMessage(error instanceof Error ? error.message : 'The API could not load projects.')
      })
    return () => { isCurrent = false }
  }, [sessionState, selectedWorkspaceId])

  useEffect(() => {
    if (sessionState !== 'signed-in' || !selectedWorkspaceId) return
    let current = true
    const refresh = () => {
      void loadNotifications(selectedWorkspaceId).then((rows) => {
        if (current) {
          setNotifications(rows)
          setNotificationMessage('')
        }
      }).catch((error) => {
        if (current) setNotificationMessage(error instanceof Error ? error.message : 'Could not load notifications.')
      })
    }
    refresh()
    const timer = window.setInterval(refresh, 15000)
    return () => { current = false; window.clearInterval(timer) }
  }, [loadNotifications, selectedWorkspaceId, sessionState])

  useEffect(() => {
    if (sessionState !== 'signed-in' || !selectedWorkspaceId) return
    let isCurrent = true
    void apiRequest(`/api/v1/workspaces/${selectedWorkspaceId}/integrations`)
      .then(async (response) => {
        if (!isCurrent) return
        if (response.ok) setIntegrations((await response.json()) as Integration[])
        else setIntegrationMessage('Could not load this workspace’s integrations.')
      })
      .catch(() => {
        if (isCurrent) setIntegrationMessage('The API could not load integrations.')
      })
    return () => { isCurrent = false }
  }, [sessionState, selectedWorkspaceId])

  useEffect(() => {
    if (sessionState !== 'signed-in' || !selectedWorkspaceId) return
    let isCurrent = true
    void apiRequest(`/api/v1/workspaces/${selectedWorkspaceId}/requests`)
      .then(async (response) => {
        if (!isCurrent) return
        if (!response.ok) throw new Error('Could not load recent context requests.')
        const rows = (await response.json()) as ContextRequestSummary[]
        if (isCurrent) setContextRequests(rows)
      })
      .catch((error) => {
        if (isCurrent) setContextMessage(error instanceof Error ? error.message : 'The API could not load context requests.')
      })
    return () => { isCurrent = false }
  }, [sessionState, selectedWorkspaceId])

  function switchWorkspace(workspaceId: string) {
    setSelectedWorkspaceId(workspaceId)
    setNotifications([])
    setNotificationMessage('')
    setProjects([])
    setProjectMessage('')
    setEditingProjectId('')
    setExpandedProjectId('')
    setIntegrations([])
    setIntegrationMessage('')
    setContextRequests([])
    setActiveContextRequest(null)
    setNotifications([])
    setNotificationsOpen(false)
    setSelectedSourceIds([])
    setActiveProposal(null)
    setContextMessage('')
    setActionMessage('')
    setIssueTitle('')
    setIssueDescription('')
    setIssueRepository('')
    setEditingProposal(false)
  }

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAuthMessage('')
    setAuthBusy(true)
    const values = new FormData(event.currentTarget)
    const payload: Record<string, string> = {
      email: String(values.get('email') || ''),
      password: String(values.get('password') || ''),
    }
    if (authMode === 'register') payload.display_name = String(values.get('display_name') || '')
    try {
      const response = await apiRequest(`/api/v1/auth/${authMode === 'register' ? 'register' : 'login'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const result = await response.json()
      if (!response.ok) {
        const detail = typeof result.detail === 'string' ? result.detail : 'Check the form and try again.'
        throw new Error(detail)
      }
      applyAuth(result as AuthResult)
    } catch (error) {
      setAuthMessage(error instanceof Error ? error.message : 'The API could not be reached.')
    } finally {
      setAuthBusy(false)
    }
  }

  async function signOut() {
    try {
      const response = await apiRequest('/api/v1/auth/logout', { method: 'POST' })
      if (!response.ok) throw new Error('Could not sign out. Try again.')
    } catch (error) {
      setWorkspaceMessage(error instanceof Error ? error.message : 'The API could not be reached.')
      return
    }
    setUser(null)
    setWorkspaces([])
    setSelectedWorkspaceId('')
    setProjects([])
    setEditingProjectId('')
    setExpandedProjectId('')
    setIntegrations([])
    setContextRequests([])
    setActiveContextRequest(null)
    setSelectedSourceIds([])
    setActiveProposal(null)
    setIssueTitle('')
    setIssueDescription('')
    setIssueRepository('')
    setEditingProposal(false)
    setSessionState('signed-out')
  }

  async function syncAfterInvitation(workspaceId: string) {
    const response = await apiRequest('/api/v1/auth/me')
    if (!response.ok) return
    const result = (await response.json()) as AuthResult
    setUser(result.user)
    setWorkspaces(result.workspaces)
    setSessionState('signed-in')
    switchWorkspace(workspaceId)
    const params = new URLSearchParams(window.location.search)
    params.delete('invite')
    const query = params.toString()
    window.history.replaceState(
      {},
      '',
      `${window.location.pathname}${query ? `?${query}` : ''}#team`,
    )
    setActiveView('team')
  }

  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setWorkspaceMessage('')
    const form = event.currentTarget
    const values = new FormData(form)
    const name = String(values.get('workspace_name') || '').trim()
    if (!name) return
    try {
      const response = await apiRequest('/api/v1/workspaces', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description: String(values.get('workspace_description') || '').trim() }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not create workspace.')
      setWorkspaces((current) => [...current, result as Workspace])
      switchWorkspace((result as Workspace).id)
      form.reset()
      setWorkspaceMessage('Workspace created.')
    } catch (error) {
      setWorkspaceMessage(error instanceof Error ? error.message : 'The API could not be reached.')
    }
  }

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedWorkspaceId) return
    const form = event.currentTarget
    const values = new FormData(form)
    setProjectBusy(true)
    setProjectMessage('Creating project…')
    try {
      const response = await apiRequest(`/api/v1/workspaces/${selectedWorkspaceId}/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: String(values.get('project_name') || ''),
          description: String(values.get('project_description') || ''),
        }),
      })
      const result = (await response.json()) as Project & { detail?: string }
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not create project.')
      setProjects((current) => [...current, result])
      form.reset()
      setProjectMessage('Project created in this workspace.')
    } catch (error) {
      setProjectMessage(error instanceof Error ? error.message : 'The API could not create the project.')
    } finally {
      setProjectBusy(false)
    }
  }

  async function updateProject(event: FormEvent<HTMLFormElement>, projectId: string) {
    event.preventDefault()
    if (!selectedWorkspaceId) return
    const values = new FormData(event.currentTarget)
    setProjectBusy(true)
    setProjectMessage('Saving project…')
    try {
      const response = await apiRequest(`/api/v1/workspaces/${selectedWorkspaceId}/projects/${projectId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: String(values.get('project_name') || ''),
          description: String(values.get('project_description') || ''),
          status: String(values.get('project_status') || 'active'),
        }),
      })
      const result = (await response.json()) as Project & { detail?: string }
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not update project.')
      setProjects((current) => current.map((project) => project.id === projectId ? result : project))
      setEditingProjectId('')
      setProjectMessage('Project details saved.')
    } catch (error) {
      setProjectMessage(error instanceof Error ? error.message : 'The API could not update the project.')
    } finally {
      setProjectBusy(false)
    }
  }

  async function connectIntegration(event: FormEvent<HTMLFormElement>, provider: 'github' | 'slack') {
    event.preventDefault()
    const form = event.currentTarget
    if (!selectedWorkspaceId) return
    setIntegrationMessage('')
    setIntegrationBusy(provider)
    const token = String(new FormData(event.currentTarget).get('access_token') || '').trim()
    try {
      const response = await apiRequest(
        `/api/v1/workspaces/${selectedWorkspaceId}/integrations/${provider}/connect`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(token ? { access_token: token } : {}),
        },
      )
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not verify this MCP server.')
      setIntegrations((current) => [...current.filter((item) => item.provider !== provider), result as Integration])
      form.reset()
      setIntegrationMessage(`${provider === 'github' ? 'GitHub' : 'Slack'} MCP verified. ${result.capabilities.length} tools discovered.`)
    } catch (error) {
      setIntegrationMessage(error instanceof Error ? error.message : 'The API could not be reached.')
      const response = await apiRequest(`/api/v1/workspaces/${selectedWorkspaceId}/integrations`).catch(() => null)
      if (response?.ok) setIntegrations((await response.json()) as Integration[])
    } finally {
      setIntegrationBusy('')
    }
  }

  async function startSlackOAuth() {
    if (!selectedWorkspaceId) return
    setIntegrationBusy('slack')
    setIntegrationMessage('Opening Slack authorization…')
    try {
      const response = await apiRequest(`/api/v1/workspaces/${selectedWorkspaceId}/integrations/slack/oauth/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      const result = (await response.json()) as { authorization_url?: string; detail?: string }
      if (!response.ok || !result.authorization_url) {
        throw new Error(result.detail || 'Could not start Slack authorization.')
      }
      window.location.assign(result.authorization_url)
    } catch (error) {
      setIntegrationMessage(error instanceof Error ? error.message : 'Could not start Slack authorization.')
      setIntegrationBusy('')
    }
  }

  async function disconnectIntegration(provider: 'github' | 'slack') {
    if (!selectedWorkspaceId) return
    setIntegrationBusy(`disconnect-${provider}`)
    setIntegrationMessage('Disconnecting integration…')
    try {
      const response = await apiRequest(`/api/v1/workspaces/${selectedWorkspaceId}/integrations/${provider}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const result = (await response.json()) as { detail?: string }
        throw new Error(result.detail || 'Could not disconnect this integration.')
      }
      setIntegrations((current) => current.filter((item) => item.provider !== provider))
      setIntegrationMessage(provider === 'slack'
        ? 'Slack disconnected and its token was revoked.'
        : 'GitHub disconnected from SAHAKARYA. Revoke the token in GitHub if it should no longer be valid.')
    } catch (error) {
      setIntegrationMessage(error instanceof Error ? error.message : 'Could not disconnect this integration.')
    } finally {
      setIntegrationBusy('')
    }
  }

  async function openNotification(notification: WorkspaceNotification) {
    if (!selectedWorkspaceId) return
    if (!notification.read_at) {
      const response = await apiRequest(
        `/api/v1/workspaces/${selectedWorkspaceId}/notifications/${notification.id}/read`,
        { method: 'PATCH' },
      )
      if (response.ok) {
        const updated = (await response.json()) as WorkspaceNotification
        setNotifications((current) => current.map((item) => item.id === updated.id ? updated : item))
      }
    }
    setNotificationsOpen(false)
    const targetView = notification.href.slice(1) as AppView
    if (notification.href.startsWith('#') && appViews.includes(targetView)) {
      window.history.pushState(null, '', notification.href)
      setActiveView(targetView)
    }
  }

  async function markAllNotificationsRead() {
    if (!selectedWorkspaceId) return
    const response = await apiRequest(`/api/v1/workspaces/${selectedWorkspaceId}/notifications/read-all`, {
      method: 'POST',
    })
    if (!response.ok) {
      setNotificationMessage('Could not mark notifications as read.')
      return
    }
    const readAt = new Date().toISOString()
    setNotifications((current) => current.map((item) => ({ ...item, read_at: item.read_at || readAt })))
  }

  async function createContextRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedWorkspaceId) return
    const form = event.currentTarget
    const prompt = String(new FormData(form).get('context_prompt') || '').trim()
    if (!prompt) return
    setContextBusy(true)
    setContextMessage('Searching verified MCP sources…')
    try {
      const response = await apiRequest(`/api/v1/workspaces/${selectedWorkspaceId}/requests`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, provider: selectedReasoningProvider || undefined }),
      })
      const result = (await response.json()) as ContextRequest & { detail?: string }
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not search context.')
      setActiveContextRequest(result)
      setSelectedSourceIds(result.sources.slice(0, 10).map((source) => source.id))
      setActiveProposal(null)
      setActionMessage('')
      setIssueTitle(result.reasoning?.suggested_action?.title || '')
      setIssueDescription(result.reasoning?.suggested_action?.description || '')
      setIssueRepository('')
      setEditingProposal(false)
      setContextRequests((current) => [
        { request_id: result.request_id, prompt: result.prompt, status: result.status, created_at: result.created_at, completed_at: result.completed_at, source_count: result.sources.length },
        ...current.filter((item) => item.request_id !== result.request_id),
      ].slice(0, 20))
      setContextMessage(result.detail || `Retrieved ${result.sources.length} attributed source${result.sources.length === 1 ? '' : 's'}.`)
      form.reset()
    } catch (error) {
      setContextMessage(error instanceof Error ? error.message : 'The API could not search context.')
    } finally {
      setContextBusy(false)
    }
  }

  async function openContextRequest(requestId: string) {
    if (!selectedWorkspaceId) return
    setContextBusy(true)
    setContextMessage('Loading the saved evidence…')
    try {
      const [response, proposalsResponse] = await Promise.all([
        apiRequest(`/api/v1/workspaces/${selectedWorkspaceId}/requests/${requestId}`),
        apiRequest(`/api/v1/workspaces/${selectedWorkspaceId}/requests/${requestId}/proposals`),
      ])
      const result = (await response.json()) as ContextRequest
      if (!response.ok) throw new Error('Could not load this context request.')
      const proposals = proposalsResponse.ok ? (await proposalsResponse.json()) as ActionProposal[] : []
      const latestProposal = proposals.find((proposal) => proposal.status !== 'cancelled') || null
      setActiveContextRequest(result)
      setSelectedSourceIds(latestProposal?.target.source_context_ids || result.sources.slice(0, 10).map((source) => source.id))
      setActiveProposal(latestProposal)
      setActionMessage('')
      setIssueTitle(latestProposal?.payload.title || result.reasoning?.suggested_action?.title || '')
      setIssueDescription(latestProposal?.editable_description || result.reasoning?.suggested_action?.description || '')
      setIssueRepository(latestProposal?.target.repository || '')
      setEditingProposal(false)
      setContextMessage(result.detail || `Loaded ${result.sources.length} attributed source${result.sources.length === 1 ? '' : 's'}.`)
    } catch (error) {
      setContextMessage(error instanceof Error ? error.message : 'The API could not load this request.')
    } finally {
      setContextBusy(false)
    }
  }

  async function createIssueProposal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedWorkspaceId || !activeContextRequest || selectedSourceIds.length === 0) return
    const form = event.currentTarget
    const values = new FormData(form)
    setActionBusy(true)
    setActionMessage('Preparing the reviewable GitHub issue…')
    try {
      const response = await apiRequest(
        `/api/v1/workspaces/${selectedWorkspaceId}/requests/${activeContextRequest.request_id}/proposals`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            repository: String(values.get('repository') || ''),
            title: String(values.get('issue_title') || ''),
            description: String(values.get('issue_description') || ''),
            source_context_ids: selectedSourceIds,
          }),
        },
      )
      const result = (await response.json()) as ActionProposal & { detail?: string }
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not prepare this issue.')
      setActiveProposal(result)
      setEditingProposal(false)
      setActionMessage('Draft prepared. Review the exact destination and content below before approving it.')
      form.reset()
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : 'The API could not prepare the action.')
    } finally {
      setActionBusy(false)
    }
  }

  async function updateIssueProposal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedWorkspaceId || !activeProposal) return
    const values = new FormData(event.currentTarget)
    setActionBusy(true)
    setActionMessage('Updating the draft and its approval hash…')
    try {
      const response = await apiRequest(
        `/api/v1/workspaces/${selectedWorkspaceId}/proposals/${activeProposal.id}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            repository: String(values.get('repository') || ''),
            title: String(values.get('issue_title') || ''),
            description: String(values.get('issue_description') || ''),
          }),
        },
      )
      const result = (await response.json()) as ActionProposal & { detail?: string }
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not update this draft.')
      setActiveProposal(result)
      setIssueRepository(result.target.repository)
      setIssueTitle(result.payload.title)
      setIssueDescription(result.editable_description)
      setEditingProposal(false)
      setActionMessage('Draft updated. Its approval hash changed; review it again before approving.')
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : 'The API could not update this draft.')
    } finally {
      setActionBusy(false)
    }
  }

  async function cancelIssueProposal() {
    if (!selectedWorkspaceId || !activeProposal) return
    setActionBusy(true)
    setActionMessage('Discarding this unapproved draft…')
    try {
      const response = await apiRequest(
        `/api/v1/workspaces/${selectedWorkspaceId}/proposals/${activeProposal.id}/cancel`,
        { method: 'POST' },
      )
      const result = (await response.json()) as ActionProposal & { detail?: string }
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not discard this draft.')
      setIssueRepository(result.target.repository)
      setIssueTitle(result.payload.title)
      setIssueDescription(result.editable_description)
      setActiveProposal(null)
      setEditingProposal(false)
      setActionMessage('Draft discarded. The form is ready for a replacement.')
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : 'The API could not discard this draft.')
    } finally {
      setActionBusy(false)
    }
  }

  async function approveIssueProposal() {
    if (!selectedWorkspaceId || !activeProposal) return
    setActionBusy(true)
    setActionMessage('Binding your approval to this exact payload…')
    try {
      const response = await apiRequest(
        `/api/v1/workspaces/${selectedWorkspaceId}/proposals/${activeProposal.id}/approve`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ payload_hash: activeProposal.payload_hash }),
        },
      )
      const result = (await response.json()) as ActionProposal & { detail?: string }
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not approve this payload.')
      setActiveProposal(result)
      setActionMessage('Approved. GitHub will only receive the content shown in this reviewed payload.')
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : 'The API could not approve this action.')
    } finally {
      setActionBusy(false)
    }
  }

  async function executeIssueProposal() {
    if (!selectedWorkspaceId || !activeProposal) return
    setActionBusy(true)
    setActionMessage('Checking GitHub for an existing action reference, then creating the approved issue…')
    try {
      const response = await apiRequest(
        `/api/v1/workspaces/${selectedWorkspaceId}/proposals/${activeProposal.id}/execute`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ payload_hash: activeProposal.payload_hash }),
        },
      )
      const result = (await response.json()) as ActionProposal & { detail?: string }
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not execute this action.')
      setActiveProposal(result)
      if (result.execution?.status === 'succeeded') {
        setActionMessage(result.execution.external_url ? 'GitHub confirmed the issue.' : 'GitHub confirmed the action.')
      } else if (result.execution?.status === 'unknown') {
        setActionMessage('GitHub’s response was uncertain. A retry will only search for the unique reference; it will not create a second issue.')
      } else {
        setActionMessage(`GitHub did not create the issue (${result.execution?.error_code || result.execution?.status || 'unknown status'}). You can retry after checking the connection.`)
      }
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : 'The API could not execute this action.')
    } finally {
      setActionBusy(false)
    }
  }

  const overallState =
    apiCheck.status === 'available' && databaseCheck.status === 'available'
      ? 'All systems ready'
      : apiCheck.status === 'available'
        ? 'API online · database pending'
        : 'Waiting for local services'

  if (sessionState === 'loading') {
    return <div className="auth-shell"><div className="auth-card"><div className="brand-name">SAHAKARYA</div><p>Restoring your session…</p></div></div>
  }

  if (sessionState === 'signed-out') {
    return (
      <div className="auth-shell">
        <form className="auth-card" onSubmit={(event) => void submitAuth(event)}>
          <div className="auth-brand"><span className="brand-name">SAHAKARYA</span><span className="track-tag">LIMITLESS · wHACKiest 2026</span></div>
          <div className="card-kicker">YOUR TEAM'S CONTEXT LAYER</div>
          <h1>{authMode === 'login' ? 'Welcome back.' : 'Start your workspace.'}</h1>
          <p className="auth-intro">Connect the conversations, code, tasks, and decisions your team already has.</p>
          {readInvitationToken() && <p className="auth-invitation" role="status">Invitation link detected. Sign in or create an account with the invited email, then accept it in Team.</p>}
          {authMode === 'register' && <label>Display name<input name="display_name" type="text" autoComplete="name" minLength={2} maxLength={160} required /></label>}
          <label>Email<input name="email" type="email" autoComplete="email" required /></label>
          <label>Password<input name="password" type="password" autoComplete={authMode === 'login' ? 'current-password' : 'new-password'} minLength={authMode === 'register' ? 12 : 1} required /></label>
          {authMessage && <p className="auth-error" role="alert">{authMessage}</p>}
          <button className="primary-button" type="submit" disabled={authBusy}>{authBusy ? 'Working…' : authMode === 'login' ? 'Sign in' : 'Create account'}</button>
          <button className="text-button" type="button" onClick={() => { setAuthMode(authMode === 'login' ? 'register' : 'login'); setAuthMessage('') }}>
            {authMode === 'login' ? 'New to SAHAKARYA? Create an account' : 'Already have an account? Sign in'}
          </button>
          <div className="auth-runtime"><span className={`small-dot ${apiCheck.status}`} /> API {apiCheck.status === 'available' ? 'available' : 'unavailable'} · {apiCheck.detail}</div>
        </form>
      </div>
    )
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#overview" onClick={() => setActiveView('overview')} aria-label="SAHAKARYA overview">
          <span className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <span className="brand-name">SAHAKARYA</span>
        </a>

        <details className="topnav-workspace" open={['workspace', 'projects', 'team', 'activity'].includes(activeView) || undefined}>
          <summary className={['workspace', 'projects', 'team', 'activity'].includes(activeView) ? 'active' : ''}>Workspace</summary>
          <nav className="topnav-workspace-menu" aria-label="Workspace navigation">
            {([
              ['workspace', 'Overview'], ['projects', 'Projects'], ['team', 'Team members'], ['activity', 'Activity'],
            ] as const).map(([view, label]) => (
              <button key={view} type="button" className={activeView === view ? 'active' : ''}
                aria-current={activeView === view ? 'page' : undefined}
                onClick={() => { window.history.pushState(null, '', `#${view}`); setActiveView(view) }}>{label}</button>
            ))}
          </nav>
        </details>

        <nav className="topnav" aria-label="Primary navigation">
          {([
            ['overview', 'Overview'], ['knowledge', 'Knowledge'], ['decisions', 'Decisions'],
            ['messages', 'Messages'], ['assistant', 'AI Assistant'], ['integrations', 'Integrations'],
            ['workflow', 'Workflow'],
          ] as const).map(([view, label]) => (
            <button key={view} type="button" className={activeView === view ? 'active' : ''}
              aria-current={activeView === view ? 'page' : undefined}
              onClick={() => { window.history.pushState(null, '', `#${view}`); setActiveView(view) }}>{label}</button>
          ))}
        </nav>

        <label className="global-workspace-select" htmlFor="global-workspace-select">
          <span>Active workspace</span>
          <select id="global-workspace-select" value={selectedWorkspaceId} onChange={(event) => switchWorkspace(event.target.value)}>
            {workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
          </select>
        </label>

        <div className="user-menu">
          <span className="user-greeting">{user?.display_name}</span>
          <button
            className={`notification-trigger ${notificationsOpen ? 'active' : ''}`}
            type="button"
            aria-expanded={notificationsOpen}
            aria-label={`Notifications${notifications.some((item) => !item.read_at) ? `, ${notifications.filter((item) => !item.read_at).length} unread` : ''}`}
            onClick={() => {
              const nextOpen = !notificationsOpen
              setNotificationsOpen(nextOpen)
              if (nextOpen && selectedWorkspaceId) {
                void loadNotifications(selectedWorkspaceId).then(setNotifications).catch(() => undefined)
              }
            }}
          >
            Notifications
            {notifications.some((item) => !item.read_at) && <span>{notifications.filter((item) => !item.read_at).length}</span>}
          </button>
          <button className="signout-button" type="button" onClick={() => void signOut()}>Sign out</button>
        </div>
        {notificationsOpen && <section className="notification-panel" aria-label="Notifications">
          <div className="notification-panel-heading">
            <div><div className="card-kicker">TEAM UPDATES</div><h2>Notifications</h2></div>
            <button type="button" onClick={() => void markAllNotificationsRead()} disabled={!notifications.some((item) => !item.read_at)}>Mark all read</button>
          </div>
          {notificationMessage && <p className="notification-error" role="status">{notificationMessage}</p>}
          {notifications.length === 0 ? <p className="notification-empty">You’re all caught up.</p> : <ul className="notification-list">
            {notifications.map((notification) => <li key={notification.id}>
              <button type="button" className={!notification.read_at ? 'unread' : ''} onClick={() => void openNotification(notification)}>
                <span className="notification-title">{notification.title}</span>
                {notification.body && <span className="notification-body">{notification.body}</span>}
                <span className="notification-meta">{notification.actor_display_name || 'SAHAKARYA'} · {formatNotificationTime(notification.created_at)}</span>
              </button>
            </li>)}
          </ul>}
        </section>}
      </header>

      <main id="top" data-view={activeView}>
        <section className="hero-grid" id="overview" aria-labelledby="hero-title">
          <div className="hero-copy">
            <div className="eyebrow"><span className="eyebrow-line" /> LIMITLESS · wHACKiest 2026</div>
            <h1 id="hero-title">Connecting context.<br /><em>Enabling action.</em></h1>
            <p className="hero-description">
              Shared project knowledge, gathered from the tools your team already uses, and carried
              forward into work you can act on.
            </p>
            <div className="hero-meta">
              <span className="track-tag">MAKE MCP MATTER</span>
              <span className="meta-divider" />
              <span>Context retrieval · Phase 4</span>
            </div>
          </div>

          <aside className="runtime-card" aria-labelledby="runtime-title">
            <div className="card-header">
              <div>
                <div className="card-kicker">LOCAL ENVIRONMENT</div>
                <h2 id="runtime-title">Runtime health</h2>
              </div>
              <button className="refresh-button" type="button" onClick={() => void refreshHealth()} disabled={isRefreshing}>
                <span className={isRefreshing ? 'refresh-icon spinning' : 'refresh-icon'} aria-hidden="true">↻</span>
                {isRefreshing ? 'Checking' : 'Refresh'}
              </button>
            </div>

            <HealthRow title="API process" check={apiCheck} />
            <HealthRow title="PostgreSQL" check={databaseCheck} />

            <div className="runtime-footer">
              <span>API endpoint</span>
              <code>{apiBaseUrl}</code>
            </div>
          </aside>
        </section>

        <section className="workspace-panel" id="workspace" aria-labelledby="workspace-title">
          <div className="workspace-heading">
            <div>
              <div className="card-kicker">YOUR TEAM SPACE</div>
              <h2 id="workspace-title">Workspace overview</h2>
            </div>
          </div>
          {selectedWorkspace && <WorkspaceOverview
            key={selectedWorkspaceId}
            workspace={selectedWorkspace}
            projects={projects}
            canEdit={canEditWorkspace}
            onWorkspaceUpdated={(updated) => setWorkspaces((current) => current.map((workspace) => workspace.id === updated.id ? updated : workspace))}
          />}
          <div className="workspace-create-row">
            <form className="workspace-create-form" onSubmit={(event) => void createWorkspace(event)}>
              <label htmlFor="workspace-name">Create another workspace</label>
              <div><input id="workspace-name" name="workspace_name" placeholder="e.g. Product team" minLength={2} maxLength={160} required /><button type="submit">Create workspace</button></div>
              <label htmlFor="workspace-description-new">Description (optional)<textarea id="workspace-description-new" name="workspace_description" maxLength={2000} rows={2} placeholder="What will this workspace focus on?" /></label>
            </form>
            <p className="workspace-note">Signed in as <strong>{user?.email}</strong>. Each space is isolated by membership.</p>
          </div>
          {workspaceMessage && <p className="workspace-message" role="status">{workspaceMessage}</p>}
        </section>

        <section className="projects-section" id="projects" aria-labelledby="projects-title">
          <div className="section-heading">
            <div>
              <div className="card-kicker">NATIVE PROJECTS</div>
              <h2 id="projects-title">Work starts in your workspace</h2>
            </div>
            <p>Create and manage SAHAKARYA projects here. Projects are stored natively and do not require an external integration.</p>
          </div>
          {canManageWorkspace && <form className="project-create-form" onSubmit={(event) => void createProject(event)}>
            <label>Project name<input name="project_name" placeholder="e.g. Authentication reliability" minLength={2} maxLength={160} required /></label>
            <label>Description<textarea name="project_description" placeholder="What is this project working toward?" maxLength={5000} rows={2} /></label>
            <button type="submit" disabled={projectBusy}>{projectBusy ? 'Saving…' : 'Create project'}</button>
          </form>}
          {projectMessage && <p className="project-message" role="status">{projectMessage}</p>}
          {projects.length === 0 ? (
            <div className="project-empty-state"><strong>No projects in this workspace yet.</strong><span>Create one above to start organizing native project work.</span></div>
          ) : (
            <div className="project-grid">
              {projects.map((project) => (
                <article className="project-card" key={project.id}>
                  {editingProjectId === project.id ? (
                    <form className="project-edit-form" onSubmit={(event) => void updateProject(event, project.id)}>
                      <label>Project name<input name="project_name" defaultValue={project.name} minLength={2} maxLength={160} required /></label>
                      <label>Description<textarea name="project_description" defaultValue={project.description} maxLength={5000} rows={3} /></label>
                      <label>Status<select name="project_status" defaultValue={project.status}><option value="active">Active</option><option value="on_hold">On hold</option><option value="completed">Completed</option></select></label>
                      <div className="project-card-actions"><button type="submit" disabled={projectBusy}>{projectBusy ? 'Saving…' : 'Save changes'}</button><button type="button" className="secondary-action" onClick={() => setEditingProjectId('')} disabled={projectBusy}>Cancel</button></div>
                    </form>
                  ) : (
                    <>
                      <div className="project-card-top"><span className="project-status">{project.status.replace('_', ' ')}</span><time>{formatCheckTime(project.created_at)}</time></div>
                      <h3>{project.name}</h3>
                      <p>{project.description || 'No project description yet.'}</p>
                      {canManageWorkspace && <button className="project-edit-button" type="button" onClick={() => setEditingProjectId(project.id)}>Edit project</button>}
                      <button className="project-overview-toggle" type="button" aria-expanded={expandedOverviewProjectId === project.id} onClick={() => setExpandedOverviewProjectId((current) => current === project.id ? '' : project.id)}>
                        {expandedOverviewProjectId === project.id ? 'Hide overview' : 'Project overview'}
                      </button>
                      {expandedOverviewProjectId === project.id && <ProjectOverview workspaceId={selectedWorkspaceId} projectId={project.id} />}
                      <button className="project-tasks-toggle" type="button" aria-expanded={expandedProjectId === project.id} onClick={() => setExpandedProjectId((current) => current === project.id ? '' : project.id)}>
                        {expandedProjectId === project.id ? 'Hide tasks' : 'View tasks'}
                      </button>
                      {expandedProjectId === project.id && <ProjectTasks workspaceId={selectedWorkspaceId} projectId={project.id} canEdit={canManageWorkspace} providers={reasoningProviders} defaultProvider={selectedReasoningProvider} />}
                    </>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>

        <KnowledgeHub
          workspaceId={selectedWorkspaceId}
          projects={projects}
          userId={user?.id || ''}
          isWorkspaceOwner={selectedWorkspaceRole === 'owner' || selectedWorkspaceRole === 'admin'}
          canWrite={canManageWorkspace}
          objectStorageConfigured={objectStorageConfigured}
        />

        <DecisionBoard
          workspaceId={selectedWorkspaceId}
          projects={projects}
          userId={user?.id || ''}
          canWrite={canManageWorkspace}
        />

        <ActivityTimeline workspaceId={selectedWorkspaceId} projects={projects} />

        <WorkspaceMembers
          workspaceId={selectedWorkspaceId}
          currentUserId={user?.id || ''}
          role={selectedWorkspaceRole || 'viewer'}
          invitationToken={readInvitationToken()}
          invitationEmailConfigured={invitationEmailConfigured}
          onAccepted={(joinedWorkspaceId) => void syncAfterInvitation(joinedWorkspaceId)}
        />

        <ChatPanel key={selectedWorkspaceId} workspaceId={selectedWorkspaceId} userId={user?.id || ''} canWrite={canManageWorkspace} />

        <section className="integrations-section" id="integrations" aria-labelledby="integrations-title">
          <div className="section-heading integrations-heading">
            <div>
              <div className="card-kicker">CONNECTED CONTEXT SOURCES</div>
              <h2 id="integrations-title">Connect your team’s tools</h2>
            </div>
            <p>GitHub and Slack use MCP capability discovery. Telegram uses the official Bot API and a verified webhook.</p>
          </div>
          <div className="integration-grid">
            {(['github', 'slack'] as const).map((provider) => {
              const integration = integrations.find((item) => item.provider === provider)
              const label = provider === 'github' ? 'GitHub' : 'Slack'
              return (
                <article className="integration-card" key={provider}>
                  <div className="integration-card-top">
                    <div className={`integration-mark ${provider}`}>{provider === 'github' ? 'GH' : 'S'}</div>
                    <div><div className="card-kicker">MCP SERVER</div><h3>{label}</h3></div>
                    <span className={`integration-status ${provider === 'slack' && !slackOAuthConfigured && integration?.status !== 'connected' ? 'unsupported' : integration?.status || 'not_configured'}`}>
                      <span className="small-dot" />
                      {integration?.status === 'connected' ? 'Verified' : integration?.status === 'error' ? 'Needs attention' : provider === 'slack' && !slackOAuthConfigured ? 'Setup incomplete' : 'Not connected'}
                    </span>
                  </div>
                  <p className="integration-endpoint">{integration?.endpoint || (provider === 'github' ? 'api.githubcopilot.com/mcp' : 'mcp.slack.com/mcp')}</p>
                  {provider === 'slack' && integration?.status === 'connected' ? (
                    <div className="tool-discovery">
                      <strong>{integration.capabilities.length} tools discovered</strong>
                      <div className="tool-chip-list">
                        {integration.capabilities.slice(0, 8).map((tool) => <span className="tool-chip" key={tool.name}>{tool.name}</span>)}
                        {integration.capabilities.length > 8 && <span className="tool-chip">+{integration.capabilities.length - 8} more</span>}
                      </div>
                    </div>
                  ) : provider === 'slack' && !slackOAuthConfigured ? (
                    <div className="integration-unavailable" role="note">Add a Slack client ID, client secret, and HTTPS callback URL to the API configuration to enable OAuth.</div>
                  ) : provider === 'slack' ? (
                    <>
                      <p className="integration-help">Authorize your Slack user account. SAHAKARYA verifies the official MCP server and lists its available tools.</p>
                      <button type="button" onClick={() => void startSlackOAuth()} disabled={integrationBusy === 'slack' || !canManageWorkspace}>
                        {integrationBusy === 'slack' ? 'Opening Slack…' : integration?.credential_configured ? 'Reconnect Slack' : 'Connect Slack'}
                      </button>
                    </>
                  ) : integration?.status === 'connected' ? (
                    <div className="tool-discovery">
                      <strong>{integration.capabilities.length} tools discovered</strong>
                      <div className="tool-chip-list">
                        {integration.capabilities.slice(0, 8).map((tool) => <span className="tool-chip" key={tool.name}>{tool.name}</span>)}
                        {integration.capabilities.length > 8 && <span className="tool-chip">+{integration.capabilities.length - 8} more</span>}
                      </div>
                    </div>
                  ) : (
                    <p className="integration-help">Use a fine-grained GitHub personal access token scoped to the repository you plan to use.</p>
                  )}
                  {integration?.status === 'connected' && <button type="button" className="integration-disconnect" onClick={() => void disconnectIntegration(provider)} disabled={integrationBusy === `disconnect-${provider}` || !canManageWorkspace}>
                    {integrationBusy === `disconnect-${provider}` ? 'Disconnecting…' : 'Disconnect'}
                  </button>}
                  {provider === 'github' && <form className="integration-connect-form" onSubmit={(event) => void connectIntegration(event, 'github')}>
                    <label htmlFor={`${provider}-token`}>{integration?.credential_configured ? 'Replace token (optional)' : 'Access token'}</label>
                    <div>
                      <input id={`${provider}-token`} name="access_token" type="password" autoComplete="new-password" placeholder={integration?.credential_configured ? 'Saved encrypted · leave blank to recheck' : 'Paste access token'} minLength={10} maxLength={4096} />
                      <button type="submit" disabled={integrationBusy === provider}>{integrationBusy === provider ? 'Verifying…' : integration?.credential_configured ? 'Recheck' : 'Connect'}</button>
                    </div>
                  </form>}
                  {integration?.last_error && <p className="integration-error">{integration.last_error}</p>}
                  {integration?.last_checked_at && <p className="integration-checked">Last checked {formatCheckTime(integration.last_checked_at)}</p>}
                </article>
              )
            })}
          </div>
          <div className="integration-grid telegram-integration-grid">
            <TelegramIntegrationPanel workspaceId={selectedWorkspaceId} projects={projects} canWrite={canManageWorkspace} />
          </div>
          {integrationMessage && <p className="integration-message" role="status">{integrationMessage}</p>}
        </section>

        <section className="context-workbench" id="context" aria-labelledby="context-title">
          <AssistantPanel workspaceId={selectedWorkspaceId} projects={projects} providers={reasoningProviders} defaultProvider={selectedReasoningProvider} canWrite={canManageWorkspace} />
          <WorkspaceSearch workspaceId={selectedWorkspaceId} projects={projects} providers={reasoningProviders} defaultProvider={selectedReasoningProvider} />
          <div className="section-heading">
            <div>
              <div className="card-kicker">SEARCH TEAM CONTEXT</div>
              <h2 id="context-title">Ask across connected sources</h2>
            </div>
            <p>SAHAKARYA searches only discovered, allowlisted read tools and keeps each result linked to its source.</p>
          </div>
          <form className="context-query-form" onSubmit={(event) => void createContextRequest(event)}>
            <label htmlFor="context-prompt">What should the team pick up, decide, or follow through on?</label>
            <div className="context-query-row">
              <input id="context-prompt" name="context_prompt" placeholder="e.g. What did we decide about the onboarding flow?" minLength={3} maxLength={2000} required />
              <button type="submit" disabled={contextBusy}>{contextBusy ? 'Searching…' : 'Gather context'}</button>
            </div>
          </form>
          {contextMessage && <p className="context-message" role="status">{contextMessage}</p>}
          <div className="context-layout">
            <aside className="request-history" aria-label="Recent context requests">
              <div className="request-history-heading"><strong>Recent requests</strong><span>{contextRequests.length}</span></div>
              {contextRequests.length === 0 ? (
                <p className="empty-context">Your workspace searches will appear here.</p>
              ) : contextRequests.map((item) => (
                <button className={`request-history-item ${activeContextRequest?.request_id === item.request_id ? 'selected' : ''}`} key={item.request_id} type="button" onClick={() => void openContextRequest(item.request_id)} disabled={contextBusy}>
                  <span>{item.prompt}</span>
                  <small>{item.source_count} source{item.source_count === 1 ? '' : 's'} · {formatCheckTime(item.created_at)}</small>
                </button>
              ))}
            </aside>
            <div className="context-results" aria-live="polite">
              {!activeContextRequest ? (
                <div className="context-empty-state"><span className="capability-icon icon-context" aria-hidden="true">C</span><h3>Evidence first</h3><p>Connect GitHub or finish Slack OAuth setup to search external context. Grounded answers also need an AI provider with available quota.</p></div>
              ) : (
                <>
                  <div className="context-result-heading"><div><div className="card-kicker">RETRIEVED EVIDENCE · {activeContextRequest.status.replaceAll('_', ' ')}</div><h3>{activeContextRequest.prompt}</h3></div><span className="source-count">{activeContextRequest.sources.length} sources</span></div>
                  {activeContextRequest.detail && activeContextRequest.sources.length === 0 && <p className="context-empty-detail">{activeContextRequest.detail}</p>}
                  {activeContextRequest.reasoning ? (
                    <article className="reasoning-answer">
                      <div className="card-kicker">GROUNDED SYNTHESIS · {activeContextRequest.reasoning.provider} / {activeContextRequest.reasoning.model}</div>
                      <p>{activeContextRequest.reasoning.answer}</p>
                      {activeContextRequest.reasoning.needs_more_context && <strong className="reasoning-uncertainty">More context is needed before making a reliable decision.</strong>}
                      {activeContextRequest.reasoning.citation_ids.length > 0 && <div className="answer-citations"><strong>Sources</strong>{activeContextRequest.reasoning.citation_ids.map((id) => {
                        const cited = activeContextRequest.sources.find((source) => source.id === id)
                        return cited ? <a key={id} href={cited.source_url || `#evidence-${id}`} target={cited.source_url ? '_blank' : undefined} rel={cited.source_url ? 'noreferrer' : undefined}>{cited.title}{cited.source_url ? ' ↗' : ''}</a> : null
                      })}</div>}
                      {activeContextRequest.reasoning.suggested_action && <div className="reasoning-suggestion"><span>Suggested follow-up</span><strong>{activeContextRequest.reasoning.suggested_action.title}</strong><p>{activeContextRequest.reasoning.suggested_action.description}</p></div>}
                    </article>
                  ) : activeContextRequest.sources.length > 0 ? (
                    <p className="context-empty-detail">{activeContextRequest.detail || (reasoningConfigured ? 'No grounded answer was validated for this search.' : 'Evidence was retrieved; configure OPENAI_API_KEY and OPENAI_MODEL to enable grounded synthesis.')}</p>
                  ) : null}
                  <div className="evidence-list">
                    {activeContextRequest.sources.map((source, index) => (
                      <article className="evidence-card" key={source.id} id={`evidence-${source.id}`}>
                        <div className="evidence-meta"><span className={`evidence-provider ${source.provider}`}>{source.provider}</span><span>{source.tool_name}</span><span>Match {Math.round(source.relevance_score * 100)}%</span><span>#{index + 1}</span><label className="cite-source"><input type="checkbox" checked={selectedSourceIds.includes(source.id)} disabled={!selectedSourceIds.includes(source.id) && selectedSourceIds.length >= 10} onChange={(event) => setSelectedSourceIds((current) => event.target.checked ? [...current, source.id].slice(0, 10) : current.filter((id) => id !== source.id))} /> Cite in issue</label></div>
                        <h4>{source.source_url ? <a href={source.source_url} target="_blank" rel="noreferrer">{source.title}<span aria-hidden="true"> ↗</span></a> : source.title}</h4>
                        <p>{source.content_text}</p>
                        <div className="evidence-footer"><span>{source.author ? `By ${source.author}` : 'Author unavailable'}</span><time>{source.occurred_at ? formatCheckTime(source.occurred_at) : 'Date unavailable'}</time></div>
                      </article>
                    ))}
                  </div>
                  {activeContextRequest.sources.length > 0 && !activeProposal && integrations.some((item) => item.provider === 'github' && item.status === 'connected') && (
                    <div className="action-compose">
                      <div className="card-kicker">TURN EVIDENCE INTO A REVIEWABLE ACTION</div>
                      <h3>Draft a GitHub issue</h3>
                      <p>Your description stays yours. Selected sources and a unique retry reference will be added to the issue body.</p>
                      <form className="action-draft-form" onSubmit={(event) => void createIssueProposal(event)}>
                        <label>Repository<input name="repository" value={issueRepository} onChange={(event) => setIssueRepository(event.target.value)} placeholder="owner/repository" minLength={3} maxLength={201} pattern="[^/]+/[^/]+" required /></label>
                        <label>Issue title<input name="issue_title" value={issueTitle} onChange={(event) => setIssueTitle(event.target.value)} placeholder="Short, specific title" maxLength={256} required /></label>
                        <label>Issue description<textarea name="issue_description" value={issueDescription} onChange={(event) => setIssueDescription(event.target.value)} placeholder="Describe the follow-up for your team." maxLength={20000} rows={4} /></label>
                        <div className="action-form-footer"><span>{selectedSourceIds.length} source{selectedSourceIds.length === 1 ? '' : 's'} selected · {Math.min(activeContextRequest.sources.length, 10)} max</span><button type="submit" disabled={actionBusy || selectedSourceIds.length === 0}>{actionBusy ? 'Preparing…' : 'Review issue draft'}</button></div>
                      </form>
                    </div>
                  )}
                  {activeProposal && (
                    <article className="proposal-review">
                      <div className="proposal-review-heading"><div><div className="card-kicker">EXACT PAYLOAD REVIEW · {activeProposal.status.replaceAll('_', ' ')}</div><h3>{activeProposal.payload.title}</h3></div><span className="source-count">{activeProposal.target.repository}</span></div>
                      <div className="payload-hash"><span>SHA-256</span><code>{activeProposal.payload_hash}</code></div>
                      {editingProposal ? (
                        <form className="action-draft-form proposal-edit-form" onSubmit={(event) => void updateIssueProposal(event)}>
                          <label>Repository<input name="repository" value={issueRepository} onChange={(event) => setIssueRepository(event.target.value)} minLength={3} maxLength={201} pattern="[^/]+/[^/]+" required /></label>
                          <label>Issue title<input name="issue_title" value={issueTitle} onChange={(event) => setIssueTitle(event.target.value)} maxLength={256} required /></label>
                          <label>Issue description<textarea name="issue_description" value={issueDescription} onChange={(event) => setIssueDescription(event.target.value)} maxLength={20000} rows={4} /></label>
                          <div className="proposal-buttons"><button type="submit" disabled={actionBusy}>{actionBusy ? 'Saving…' : 'Save updated draft'}</button><button type="button" className="secondary-action" onClick={() => { setIssueRepository(activeProposal.target.repository); setIssueTitle(activeProposal.payload.title); setIssueDescription(activeProposal.editable_description); setEditingProposal(false) }} disabled={actionBusy}>Cancel edit</button><button type="button" className="secondary-action" onClick={() => void cancelIssueProposal()} disabled={actionBusy}>Discard draft</button></div>
                        </form>
                      ) : <pre>{activeProposal.payload.body}</pre>}
                      {activeProposal.execution?.external_url && <p className="action-success"><a href={activeProposal.execution.external_url} target="_blank" rel="noreferrer">Open created GitHub issue ↗</a></p>}
                      {actionMessage && <p className="action-message" role="status">{actionMessage}</p>}
                      {!editingProposal && <div className="proposal-buttons">
                        {activeProposal.status === 'draft' && <button type="button" className="secondary-action" onClick={() => { setIssueRepository(activeProposal.target.repository); setIssueTitle(activeProposal.payload.title); setIssueDescription(activeProposal.editable_description); setEditingProposal(true) }} disabled={actionBusy}>Edit draft</button>}
                        {activeProposal.status === 'draft' && <button type="button" onClick={() => void approveIssueProposal()} disabled={actionBusy}>Approve this exact payload</button>}
                        {activeProposal.status === 'approved' && !activeProposal.execution && <button type="button" className="execute-action" onClick={() => void executeIssueProposal()} disabled={actionBusy}>Create approved GitHub issue</button>}
                        {activeProposal.status === 'approved' && activeProposal.execution && activeProposal.execution.status !== 'succeeded' && <button type="button" className="secondary-action" onClick={() => void executeIssueProposal()} disabled={actionBusy}>Check or retry safely</button>}
                      </div>}
                    </article>
                  )}
                  {!integrations.some((item) => item.provider === 'github' && item.status === 'connected') && <p className="context-empty-detail">Connect and verify GitHub to draft an issue from these sources.</p>}
                  {!activeProposal && actionMessage && <p className="action-message" role="status">{actionMessage}</p>}
                </>
              )}
            </div>
          </div>
        </section>

        <section className="flow-section" id="workflow" aria-labelledby="workflow-title">
          <div className="section-heading">
            <div>
              <div className="card-kicker">THE SAHAKARYA LOOP</div>
              <h2 id="workflow-title">Context that moves work forward</h2>
            </div>
            <p>One connected path from a team's scattered knowledge to a confirmed result.</p>
          </div>

          <ol className="flow-list">
            {[
              ['01', 'Connect tools', 'Verified MCP sources'],
              ['02', 'Gather context', 'Bounded and attributed'],
              ['03', 'Understand intent', 'Grounded in evidence'],
              ['04', 'Generate action', 'Typed and reviewable'],
              ['05', 'Execute', 'Only after approval'],
              ['06', 'Provide feedback', 'Confirmed and recorded'],
            ].map(([number, title, detail], index) => (
              <li className="flow-step" key={number}>
                <div className={`step-number ${index === 4 ? 'step-highlight' : ''}`}>{number}</div>
                <div className="step-copy">
                  <h3>{title}</h3>
                  <p>{detail}</p>
                </div>
                {index < 5 && <span className="step-arrow" aria-hidden="true">→</span>}
              </li>
            ))}
          </ol>
        </section>

        <section className="capability-grid" aria-label="Current foundation status">
          <div className="capability-card">
            <div className="capability-icon icon-context" aria-hidden="true">C</div>
            <div className="card-kicker">CONTEXT SOURCES</div>
            <h2>{integrations.filter((item) => item.status === 'connected').length} integrations verified</h2>
            <p>Only successful MCP handshakes with discovered capabilities count as connected.</p>
            <span className="plain-state"><span className={`small-dot ${integrations.some((item) => item.status === 'connected') ? 'available' : 'muted'}`} /> {integrations.some((item) => item.status === 'connected') ? 'Ready for context retrieval' : 'Not connected'}</span>
          </div>
          <div className="capability-card">
            <div className="capability-icon icon-reasoning" aria-hidden="true">R</div>
            <div className="card-kicker">REASONING PROVIDER</div>
            <h2>{reasoningConfigured ? `${reasoningProviders.find((item) => item.id === selectedReasoningProvider)?.label || 'AI'} · ${reasoningProviders.find((item) => item.id === selectedReasoningProvider)?.model || reasoningModel}` : 'Provider setup needed'}</h2>
            <p>Choose from configured model providers. Grounded answers validate every citation against the evidence supplied to that request.</p>
            <span className="plain-state"><span className={`small-dot ${reasoningConfigured ? 'available' : 'muted'}`} /> {reasoningConfigured ? 'Configured on API' : 'Not configured'}</span>
          </div>
          <div className="capability-card action-card">
            <div className="capability-icon icon-action" aria-hidden="true">A</div>
            <div className="card-kicker">SAFE ACTIONS</div>
            <h2>Approval stays in the loop</h2>
            <p>Every external write will be checked, tied to the reviewed payload, and recorded.</p>
            <span className="plain-state"><span className="small-dot orange" /> Policy boundary established</span>
          </div>
        </section>

        <div className={`runtime-inline ${apiCheck.status}`} aria-live="polite">
          <span className="status-dot" /><span>{overallState}</span><span>API {apiCheck.status}</span><span>PostgreSQL {databaseCheck.status}</span>
          <button className="inline-refresh" type="button" onClick={() => void refreshHealth()} disabled={isRefreshing}>{isRefreshing ? 'Checking…' : 'Refresh health'}</button>
        </div>

        <footer className="page-footer">
          <div className="footer-brand">SAHAKARYA <span>·</span> LIMITLESS</div>
          <p>Project foundation is being built in verified increments.</p>
          <a href="https://modelcontextprotocol.io/" target="_blank" rel="noreferrer">Model Context Protocol ↗</a>
        </footer>
      </main>
    </div>
  )
}

function HealthRow({ title, check }: { title: string; check: RuntimeCheck }) {
  const label =
    check.status === 'loading' ? 'Checking' : check.status === 'available' ? 'Available' : 'Unavailable'

  return (
    <div className="health-row">
      <div className="health-row-main">
        <div className="health-title-line">
          <span className={`small-dot ${check.status}`} />
          <h3>{title}</h3>
          <span className={`health-badge ${check.status}`}>{label}</span>
        </div>
        <p>{check.detail}</p>
      </div>
      <time className="health-time">{formatCheckTime(check.checkedAt)}</time>
    </div>
  )
}

export default App
