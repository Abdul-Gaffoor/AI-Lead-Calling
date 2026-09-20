from functools import lru_cache

from backend.core.config import settings
from backend.knowledge.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    MockEmbeddingProvider,
    VoyageEmbeddingProvider,
)


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    name = settings.embedding_provider.lower()
    if name == "mock":
        return MockEmbeddingProvider()
    if name == "voyage":
        return VoyageEmbeddingProvider()
    raise EmbeddingError(f"Unknown embedding provider: {settings.embedding_provider}")


def reset_embedding_provider_cache() -> None:
    """Drop the cached provider (used by tests and after config changes)."""
    get_embedding_provider.cache_clear()
