from __future__ import annotations

from functools import lru_cache
from io import BytesIO

import boto3
from botocore.config import Config

from app.core.config import settings


@lru_cache(maxsize=1)
def _client():
    if not settings.object_storage_configured:
        raise RuntimeError("Object storage is not configured")
    assert settings.file_storage_secret_access_key is not None
    return boto3.client(
        "s3",
        endpoint_url=settings.file_storage_endpoint,
        aws_access_key_id=settings.file_storage_access_key_id,
        aws_secret_access_key=settings.file_storage_secret_access_key.get_secret_value(),
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def upload_object(key: str, content: bytes, content_type: str) -> None:
    stream = BytesIO(content)
    _client().upload_fileobj(
        stream,
        settings.file_storage_bucket,
        key,
        ExtraArgs={"ContentType": content_type},
    )


def download_object(key: str) -> bytes:
    response = _client().get_object(Bucket=settings.file_storage_bucket, Key=key)
    try:
        return response["Body"].read()
    finally:
        response["Body"].close()


def delete_object(key: str) -> None:
    _client().delete_object(Bucket=settings.file_storage_bucket, Key=key)
