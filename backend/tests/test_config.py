from pydantic import SecretStr

from app.core.config import Settings


def test_managed_postgres_urls_use_psycopg_three() -> None:
    assert (
        Settings(database_url="postgres://user:pass@host/db").sqlalchemy_database_url
        == "postgresql+psycopg://user:pass@host/db"
    )
    assert (
        Settings(database_url="postgresql://user:pass@host/db").sqlalchemy_database_url
        == "postgresql+psycopg://user:pass@host/db"
    )


def test_explicit_database_drivers_are_preserved() -> None:
    url = "postgresql+psycopg://user:pass@host/db"
    assert Settings(database_url=url).sqlalchemy_database_url == url
    assert Settings(database_url="sqlite://").sqlalchemy_database_url == "sqlite://"


def test_slack_callback_defaults_to_frontend_proxy_path() -> None:
    settings = Settings(
        slack_frontend_url="https://sahakarya.example.test/",
        slack_client_id="client-id",
        slack_client_secret=SecretStr("client-secret"),
    )
    assert (
        settings.slack_callback_uri
        == "https://sahakarya.example.test/api/v1/integrations/slack/oauth/callback"
    )
    assert settings.slack_oauth_configured is True


def test_reasoning_providers_require_key_and_model_and_support_xai() -> None:
    settings = Settings(
        gemini_api_key=SecretStr("gemini-secret"),
        gemini_model="gemini-3.1-flash-lite",
        xai_api_key=SecretStr("xai-secret"),
        xai_model="grok-4.3",
        default_reasoning_provider="gemini",
        _env_file=None,
    )

    assert settings.reasoning_providers == [
        {"id": "gemini", "label": "Google Gemini", "model": "gemini-3.1-flash-lite"},
        {"id": "xai", "label": "xAI Grok", "model": "grok-4.3"},
    ]
    assert settings.active_reasoning_provider == "gemini"
    assert settings.reasoning_credentials("xai") == ("xai-secret", "grok-4.3")

    key_without_model = Settings(xai_api_key=SecretStr("xai-secret"), _env_file=None)
    assert key_without_model.reasoning_configured is False


def test_object_storage_requires_complete_https_credentials() -> None:
    settings = Settings(
        file_storage_endpoint="https://account.r2.cloudflarestorage.com",
        file_storage_bucket="sahakarya-files",
        file_storage_access_key_id="access-id",
        file_storage_secret_access_key=SecretStr("secret-value"),
        _env_file=None,
    )
    assert settings.object_storage_configured is True
    assert (
        Settings(
            file_storage_endpoint="http://insecure.example.test",
            file_storage_bucket="sahakarya-files",
            file_storage_access_key_id="access-id",
            file_storage_secret_access_key=SecretStr("secret-value"),
            _env_file=None,
        ).object_storage_configured
        is False
    )


def test_invitation_mail_requires_key_and_sender() -> None:
    settings = Settings(
        resend_api_key=SecretStr("resend-secret"),
        invitation_from_email="SAHAKARYA <team@example.test>",
        public_app_url="https://sahakarya.example.test",
        _env_file=None,
    )
    assert settings.invitation_email_configured is True
    assert settings.app_public_url == "https://sahakarya.example.test"
