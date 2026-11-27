import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote
from uuid import UUID

import httpx2
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import (
    AuditEvent,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
)

router = APIRouter(prefix="/workspaces", tags=["workspace members and invitations"])
MemberRole = Literal["admin", "member", "viewer"]
logger = logging.getLogger("sahakarya.invitations")


class MemberView(BaseModel):
    workspace_id: UUID
    user_id: UUID
    email: EmailStr
    display_name: str
    role: str
    joined_at: datetime


class InviteCreate(BaseModel):
    email: EmailStr
    role: MemberRole = "member"

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().casefold()


class InviteCreated(BaseModel):
    id: UUID
    workspace_id: UUID
    email: EmailStr
    role: MemberRole
    expires_at: datetime
    token: str
    invitation_url: str
    email_status: Literal["sent", "not_configured", "failed"]


class InviteView(BaseModel):
    id: UUID
    workspace_id: UUID
    email: EmailStr
    role: MemberRole
    expires_at: datetime
    created_at: datetime
    status: Literal["pending", "accepted", "expired", "revoked"]


class InviteAccept(BaseModel):
    token: str = Field(min_length=32, max_length=200)


class MemberRoleUpdate(BaseModel):
    role: MemberRole


def _workspace_role(db: Session, workspace_id: UUID, user_id: UUID) -> str:
    role = db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if role not in {"owner", "admin", "member", "viewer"}:
        raise HTTPException(status_code=403, detail="Workspace role is invalid")
    return role


def _require_manager(db: Session, workspace_id: UUID, user_id: UUID) -> tuple[Workspace, str]:
    role = _workspace_role(db, workspace_id, user_id)
    if role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Workspace admin access is required")
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace, role


def _member_query(workspace_id: UUID):
    return (
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.joined_at, User.display_name)
    )


def _member_view(member: WorkspaceMember, user: User) -> MemberView:
    return MemberView(
        workspace_id=member.workspace_id,
        user_id=member.user_id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
        joined_at=member.joined_at,
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _invitation_url(token: str) -> str:
    return f"{settings.app_public_url}/#team?invite={quote(token, safe='')}"


async def _send_invitation_email(email: str, workspace_name: str, role: str, url: str) -> str:
    if not settings.invitation_email_configured:
        return "not_configured"
    assert settings.resend_api_key is not None
    try:
        async with httpx2.AsyncClient(timeout=httpx2.Timeout(10.0, read=20.0)) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.resend_api_key.get_secret_value()}"},
                json={
                    "from": settings.invitation_from_email,
                    "to": [email],
                    "subject": f"Join {workspace_name} on SAHAKARYA",
                    "text": (
                        f"You have been invited to join {workspace_name} as a {role}.\n\n"
                        f"Accept the invitation: {url}\n\n"
                        "This link expires in seven days and can only be used by the invited "
                        "email address."
                    ),
                },
            )
            response.raise_for_status()
        return "sent"
    except Exception as exc:
        logger.warning("invitation email delivery failed error_type=%s", type(exc).__name__)
        return "failed"


def _invite_status(invitation: WorkspaceInvitation, now: datetime) -> str:
    if invitation.accepted_at:
        return "accepted"
    if invitation.revoked_at:
        return "revoked"
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return "expired" if expires_at <= now else "pending"


@router.post(
    "/{workspace_id}/invitations", response_model=InviteCreated, status_code=status.HTTP_201_CREATED
)
async def create_invitation(
    workspace_id: UUID,
    payload: InviteCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> InviteCreated:
    workspace, actor_role = _require_manager(db, workspace_id, user.id)
    if actor_role == "admin" and payload.role == "admin":
        raise HTTPException(status_code=403, detail="Only the workspace owner can invite admins")
    email = str(payload.email).casefold()
    email_member = db.scalar(
        select(User.id)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            User.email.ilike(email),
        )
    )
    if email_member:
        raise HTTPException(status_code=409, detail="That email already belongs to this workspace")
    now = datetime.now(UTC)
    pending = db.scalar(
        select(WorkspaceInvitation.id).where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.invited_email == email,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
            WorkspaceInvitation.expires_at > now,
        )
    )
    if pending:
        raise HTTPException(
            status_code=409, detail="An active invitation already exists for that email"
        )
    token = secrets.token_urlsafe(36)
    expires_at = now + timedelta(days=7)
    invitation = WorkspaceInvitation(
        workspace_id=workspace_id,
        invited_email=email,
        role=payload.role,
        token_hash=_token_hash(token),
        expires_at=expires_at,
        invited_by=user.id,
    )
    db.add(invitation)
    db.flush()
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="workspace.member_invited",
            object_type="workspace_invitation",
            object_id=invitation.id,
            details_json={"email": email, "role": payload.role},
        )
    )
    db.commit()
    invitation_link = _invitation_url(token)
    email_status = await _send_invitation_email(
        email, workspace.name, payload.role, invitation_link
    )
    return InviteCreated(
        id=invitation.id,
        workspace_id=workspace_id,
        email=email,
        role=payload.role,
        expires_at=expires_at,
        token=token,
        invitation_url=invitation_link,
        email_status=email_status,
    )


@router.get("/{workspace_id}/invitations", response_model=list[InviteView])
def list_invitations(
    workspace_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[InviteView]:
    _require_manager(db, workspace_id, user.id)
    now = datetime.now(UTC)
    rows = db.scalars(
        select(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.workspace_id == workspace_id,
        )
        .order_by(WorkspaceInvitation.created_at.desc())
    ).all()
    return [
        InviteView(
            id=row.id,
            workspace_id=row.workspace_id,
            email=row.invited_email,
            role=row.role,
            expires_at=row.expires_at,
            created_at=row.created_at,
            status=_invite_status(row, now),
        )
        for row in rows
    ]


@router.delete(
    "/{workspace_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT
)
def revoke_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    _, actor_role = _require_manager(db, workspace_id, user.id)
    invitation = db.scalar(
        select(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.id == invitation_id,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if invitation is None:
        raise HTTPException(status_code=404, detail="Pending invitation not found")
    if actor_role == "admin" and invitation.role == "admin":
        raise HTTPException(
            status_code=403, detail="Only the workspace owner can revoke admin invites"
        )
    invitation.revoked_at = datetime.now(UTC)
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="workspace.invitation_revoked",
            object_type="workspace_invitation",
            object_id=invitation.id,
            details_json={"email": invitation.invited_email},
        )
    )
    db.commit()


@router.post("/{workspace_id}/invitations/{invitation_id}/resend", response_model=InviteCreated)
async def resend_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> InviteCreated:
    workspace, actor_role = _require_manager(db, workspace_id, user.id)
    invitation = db.scalar(
        select(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.id == invitation_id,
        )
        .with_for_update()
    )
    if invitation is None or invitation.accepted_at or invitation.revoked_at:
        raise HTTPException(status_code=404, detail="Invitation is no longer resendable")
    if actor_role == "admin" and invitation.role == "admin":
        raise HTTPException(
            status_code=403, detail="Only the workspace owner can resend admin invites"
        )
    token = secrets.token_urlsafe(36)
    now = datetime.now(UTC)
    invitation.token_hash = _token_hash(token)
    invitation.expires_at = now + timedelta(days=7)
    invitation.invited_by = user.id
    db.commit()
    invitation_link = _invitation_url(token)
    email_status = await _send_invitation_email(
        invitation.invited_email, workspace.name, invitation.role, invitation_link
    )
    return InviteCreated(
        id=invitation.id,
        workspace_id=workspace_id,
        email=invitation.invited_email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        token=token,
        invitation_url=invitation_link,
        email_status=email_status,
    )


@router.post("/invitations/accept", response_model=MemberView)
def accept_invitation(
    payload: InviteAccept,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MemberView:
    invitation = db.scalar(
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.token_hash == _token_hash(payload.token))
        .with_for_update()
    )
    now = datetime.now(UTC)
    if invitation is None or invitation.accepted_at or invitation.revoked_at:
        raise HTTPException(status_code=404, detail="Invitation is invalid or no longer active")
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise HTTPException(status_code=404, detail="Invitation has expired")
    if user.email.casefold() != invitation.invited_email.casefold():
        raise HTTPException(
            status_code=403, detail="Sign in with the email address that was invited"
        )
    existing = db.get(WorkspaceMember, (invitation.workspace_id, user.id))
    if existing is not None:
        raise HTTPException(status_code=409, detail="You already belong to this workspace")
    member = WorkspaceMember(
        workspace_id=invitation.workspace_id,
        user_id=user.id,
        role=invitation.role,
    )
    db.add(member)
    invitation.accepted_at = now
    invitation.accepted_by = user.id
    db.add(
        AuditEvent(
            workspace_id=invitation.workspace_id,
            actor_user_id=user.id,
            event_type="workspace.member_joined",
            object_type="workspace_member",
            object_id=user.id,
            details_json={"role": invitation.role},
        )
    )
    db.commit()
    db.refresh(member)
    return _member_view(member, user)


@router.patch("/{workspace_id}/members/{member_user_id}", response_model=MemberView)
def update_member_role(
    workspace_id: UUID,
    member_user_id: UUID,
    payload: MemberRoleUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MemberView:
    workspace, actor_role = _require_manager(db, workspace_id, user.id)
    member = db.scalar(
        select(WorkspaceMember)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == member_user_id,
        )
        .with_for_update()
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Workspace member not found")
    if member.user_id == workspace.owner_user_id or member.role == "owner":
        raise HTTPException(
            status_code=409, detail="The workspace owner role cannot be changed here"
        )
    if actor_role == "admin" and (member.role == "admin" or payload.role == "admin"):
        raise HTTPException(status_code=403, detail="Only the workspace owner can manage admins")
    if member.role == payload.role:
        raise HTTPException(status_code=422, detail="Member already has that role")
    previous_role = member.role
    member.role = payload.role
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="workspace.member_role_changed",
            object_type="workspace_member",
            object_id=member_user_id,
            details_json={"previous_role": previous_role, "role": payload.role},
        )
    )
    db.commit()
    person = db.get(User, member_user_id)
    return _member_view(member, person)


@router.delete("/{workspace_id}/members/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    workspace_id: UUID,
    member_user_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    workspace, actor_role = _require_manager(db, workspace_id, user.id)
    member = db.scalar(
        select(WorkspaceMember)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == member_user_id,
        )
        .with_for_update()
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Workspace member not found")
    if (
        member.user_id == user.id
        or member.user_id == workspace.owner_user_id
        or member.role == "owner"
    ):
        raise HTTPException(status_code=409, detail="The workspace owner cannot be removed")
    if actor_role == "admin" and member.role == "admin":
        raise HTTPException(status_code=403, detail="Only the workspace owner can remove admins")
    removed_role = member.role
    db.delete(member)
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user.id,
            event_type="workspace.member_removed",
            object_type="workspace_member",
            object_id=member_user_id,
            details_json={"role": removed_role},
        )
    )
    db.commit()


@router.get("/{workspace_id}/members", response_model=list[MemberView])
def list_members(
    workspace_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[MemberView]:
    _workspace_role(db, workspace_id, user.id)
    return [
        _member_view(member, person)
        for member, person in db.execute(_member_query(workspace_id)).all()
    ]
