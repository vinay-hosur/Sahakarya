import hashlib
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.entities import AuthSession, User, Workspace, WorkspaceMember

router = APIRouter(prefix="/auth", tags=["authentication"])
password_hasher = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hasher.hash("not-a-real-user-password")


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("display_name")
    @classmethod
    def display_name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Display name must contain at least two characters")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    display_name: str


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    description: str
    slug: str
    role: str


class AuthResponse(BaseModel):
    user: UserResponse
    workspaces: list[WorkspaceResponse]


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_session(user: User, db: Session, response: Response) -> None:
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=settings.session_ttl_days)
    db.add(AuthSession(user_id=user.id, token_hash=_token_hash(raw_token), expires_at=expires_at))
    db.commit()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        expires=expires_at,
        httponly=True,
        secure=settings.secure_session_cookie,
        samesite="lax",
        path="/",
    )


def _user_workspaces(db: Session, user_id: UUID) -> list[WorkspaceResponse]:
    rows = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(Workspace.created_at)
    ).all()
    return [
        WorkspaceResponse(
            id=workspace.id,
            name=workspace.name,
            description=workspace.description,
            slug=workspace.slug,
            role=role,
        )
        for workspace, role in rows
    ]


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue")

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        allowed_origins = {item.rstrip("/") for item in settings.allowed_origins}
        if origin and origin.rstrip("/") not in allowed_origins:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Origin is not allowed"
            )

    now = datetime.now(UTC)
    session_user = db.scalar(
        select(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            AuthSession.token_hash == _token_hash(raw_token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )
    if session_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return session_user


def _slug_prefix(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    slug = "".join(character if character.isalnum() else "-" for character in plain)
    slug = "-".join(part for part in slug.split("-") if part)
    return slug[:62].strip("-") or "workspace"


def _create_default_workspace(db: Session, user: User) -> Workspace:
    slug = f"{_slug_prefix(user.display_name)}-{secrets.token_hex(3)}"
    workspace = Workspace(name=f"{user.display_name}'s workspace", slug=slug, owner_user_id=user.id)
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    return workspace


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest, response: Response, db: Session = Depends(get_db)
) -> AuthResponse:
    email = str(payload.email).strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An account already exists"
        )

    user = User(
        email=email,
        display_name=payload.display_name.strip(),
        password_hash=password_hasher.hash(payload.password),
    )
    db.add(user)
    try:
        db.flush()
        _create_default_workspace(db, user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An account already exists"
        ) from exc

    _new_session(user, db, response)
    return AuthResponse(
        user=UserResponse(id=user.id, email=user.email, display_name=user.display_name),
        workspaces=_user_workspaces(db, user.id),
    )


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> AuthResponse:
    email = str(payload.email).strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    verified = False
    stored_hash = user.password_hash if user and user.password_hash else DUMMY_PASSWORD_HASH
    try:
        verified = password_hasher.verify(payload.password, stored_hash)
    except Exception:
        verified = False
    if not verified or user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect"
        )

    _new_session(user, db, response)
    return AuthResponse(
        user=UserResponse(id=user.id, email=user.email, display_name=user.display_name),
        workspaces=_user_workspaces(db, user.id),
    )


@router.get("/me", response_model=AuthResponse)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> AuthResponse:
    return AuthResponse(
        user=UserResponse(id=user.id, email=user.email, display_name=user.display_name),
        workspaces=_user_workspaces(db, user.id),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Response:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        session = db.scalar(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.token_hash == _token_hash(raw_token),
                AuthSession.revoked_at.is_(None),
            )
        )
        if session:
            session.revoked_at = datetime.now(UTC)
            db.commit()
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.secure_session_cookie,
        samesite="lax",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
