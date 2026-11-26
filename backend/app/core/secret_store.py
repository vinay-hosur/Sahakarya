from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class SecretStoreUnavailable(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = settings.integration_encryption_key
    if not key:
        raise SecretStoreUnavailable(
            "Configure INTEGRATION_ENCRYPTION_KEY before saving connector credentials"
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise SecretStoreUnavailable("INTEGRATION_ENCRYPTION_KEY is invalid") from exc


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError) as exc:
        raise SecretStoreUnavailable(
            "Stored integration credential could not be decrypted"
        ) from exc
