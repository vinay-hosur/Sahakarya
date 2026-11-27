import logging
import time
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from app.api.health import router as health_router
from app.api.v1.actions import router as actions_router
from app.api.v1.activity import router as activity_router
from app.api.v1.assistant import router as assistant_router
from app.api.v1.auth import router as auth_router
from app.api.v1.chat import router as chat_router
from app.api.v1.decisions import router as decisions_router
from app.api.v1.documents import router as documents_router
from app.api.v1.integrations import oauth_router as integrations_oauth_router
from app.api.v1.integrations import router as integrations_router
from app.api.v1.intelligence import router as intelligence_router
from app.api.v1.members import router as members_router
from app.api.v1.meta import router as meta_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.projects import router as projects_router
from app.api.v1.requests import router as requests_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.telegram import public_router as telegram_public_router
from app.api.v1.telegram import router as telegram_router
from app.api.v1.workspaces import router as workspaces_router
from app.core.config import settings
from app.core.request_context import request_id_context

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s request_id=%(request_id)s %(name)s %(message)s",
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = request_id_context.get()
        return True


for handler in logging.getLogger().handlers:
    handler.addFilter(RequestIdFilter())

logger = logging.getLogger("sahakarya.http")

app = FastAPI(
    title="SAHAKARYA API",
    description="Context-aware collaboration service for SAHAKARYA.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next) -> Response:
    supplied_id = request.headers.get("X-Request-ID")
    try:
        request_id = str(UUID(supplied_id)) if supplied_id else str(uuid4())
    except (ValueError, TypeError, AttributeError):
        request_id = str(uuid4())

    token = request_id_context.set(request_id)
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error("request failed error_type=%s", type(exc).__name__)
        raise
    finally:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        request_id_context.reset(token)

    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request completed method=%s path=%s status=%s duration_ms=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        extra={"request_id": request_id},
    )
    return response


app.include_router(health_router)
app.include_router(meta_router, prefix=settings.api_v1_prefix)
app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(workspaces_router, prefix=settings.api_v1_prefix)
app.include_router(projects_router, prefix=settings.api_v1_prefix)
app.include_router(intelligence_router, prefix=settings.api_v1_prefix)
app.include_router(tasks_router, prefix=settings.api_v1_prefix)
app.include_router(documents_router, prefix=settings.api_v1_prefix)
app.include_router(decisions_router, prefix=settings.api_v1_prefix)
app.include_router(activity_router, prefix=settings.api_v1_prefix)
app.include_router(assistant_router, prefix=settings.api_v1_prefix)
app.include_router(chat_router, prefix=settings.api_v1_prefix)
app.include_router(members_router, prefix=settings.api_v1_prefix)
app.include_router(notifications_router, prefix=settings.api_v1_prefix)
app.include_router(telegram_router, prefix=settings.api_v1_prefix)
app.include_router(telegram_public_router, prefix=settings.api_v1_prefix)
app.include_router(integrations_router, prefix=settings.api_v1_prefix)
app.include_router(integrations_oauth_router, prefix=settings.api_v1_prefix)
app.include_router(requests_router, prefix=settings.api_v1_prefix)
app.include_router(actions_router, prefix=settings.api_v1_prefix)
