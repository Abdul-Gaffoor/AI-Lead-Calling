"""Recordings in S3 (or any S3-compatible store, including Azure via a gateway)."""

from backend.core.config import settings
from backend.storage.base import StorageError


class S3StorageProvider:
    """Bucket-backed storage. Objects stay private; nothing is ever made public."""

    name = "s3"

    def __init__(self) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise StorageError(
                "STORAGE_PROVIDER=s3 needs boto3: pip install boto3"
            ) from exc

        if not settings.s3_bucket:
            raise StorageError("S3_BUCKET is not set")

        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region or None,
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key or None,
        )

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )
        except Exception as exc:
            raise StorageError(f"Could not store {key!r}: {exc}") from exc

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        except Exception as exc:
            raise StorageError(f"Could not read {key!r}: {exc}") from exc

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        try:
            # Paginated: a year of daily backups is small, but a recordings
            # prefix is not, and a truncated listing would silently skip
            # the objects an expiry pass is meant to remove.
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                keys += [item["Key"] for item in page.get("Contents", [])]
        except Exception as exc:  # noqa: BLE001 - surfaced as StorageError
            raise StorageError(f"Could not list {prefix!r}: {exc}") from exc
        return keys

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            raise StorageError(f"Could not delete {key!r}: {exc}") from exc
