from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["system"])


@router.get("/meta", summary="Describe this SAHAKARYA API instance")
def meta() -> dict[str, object]:
    providers = settings.reasoning_providers
    return {
        "name": settings.app_name,
        "api_version": "v1",
        "environment": settings.app_env,
        "reasoning_provider": settings.active_reasoning_provider,
        "reasoning_model": next(
            (
                provider["model"]
                for provider in providers
                if provider["id"] == settings.active_reasoning_provider
            ),
            None,
        ),
        "reasoning_configured": settings.reasoning_configured,
        "reasoning_providers": providers,
        "slack_oauth_configured": settings.slack_oauth_configured,
        "object_storage_configured": settings.object_storage_configured,
        "max_upload_bytes": 50 * 1024 * 1024,
        "database_upload_limit_bytes": 5 * 1024 * 1024,
        "invitation_email_configured": settings.invitation_email_configured,
    }
