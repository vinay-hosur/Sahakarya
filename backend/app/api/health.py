from fastapi import APIRouter, HTTPException, status

from app.core.config import settings
from app.db.session import check_database

router = APIRouter(tags=["health"])


@router.get("/health/live", summary="Check whether the API process is running")
def live() -> dict[str, str]:
    return {"status": "live", "service": settings.app_name}


@router.get("/health/ready", summary="Check whether required runtime dependencies are ready")
def ready() -> dict[str, object]:
    if not check_database():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "dependencies": {"database": "unavailable"}},
        )
    return {"status": "ready", "dependencies": {"database": "available"}}
