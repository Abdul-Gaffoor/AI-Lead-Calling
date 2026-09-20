from functools import lru_cache

from backend.core.config import settings
from backend.storage.base import StorageError, StorageProvider
from backend.storage.local import LocalStorageProvider


@lru_cache(maxsize=1)
def get_storage_provider() -> StorageProvider:
    name = settings.storage_provider.lower()
    if name == "local":
        return LocalStorageProvider(settings.storage_dir)
    if name == "s3":
        from backend.storage.s3 import S3StorageProvider

        return S3StorageProvider()
    raise StorageError(f"Unknown storage provider: {settings.storage_provider}")


def reset_storage_provider_cache() -> None:
    """Drop the cached provider (used by tests and after config changes)."""
    get_storage_provider.cache_clear()
