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
