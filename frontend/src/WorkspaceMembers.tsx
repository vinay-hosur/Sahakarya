import { useEffect, useState, type FormEvent } from 'react'
import { apiBaseUrl } from './api'

type Member = {
  workspace_id: string
  user_id: string
  email: string
  display_name: string
  role: string
  joined_at: string
}
type Invitation = {
  id: string
  email: string
  role: 'member' | 'viewer'
  expires_at: string
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

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value))
}

export default function WorkspaceMembers({
  workspaceId,
  currentUserId,
  role,
  onAccepted,
}: {
  workspaceId: string
  currentUserId: string
  role: string
  onAccepted: (workspaceId: string) => void
}) {
  const [members, setMembers] = useState<Member[]>([])
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [inviteToken, setInviteToken] = useState('')
  const [acceptToken, setAcceptToken] = useState('')
  const [message, setMessage] = useState('')
  const [busyId, setBusyId] = useState('')
  const isOwner = role === 'owner'
  const membersEndpoint = `/api/v1/workspaces/${workspaceId}/members`

  useEffect(() => {
    let current = true
    const load = async () => {
      const response = await request(membersEndpoint)
      const payload = await response.json()
      if (!response.ok) throw new Error(apiMessage(payload, 'Could not load workspace members.'))
      if (current) setMembers(payload as Member[])
      if (isOwner) {
        const inviteResponse = await request(`/api/v1/workspaces/${workspaceId}/invitations`)
        const invitePayload = await inviteResponse.json()
        if (!inviteResponse.ok) throw new Error(apiMessage(invitePayload, 'Could not load invitations.'))
        if (current) setInvitations(invitePayload as Invitation[])
      } else if (current) {
        setInvitations([])
      }
    }
    void load().catch((error) => {
      if (current) setMessage(error instanceof Error ? error.message : 'Could not load workspace members.')
    })
    return () => { current = false }
  }, [membersEndpoint, workspaceId, isOwner])

  async function createInvitation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const values = new FormData(form)
    setBusyId('invite')
    setMessage('Creating invitation…')
    try {
      const response = await request(`/api/v1/workspaces/${workspaceId}/invitations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: String(values.get('email') || ''), role: String(values.get('role') || 'member') }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(apiMessage(payload, 'Could not invite this member.'))
      setInviteToken(String(payload.token))
      setInvitations((current) => [payload as Invitation, ...current])
      setMessage(`Invitation created for ${String(payload.email)}. Copy the code below and share it with them.`)
      form.reset()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not invite this member.')
    } finally {
      setBusyId('')
    }
  }

  async function acceptInvitation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusyId('accept')
    setMessage('Checking invitation…')
    try {
      const response = await request('/api/v1/workspaces/invitations/accept', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: acceptToken.trim() }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(apiMessage(payload, 'Could not accept invitation.'))
      setAcceptToken('')
      setMessage(`You joined ${payload.workspace_id} as a ${payload.role}.`)
      onAccepted(String(payload.workspace_id))
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not accept invitation.')
    } finally {
      setBusyId('')
    }
  }

  async function changeRole(member: Member, nextRole: string) {
    setBusyId(member.user_id)
    setMessage('Saving member role…')
    try {
      const response = await request(`${membersEndpoint}/${member.user_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: nextRole }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(apiMessage(payload, 'Could not update member role.'))
      setMembers((current) => current.map((item) => item.user_id === member.user_id ? payload as Member : item))
      setMessage(`${member.display_name} is now a ${nextRole}.`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not update member role.')
    } finally {
      setBusyId('')
    }
  }

  async function removeMember(member: Member) {
    if (!window.confirm(`Remove ${member.display_name} from this workspace?`)) return
    setBusyId(member.user_id)
    setMessage('Removing workspace member…')
    try {
      const response = await request(`${membersEndpoint}/${member.user_id}`, { method: 'DELETE' })
      if (!response.ok) {
        const payload = await response.json()
        throw new Error(apiMessage(payload, 'Could not remove member.'))
      }
      setMembers((current) => current.filter((item) => item.user_id !== member.user_id))
      setMessage(`${member.display_name} was removed from this workspace.`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not remove member.')
    } finally {
      setBusyId('')
    }
  }

  async function revokeInvitation(invitation: Invitation) {
    setBusyId(invitation.id)
    setMessage('Revoking invitation…')
    try {
      const response = await request(`/api/v1/workspaces/${workspaceId}/invitations/${invitation.id}`, { method: 'DELETE' })
      if (!response.ok) {
        const payload = await response.json()
        throw new Error(apiMessage(payload, 'Could not revoke invitation.'))
      }
      setInvitations((current) => current.filter((item) => item.id !== invitation.id))
      setMessage(`Invitation for ${invitation.email} revoked.`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not revoke invitation.')
    } finally {
      setBusyId('')
    }
  }

  async function copyToken() {
    try {
      await navigator.clipboard.writeText(inviteToken)
      setMessage('Invitation code copied. Share it with the invited person.')
    } catch {
      setMessage('Copy was unavailable; select and copy the invitation code manually.')
    }
  }

  return (
    <section className="members-section" id="team" aria-labelledby="members-title">
      <div className="section-heading"><div><div className="card-kicker">WORKSPACE ACCESS</div><h2 id="members-title">Your team</h2></div><p>Owners manage invites and roles. Members and viewers can see the workspace roster.</p></div>
      <div className="members-layout">
        <div className="members-panel">
          <h3>Members ({members.length})</h3>
          <ul className="member-list">{members.map((member) => <li className="member-row" key={member.user_id}>
            <div className="member-person"><strong>{member.display_name}{member.user_id === currentUserId ? ' (you)' : ''}</strong><span>{member.email}</span><small>Joined {formatDate(member.joined_at)}</small></div>
            {isOwner && member.role !== 'owner' ? <div className="member-actions"><select aria-label={`Role for ${member.display_name}`} value={member.role} disabled={busyId === member.user_id} onChange={(event) => void changeRole(member, event.target.value)}><option value="member">Member</option><option value="viewer">Viewer</option></select><button type="button" className="member-remove" disabled={busyId === member.user_id} onClick={() => void removeMember(member)}>Remove</button></div> : <span className="member-role">{member.role}</span>}
          </li>)}</ul>
        </div>
        <div className="members-panel">
          {isOwner ? <>
            <h3>Invite someone</h3>
            <form className="member-invite-form" onSubmit={(event) => void createInvitation(event)}><label>Email<input name="email" type="email" required /></label><label>Role<select name="role" defaultValue="member"><option value="member">Member</option><option value="viewer">Viewer</option></select></label><button type="submit" disabled={busyId === 'invite'}>{busyId === 'invite' ? 'Creating…' : 'Create invitation'}</button></form>
            {inviteToken && <div className="invitation-token"><strong>One-time invitation code</strong><p>Share this with the invited person. They must sign in using the invited email address. The code is shown only now.</p><code>{inviteToken}</code><button type="button" onClick={() => void copyToken()}>Copy code</button></div>}
            <h4>Pending invitations</h4>
            {invitations.length === 0 ? <p className="member-empty">No pending invitations.</p> : <ul className="invitation-list">{invitations.map((invitation) => <li key={invitation.id}><div><strong>{invitation.email}</strong><span>{invitation.role} · expires {formatDate(invitation.expires_at)}</span></div><button type="button" className="member-remove" disabled={busyId === invitation.id} onClick={() => void revokeInvitation(invitation)}>Revoke</button></li>)}</ul>}
          </> : <>
            <h3>Accept an invitation</h3>
            <p className="member-empty">Sign in with the email address the workspace owner invited, then enter your one-time invitation code.</p>
            <form className="member-accept-form" onSubmit={(event) => void acceptInvitation(event)}><label>Invitation code<textarea value={acceptToken} onChange={(event) => setAcceptToken(event.target.value)} minLength={32} maxLength={200} rows={3} required /></label><button type="submit" disabled={busyId === 'accept'}>{busyId === 'accept' ? 'Checking…' : 'Accept invitation'}</button></form>
          </>}
        </div>
      </div>
      {message && <p className="members-message" role="status">{message}</p>}
    </section>
  )
}
