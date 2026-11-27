import hashlib
import logging
import re
import xml.etree.ElementTree as ElementTree
from datetime import datetime
from io import BytesIO
from pathlib import PurePosixPath
from types import SimpleNamespace
from urllib.parse import quote
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pypdf import PdfReader
from sqlalchemy import String, cast, delete, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.v1.auth import current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import (
    AuditEvent,
    KnowledgeDocument,
    KnowledgeDocumentChunk,
    KnowledgeDocumentComment,
    KnowledgeDocumentFile,
    KnowledgeDocumentVersion,
    Project,
    User,
    WorkspaceMember,
)
from app.services.file_storage import delete_object, download_object, upload_object
from app.services.reasoning import ReasoningUnavailable, reason_over_context

router = APIRouter(prefix="/workspaces/{workspace_id}/documents", tags=["knowledge documents"])
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
DATABASE_UPLOAD_LIMIT_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 100_000
CHUNK_WORDS = 300
CHUNK_OVERLAP_WORDS = 60
logger = logging.getLogger("sahakarya.documents")


def _tags(values: list[str]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = value.strip()
        key = tag.casefold()
        if not tag:
            continue
        if len(tag) > 40:
            raise ValueError("Each tag must be 40 characters or fewer")
        if key not in seen:
            seen.add(key)
            tags.append(tag)
    if len(tags) > 20:
        raise ValueError("A document can have at most 20 tags")
    return tags


def _split_chunks(content: str) -> list[str]:
    words = content.split()
    step = CHUNK_WORDS - CHUNK_OVERLAP_WORDS
    return [" ".join(words[start : start + CHUNK_WORDS]) for start in range(0, len(words), step)]


def _index_document(db: Session, document: KnowledgeDocument) -> None:
    db.execute(
        delete(KnowledgeDocumentChunk).where(KnowledgeDocumentChunk.document_id == document.id)
    )
    for ordinal, content in enumerate(_split_chunks(document.content)):
        db.add(
            KnowledgeDocumentChunk(
                workspace_id=document.workspace_id,
                document_id=document.id,
                ordinal=ordinal,
                content=content,
                word_count=len(content.split()),
            )
        )


def _extract_file(filename: str, data: bytes) -> tuple[str, str]:
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.casefold()
    if suffix == ".pdf":
        try:
            reader = PdfReader(BytesIO(data))
            parts: list[str] = []
            total = 0
            for page in reader.pages:
                page_text = page.extract_text() or ""
                remaining = MAX_EXTRACTED_CHARACTERS + 1 - total
                parts.append(page_text[:remaining])
                total += len(page_text)
                if total > MAX_EXTRACTED_CHARACTERS:
                    raise HTTPException(
                        status_code=413,
                        detail="Extracted text exceeds the 100,000-character limit.",
                    )
            text = "\n\n".join(parts)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail="This PDF could not be read.") from exc
        content_type = "application/pdf"
    elif suffix == ".docx":
        try:
            with ZipFile(BytesIO(data)) as archive:
                document_info = archive.getinfo("word/document.xml")
                xml_limit = 2 * 1024 * 1024
                if document_info.file_size > xml_limit:
                    raise HTTPException(status_code=413, detail="DOCX document XML is too large.")
                with archive.open(document_info) as document_file:
                    document_xml = document_file.read(xml_limit + 1)
                if len(document_xml) > xml_limit:
                    raise HTTPException(status_code=413, detail="DOCX document XML is too large.")
                xml = ElementTree.fromstring(document_xml)
            paragraphs = []
            for paragraph in xml.iter(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
            ):
                value = "".join(
                    text_node.text or ""
                    for text_node in paragraph.iter(
                        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                    )
                )
                if value.strip():
                    paragraphs.append(value)
            text = "\n\n".join(paragraphs)
        except (BadZipFile, KeyError, ElementTree.ParseError) as exc:
            raise HTTPException(
                status_code=422, detail="This DOCX file could not be read."
            ) from exc
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif suffix in {".md", ".txt"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=422, detail="Text files must use UTF-8 encoding."
            ) from exc
        content_type = "text/markdown" if suffix == ".md" else "text/plain"
    else:
        raise HTTPException(status_code=415, detail="Upload a PDF, DOCX, Markdown, or TXT file.")
    extracted = text.strip()
    if not extracted:
        raise HTTPException(
            status_code=422,
            detail="No text could be extracted. Scanned PDFs need OCR before upload.",
        )
    if len(extracted) > MAX_EXTRACTED_CHARACTERS:
        raise HTTPException(
            status_code=413, detail="Extracted text exceeds the 100,000-character limit."
        )
    return extracted, content_type


class DocumentCreate(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    content: str = Field(min_length=1, max_length=100000)
    category: str = Field(default="General", min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list)
    project_id: UUID | None = None

    @field_validator("title", "category")
    @classmethod
    def trim_required_labels(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value

    @field_validator("content")
    @classmethod
    def content_cannot_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Document content cannot be blank")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        return _tags(values)


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=240)
    content: str | None = Field(default=None, min_length=1, max_length=100000)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    tags: list[str] | None = None
    project_id: UUID | None = None

    @field_validator("title", "content", "category")
    @classmethod
    def trim_non_null_values(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_optional_tags(cls, values: list[str] | None) -> list[str] | None:
        return _tags(values) if values is not None else None

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one document field to update")
        if any(getattr(self, field) is None for field in self.model_fields_set - {"project_id"}):
            raise ValueError("Document fields cannot be null")
        return self


class DocumentCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)

    @field_validator("body")
    @classmethod
    def body_cannot_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Comment cannot be blank")
        return value


class DocumentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    owner_user_id: UUID
    owner_display_name: str
    title: str
    content: str
    category: str
    tags: list[str]
    version_number: int
    source_filename: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentSearchResult(BaseModel):
    document_id: UUID
    title: str
    project_id: UUID | None
    category: str
    source_filename: str | None
    excerpt: str
    relevance: float
    updated_at: datetime


class DocumentSearchAnswer(BaseModel):
    query: str
    results: list[DocumentSearchResult]
    answer: dict | None = None
    detail: str | None = None


class DocumentSearchQuery(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    project_id: UUID | None = None
    limit: int = Field(default=10, ge=1, le=20)
    provider: str | None = Field(
        default=None, pattern="^(openai|anthropic|gemini|groq|xai|cloudflare)$"
    )

    @field_validator("query")
    @classmethod
    def trim_query(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Enter at least two non-space characters")
        return value


class DocumentVersionView(BaseModel):
    id: UUID
    document_id: UUID
    version_number: int
    snapshot: dict
    created_by: UUID
    created_by_name: str
    created_at: datetime


class DocumentCommentView(BaseModel):
    id: UUID
    document_id: UUID
    author_user_id: UUID
    author_display_name: str
    body: str
    created_at: datetime


def _workspace_role(db: Session, workspace_id: UUID, user_id: UUID, *, write: bool) -> str:
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
    if write and role == "viewer":
        raise HTTPException(status_code=403, detail="Viewer access is read-only")
    return role


def _project_in_workspace(db: Session, workspace_id: UUID, project_id: UUID | None) -> None:
    if project_id is None:
        return
    exists = db.scalar(
        select(Project.id).where(Project.id == project_id, Project.workspace_id == workspace_id)
    )
    if exists is None:
        raise HTTPException(status_code=404, detail="Project not found in this workspace")


def _document(db: Session, workspace_id: UUID, document_id: UUID) -> KnowledgeDocument:
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.workspace_id == workspace_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _can_edit_document(
    db: Session, document: KnowledgeDocument, user_id: UUID, workspace_role: str
) -> bool:
    return workspace_role in {"owner", "admin"} or document.owner_user_id == user_id


def _document_view(db: Session, document: KnowledgeDocument) -> DocumentView:
    owner_name = db.scalar(select(User.display_name).where(User.id == document.owner_user_id))
    source_filename = db.scalar(
        select(KnowledgeDocumentFile.original_filename).where(
            KnowledgeDocumentFile.document_id == document.id
        )
    )
    return DocumentView(
        id=document.id,
        workspace_id=document.workspace_id,
        project_id=document.project_id,
        owner_user_id=document.owner_user_id,
        owner_display_name=owner_name or "Former member",
        title=document.title,
        content=document.content,
        category=document.category,
        tags=document.tags,
        version_number=document.version_number,
        source_filename=source_filename,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _snapshot(document: KnowledgeDocument) -> dict:
    return {
        "title": document.title,
        "content": document.content,
        "category": document.category,
        "tags": document.tags,
        "project_id": str(document.project_id) if document.project_id else None,
        "owner_user_id": str(document.owner_user_id),
    }


def _add_version(db: Session, document: KnowledgeDocument, user_id: UUID) -> None:
    db.add(
        KnowledgeDocumentVersion(
            document_id=document.id,
            version_number=document.version_number,
            snapshot_json=_snapshot(document),
            created_by=user_id,
        )
    )


def _search_documents(
    db: Session, workspace_id: UUID, query: str, project_id: UUID | None, limit: int
) -> tuple[list[DocumentSearchResult], list[SimpleNamespace]]:
    terms = list(dict.fromkeys(re.findall(r"[\w'-]{2,}", query.casefold())))[:20]
    if not terms:
        return [], []
    statement = (
        select(KnowledgeDocumentChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeDocumentChunk.document_id)
        .where(KnowledgeDocumentChunk.workspace_id == workspace_id)
    )
    if project_id is not None:
        statement = statement.where(KnowledgeDocument.project_id == project_id)
    rows = db.execute(statement).all()
    # Older native documents may predate chunk indexing; index their current content on read.
    indexed_ids = {chunk.document_id for chunk, _ in rows}
    missing_statement = select(KnowledgeDocument).where(
        KnowledgeDocument.workspace_id == workspace_id,
        ~KnowledgeDocument.id.in_(indexed_ids) if indexed_ids else True,
    )
    if project_id is not None:
        missing_statement = missing_statement.where(KnowledgeDocument.project_id == project_id)
    for document in db.scalars(missing_statement).all():
        for ordinal, content in enumerate(_split_chunks(document.content)):
            rows.append(
                (
                    SimpleNamespace(document_id=document.id, ordinal=ordinal, content=content),
                    document,
                )
            )

    ranked: list[tuple[float, DocumentSearchResult, SimpleNamespace]] = []
    phrase = query.casefold().strip()
    for chunk, document in rows:
        content = chunk.content
        lower_content = content.casefold()
        lower_title = document.title.casefold()
        count = sum(lower_content.count(term) for term in terms)
        title_count = sum(lower_title.count(term) for term in terms)
        if not count and not title_count:
            continue
        score = count + title_count * 3
        if phrase in lower_content:
            score += 4
        if phrase in lower_title:
            score += 6
        start = 0
        positions = [lower_content.find(term) for term in terms if lower_content.find(term) >= 0]
        if positions:
            start = max(0, min(positions) - 180)
        excerpt = content[start : start + 600].strip()
        if start:
            excerpt = "…" + excerpt
        if start + 600 < len(content):
            excerpt += "…"
        source_filename = db.scalar(
            select(KnowledgeDocumentFile.original_filename).where(
                KnowledgeDocumentFile.document_id == document.id
            )
        )
        result = DocumentSearchResult(
            document_id=document.id,
            title=document.title,
            project_id=document.project_id,
            category=document.category,
            source_filename=source_filename,
            excerpt=excerpt,
            relevance=round(score / max(1, len(terms)), 3),
            updated_at=document.updated_at,
        )
        source = SimpleNamespace(
            id=document.id,
            provider="native_document",
            title=document.title,
            source_url=f"/api/v1/workspaces/{workspace_id}/documents/{document.id}",
            content_text=content,
        )
        ranked.append((score, result, source))
    ranked.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    # Keep one strongest matching passage per document.
    unique: list[tuple[DocumentSearchResult, SimpleNamespace]] = []
    seen: set[UUID] = set()
    for _, result, source in ranked:
        if result.document_id not in seen:
            unique.append((result, source))
            seen.add(result.document_id)
        if len(unique) >= limit:
            break
    return [item[0] for item in unique], [item[1] for item in unique]


@router.get("/search", response_model=list[DocumentSearchResult])
def search_documents(
    workspace_id: UUID,
    q: str = Query(min_length=2, max_length=1000),
    project_id: UUID | None = None,
    limit: int = Query(default=10, ge=1, le=20),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[DocumentSearchResult]:
    _workspace_role(db, workspace_id, user.id, write=False)
    _project_in_workspace(db, workspace_id, project_id)
    results, _ = _search_documents(db, workspace_id, q.strip(), project_id, limit)
    return results


@router.post("/search/answer", response_model=DocumentSearchAnswer)
async def answer_from_documents(
    workspace_id: UUID,
    payload: DocumentSearchQuery,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DocumentSearchAnswer:
    _workspace_role(db, workspace_id, user.id, write=False)
    _project_in_workspace(db, workspace_id, payload.project_id)
    results, sources = _search_documents(
        db, workspace_id, payload.query, payload.project_id, payload.limit
    )
    answer = None
    detail = None
    if sources:
        try:
            answer = await reason_over_context(
                SimpleNamespace(prompt=payload.query),
                sources[:8],
                provider=payload.provider,
            )
        except ReasoningUnavailable as exc:
            detail = str(exc)
    if answer is None and detail is None:
        detail = (
            "No matching passages were found."
            if not results
            else "Evidence was found. Configure the reasoning provider for a grounded answer."
        )
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=payload.project_id,
            actor_user_id=user.id,
            event_type="document.search_answered",
            object_type="workspace",
            object_id=workspace_id,
            details_json={"query_length": len(payload.query), "result_count": len(results)},
        )
    )
    db.commit()
    return DocumentSearchAnswer(query=payload.query, results=results, answer=answer, detail=detail)


@router.get("", response_model=list[DocumentView])
def list_documents(
    workspace_id: UUID,
    project_id: UUID | None = None,
    category: str | None = Query(default=None, max_length=100),
    tag: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, min_length=1, max_length=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[DocumentView]:
    _workspace_role(db, workspace_id, user.id, write=False)
    _project_in_workspace(db, workspace_id, project_id)
    query = select(KnowledgeDocument).where(KnowledgeDocument.workspace_id == workspace_id)
    if project_id is not None:
        query = query.where(KnowledgeDocument.project_id == project_id)
    if category:
        query = query.where(KnowledgeDocument.category.ilike(category.strip()))
    if tag:
        query = query.where(cast(KnowledgeDocument.tags, String).ilike(f'%"{tag.strip()}"%'))
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            KnowledgeDocument.title.ilike(pattern) | KnowledgeDocument.content.ilike(pattern)
        )
    documents = db.scalars(query.order_by(KnowledgeDocument.updated_at.desc())).all()
    return [_document_view(db, document) for document in documents]


@router.post("/upload", response_model=DocumentView, status_code=status.HTTP_201_CREATED)
async def upload_document(
    workspace_id: UUID,
    file: UploadFile = File(...),
    title: str | None = Form(default=None, max_length=240),
    category: str = Form(default="General", max_length=100),
    tags: str = Form(default=""),
    project_id: UUID | None = Form(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DocumentView:
    _workspace_role(db, workspace_id, user.id, write=True)
    _project_in_workspace(db, workspace_id, project_id)
    original_filename = PurePosixPath((file.filename or "").replace("\\", "/")).name
    original_filename = re.sub(r"[\x00-\x1f\x7f]", "", original_filename).strip()[:255]
    if not original_filename:
        raise HTTPException(status_code=422, detail="Choose a file with a valid name.")
    file_content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not file_content:
        raise HTTPException(status_code=422, detail="The selected file is empty.")
    if len(file_content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Files must be 50 MiB or smaller.")
    if len(file_content) > DATABASE_UPLOAD_LIMIT_BYTES and not settings.object_storage_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "Files above 5 MiB require Cloudflare R2 object storage. "
                "Ask the administrator to finish the file-storage setup."
            ),
        )
    content, content_type = _extract_file(original_filename, file_content)
    clean_title = (title or "").strip() or PurePosixPath(original_filename).stem.strip()
    if len(clean_title) < 2:
        clean_title = f"{clean_title} document".strip()
    clean_category = category.strip()
    if not clean_category:
        raise HTTPException(status_code=422, detail="Category cannot be blank.")
    try:
        clean_tags = _tags([tag for tag in tags.split(",")])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    document = KnowledgeDocument(
        workspace_id=workspace_id,
        project_id=project_id,
        owner_user_id=user.id,
        title=clean_title[:240],
        content=content,
        category=clean_category,
        tags=clean_tags,
        version_number=1,
    )
    storage_key = None
    if settings.object_storage_configured:
        digest = hashlib.sha256(file_content).hexdigest()
        storage_key = f"workspaces/{workspace_id}/documents/{document.id}/{digest}"
        try:
            await run_in_threadpool(upload_object, storage_key, file_content, content_type)
        except Exception as exc:
            logger.warning("document object upload failed error_type=%s", type(exc).__name__)
            raise HTTPException(
                status_code=503,
                detail="File storage is temporarily unavailable. The document was not created.",
            ) from exc

    try:
        db.add(document)
        db.flush()
        db.add(
            KnowledgeDocumentFile(
                document_id=document.id,
                workspace_id=workspace_id,
                original_filename=original_filename,
                content_type=content_type,
                file_size=len(file_content),
                sha256=hashlib.sha256(file_content).hexdigest(),
                file_content=None if storage_key else file_content,
                storage_key=storage_key,
                uploaded_by=user.id,
            )
        )
        _add_version(db, document, user.id)
        _index_document(db, document)
        db.add(
            AuditEvent(
                workspace_id=workspace_id,
                project_id=project_id,
                actor_user_id=user.id,
                event_type="document.uploaded",
                object_type="document",
                object_id=document.id,
                details_json={
                    "project_id": str(project_id) if project_id else None,
                    "filename": original_filename,
                    "file_size": len(file_content),
                    "storage": "object_storage" if storage_key else "database_legacy",
                },
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        if storage_key:
            try:
                await run_in_threadpool(delete_object, storage_key)
            except Exception as cleanup_error:
                logger.warning(
                    "orphan upload cleanup failed error_type=%s", type(cleanup_error).__name__
                )
        raise
    db.refresh(document)
    return _document_view(db, document)


@router.post("", response_model=DocumentView, status_code=status.HTTP_201_CREATED)
def create_document(
    workspace_id: UUID,
    payload: DocumentCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DocumentView:
    _workspace_role(db, workspace_id, user.id, write=True)
    _project_in_workspace(db, workspace_id, payload.project_id)
    document = KnowledgeDocument(
        workspace_id=workspace_id,
        project_id=payload.project_id,
        owner_user_id=user.id,
        title=payload.title,
        content=payload.content,
        category=payload.category,
        tags=payload.tags,
        version_number=1,
    )
    db.add(document)
    db.flush()
    _add_version(db, document, user.id)
    _index_document(db, document)
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=document.project_id,
            actor_user_id=user.id,
            event_type="document.created",
            object_type="document",
            object_id=document.id,
            details_json={
                "project_id": str(payload.project_id) if payload.project_id else None,
                "title": document.title,
            },
        )
    )
    db.commit()
    db.refresh(document)
    return _document_view(db, document)


@router.get("/{document_id}", response_model=DocumentView)
def get_document(
    workspace_id: UUID,
    document_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DocumentView:
    _workspace_role(db, workspace_id, user.id, write=False)
    return _document_view(db, _document(db, workspace_id, document_id))


@router.patch("/{document_id}", response_model=DocumentView)
def update_document(
    workspace_id: UUID,
    document_id: UUID,
    payload: DocumentUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DocumentView:
    role = _workspace_role(db, workspace_id, user.id, write=True)
    document = _document(db, workspace_id, document_id)
    if not _can_edit_document(db, document, user.id, role):
        raise HTTPException(
            status_code=403, detail="Only the document owner or workspace owner can edit it"
        )
    changes = payload.model_dump(exclude_unset=True)
    if "project_id" in changes:
        _project_in_workspace(db, workspace_id, changes["project_id"])
    changed = {
        field: value for field, value in changes.items() if getattr(document, field) != value
    }
    if not changed:
        raise HTTPException(status_code=422, detail="No document values changed")
    for field, value in changed.items():
        setattr(document, field, value)
    document.version_number += 1
    db.flush()
    if "content" in changed:
        _index_document(db, document)
    _add_version(db, document, user.id)
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=document.project_id,
            actor_user_id=user.id,
            event_type="document.updated",
            object_type="document",
            object_id=document.id,
            details_json={
                "project_id": str(document.project_id) if document.project_id else None,
                "title": document.title,
                "changed_fields": sorted(changed),
            },
        )
    )
    db.commit()
    db.refresh(document)
    return _document_view(db, document)


@router.get("/{document_id}/file")
async def download_document_file(
    workspace_id: UUID,
    document_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    _workspace_role(db, workspace_id, user.id, write=False)
    _document(db, workspace_id, document_id)
    source = db.get(KnowledgeDocumentFile, document_id)
    if source is None or source.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Original file not found")
    content = source.file_content
    if source.storage_key:
        try:
            content = await run_in_threadpool(download_object, source.storage_key)
        except Exception as exc:
            logger.warning("document object download failed error_type=%s", type(exc).__name__)
            raise HTTPException(
                status_code=503, detail="File storage is temporarily unavailable."
            ) from exc
    if content is None:
        raise HTTPException(status_code=404, detail="Original file content is unavailable")
    return Response(
        content=content,
        media_type=source.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(source.original_filename)}"
        },
    )


@router.get("/{document_id}/versions", response_model=list[DocumentVersionView])
def list_document_versions(
    workspace_id: UUID,
    document_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[DocumentVersionView]:
    _workspace_role(db, workspace_id, user.id, write=False)
    _document(db, workspace_id, document_id)
    rows = db.execute(
        select(KnowledgeDocumentVersion, User.display_name)
        .join(User, User.id == KnowledgeDocumentVersion.created_by)
        .where(KnowledgeDocumentVersion.document_id == document_id)
        .order_by(KnowledgeDocumentVersion.version_number.desc())
    ).all()
    return [
        DocumentVersionView(
            id=version.id,
            document_id=version.document_id,
            version_number=version.version_number,
            snapshot=version.snapshot_json,
            created_by=version.created_by,
            created_by_name=display_name,
            created_at=version.created_at,
        )
        for version, display_name in rows
    ]


@router.get("/{document_id}/comments", response_model=list[DocumentCommentView])
def list_document_comments(
    workspace_id: UUID,
    document_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[DocumentCommentView]:
    _workspace_role(db, workspace_id, user.id, write=False)
    _document(db, workspace_id, document_id)
    rows = db.execute(
        select(KnowledgeDocumentComment, User.display_name)
        .join(User, User.id == KnowledgeDocumentComment.author_user_id)
        .where(
            KnowledgeDocumentComment.workspace_id == workspace_id,
            KnowledgeDocumentComment.document_id == document_id,
        )
        .order_by(KnowledgeDocumentComment.created_at, KnowledgeDocumentComment.id)
    ).all()
    return [
        DocumentCommentView(
            id=comment.id,
            document_id=comment.document_id,
            author_user_id=comment.author_user_id,
            author_display_name=display_name,
            body=comment.body,
            created_at=comment.created_at,
        )
        for comment, display_name in rows
    ]


@router.post(
    "/{document_id}/comments",
    response_model=DocumentCommentView,
    status_code=status.HTTP_201_CREATED,
)
def create_document_comment(
    workspace_id: UUID,
    document_id: UUID,
    payload: DocumentCommentCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DocumentCommentView:
    _workspace_role(db, workspace_id, user.id, write=True)
    document = _document(db, workspace_id, document_id)
    comment = KnowledgeDocumentComment(
        workspace_id=workspace_id,
        project_id=document.project_id,
        document_id=document_id,
        author_user_id=user.id,
        body=payload.body,
    )
    db.add(comment)
    db.flush()
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            project_id=document.project_id,
            actor_user_id=user.id,
            event_type="document.comment_created",
            object_type="document_comment",
            object_id=comment.id,
            details_json={
                "project_id": str(document.project_id) if document.project_id else None,
                "document_id": str(document_id),
            },
        )
    )
    db.commit()
    db.refresh(comment)
    return DocumentCommentView(
        id=comment.id,
        document_id=document_id,
        author_user_id=user.id,
        author_display_name=user.display_name,
        body=comment.body,
        created_at=comment.created_at,
    )
