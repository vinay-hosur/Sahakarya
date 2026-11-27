from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SAHAKARYA API"
    app_env: str = "development"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://sahakarya:sahakarya@127.0.0.1:5432/sahakarya"
    db_connect_timeout_seconds: int = 1
    cors_origins: str = "http://localhost:5173"
    session_cookie_name: str = "sahakarya_session"
    session_ttl_days: int = 14
    session_cookie_secure: bool | None = None
    integration_encryption_key: str | None = None
    file_storage_endpoint: str | None = None
    file_storage_bucket: str | None = None
    file_storage_access_key_id: str | None = None
    file_storage_secret_access_key: SecretStr | None = None
    resend_api_key: SecretStr | None = None
    invitation_from_email: str | None = None
    public_app_url: str | None = None
    public_api_url: str | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str | None = None
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    groq_api_key: SecretStr | None = None
    groq_model: str | None = None
    xai_api_key: SecretStr | None = None
    xai_model: str | None = None
    default_reasoning_provider: str | None = None
    reasoning_connect_timeout_seconds: int = 10
    reasoning_read_timeout_seconds: int = 60
    slack_client_id: str | None = None
    slack_client_secret: SecretStr | None = None
    slack_redirect_uri: str | None = None
    slack_frontend_url: str = "http://localhost:5173"
    slack_user_scopes: str = (
        "search:read.public,search:read.private,search:read.mpim,search:read.im,"
        "channels:history,groups:history,mpim:history,im:history"
    )

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def sqlalchemy_database_url(self) -> str:
        """Use the installed psycopg 3 driver with platform Postgres URLs."""
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+psycopg://", 1)
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self.database_url

    @property
    def secure_session_cookie(self) -> bool:
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.app_env.lower() not in {"development", "test"}

    @property
    def reasoning_configured(self) -> bool:
        return bool(self.reasoning_providers)

    @property
    def slack_oauth_configured(self) -> bool:
        return bool(
            self.slack_client_id
            and self.slack_client_id.strip()
            and self.slack_client_secret
            and self.slack_client_secret.get_secret_value().strip()
            and self.slack_callback_uri.startswith("https://")
        )

    @property
    def slack_callback_uri(self) -> str:
        configured = (self.slack_redirect_uri or "").strip()
        if configured:
            return configured
        return f"{self.slack_frontend_url.rstrip('/')}/api/v1/integrations/slack/oauth/callback"

    @property
    def object_storage_configured(self) -> bool:
        return bool(
            self.file_storage_endpoint
            and self.file_storage_endpoint.startswith("https://")
            and self.file_storage_bucket
            and self.file_storage_bucket.strip()
            and self.file_storage_access_key_id
            and self.file_storage_access_key_id.strip()
            and self.file_storage_secret_access_key
            and self.file_storage_secret_access_key.get_secret_value().strip()
        )

    @property
    def invitation_email_configured(self) -> bool:
        return bool(
            self.resend_api_key
            and self.resend_api_key.get_secret_value().strip()
            and self.invitation_from_email
            and self.invitation_from_email.strip()
        )

    @property
    def app_public_url(self) -> str:
        return (self.public_app_url or self.slack_frontend_url).rstrip("/")

    @property
    def reasoning_providers(self) -> list[dict[str, str]]:
        configured = (
            ("openai", "OpenAI", self.openai_api_key, self.openai_model),
            ("anthropic", "Anthropic", self.anthropic_api_key, self.anthropic_model),
            ("gemini", "Google Gemini", self.gemini_api_key, self.gemini_model),
            ("groq", "Groq", self.groq_api_key, self.groq_model),
            ("xai", "xAI Grok", self.xai_api_key, self.xai_model),
        )
        return [
            {"id": provider_id, "label": label, "model": model.strip()}
            for provider_id, label, api_key, model in configured
            if api_key and api_key.get_secret_value().strip() and model and model.strip()
        ]

    @property
    def active_reasoning_provider(self) -> str | None:
        providers = self.reasoning_providers
        available = {provider["id"] for provider in providers}
        if self.default_reasoning_provider in available:
            return self.default_reasoning_provider
        return providers[0]["id"] if providers else None

    def reasoning_credentials(self, provider: str | None = None) -> tuple[str, str] | None:
        provider_id = provider or self.active_reasoning_provider
        fields = {
            "openai": (self.openai_api_key, self.openai_model),
            "anthropic": (self.anthropic_api_key, self.anthropic_model),
            "gemini": (self.gemini_api_key, self.gemini_model),
            "groq": (self.groq_api_key, self.groq_model),
            "xai": (self.xai_api_key, self.xai_model),
        }
        values = fields.get(provider_id or "")
        if not values:
            return None
        api_key, model = values
        if not api_key or not api_key.get_secret_value().strip() or not model or not model.strip():
            return None
        return api_key.get_secret_value().strip(), model.strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
