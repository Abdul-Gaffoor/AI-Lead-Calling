"""Embedding providers for the knowledge base (MVP sections 10 and 18).

Same shape as the speech, language and voice providers: an interface with a
mock default that makes no network calls and costs nothing, and a real
implementation switched on by environment variable.

The mock is not a stub that returns noise. It is a hashed bag-of-words, so
retrieval genuinely works offline — a question about subsidy finds the subsidy
document because they share words. What it cannot do is match meaning across
different words ("savings" will not find "payback"), which is exactly what a
real embedding model is for. Treat mock retrieval as keyword search.
"""

import hashlib
import math
import re
from typing import Protocol

from backend.core.config import settings

#: Latin letters, digits, and the Telugu block — so Telugu questions tokenize
#: into words rather than one undifferentiated blob.
_TOKEN = re.compile(r"[a-z0-9ఀ-౿]+")

MOCK_DIMENSIONS = 256


class EmbeddingError(Exception):
    """Raised when embeddings cannot be produced."""


class EmbeddingProvider(Protocol):
    name: str
    #: Identifies the vector space. Vectors from two different models are not
    #: comparable, so this is stored with every chunk and filtered on at search.
    model: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed knowledge chunks for storage."""

    def embed_query(self, text: str) -> list[float]:
        """Embed a customer question for retrieval."""


def tokenize(text: str) -> list[str]:
    """Words, with English plurals folded onto the singular.

    Crude on purpose — it only exists so the offline mock does not treat
    "panels" and "panel" as unrelated words, which made keyword retrieval miss
    obvious matches. A real embedding model needs none of this.
    """
    tokens = []
    for token in _TOKEN.findall(text.lower()):
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.append(token)
    return tokens


def _normalize(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length == 0:
        return vector
    return [value / length for value in vector]


class MockEmbeddingProvider:
    """Hashed bag-of-words. Deterministic, offline, free."""

    name = "mock"
    model = "mock-hash-v1"
    dimensions = MOCK_DIMENSIONS

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest, "big") % self.dimensions
            # Sublinear term frequency: a word repeated ten times is not ten
            # times as important, which keeps long documents from dominating.
            vector[bucket] += 1.0
        return _normalize([math.log1p(value) for value in vector])

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class VoyageEmbeddingProvider:
    """Voyage AI embeddings — multilingual, so Telugu questions work.

    Chosen because the LLM here is Claude and Voyage is the embedding model
    Anthropic points at; it is one class behind the interface, so swapping it
    for an Indic specialist is a config change.
    """

    name = "voyage"

    def __init__(self) -> None:
        if not settings.voyage_api_key:
            raise EmbeddingError("VOYAGE_API_KEY is not set")
        self.model = settings.embedding_model
        self._endpoint = "https://api.voyageai.com/v1/embeddings"

    def _call(self, texts: list[str], input_type: str) -> list[list[float]]:
        import httpx

        try:
            response = httpx.post(
                self._endpoint,
                headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                json={"model": self.model, "input": texts, "input_type": input_type},
                timeout=settings.embedding_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # network, auth, rate limit, malformed body
            raise EmbeddingError(f"Voyage embedding request failed: {exc}") from exc

        try:
            # The API does not promise input order, so sort by index.
            rows = sorted(payload["data"], key=lambda row: row["index"])
            return [row["embedding"] for row in rows]
        except (KeyError, TypeError) as exc:
            raise EmbeddingError(f"Unexpected Voyage response shape: {exc}") from exc

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._call(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        return self._call([text], "query")[0]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine of the angle between two vectors, 0.0 when either has no length.

    Used directly on SQLite; PostgreSQL does the same thing with pgvector's
    `<=>` operator, which returns cosine *distance* (1 - similarity).
    """
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_len = math.sqrt(sum(a * a for a in left))
    right_len = math.sqrt(sum(b * b for b in right))
    if left_len == 0 or right_len == 0:
        return 0.0
    return dot / (left_len * right_len)
