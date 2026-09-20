"""Object storage abstraction for call recordings (MVP sections 29, 33).

Same shape as the other provider interfaces. The default is not a throwaway
mock: `local` writes to a directory on the server, which is a working setup for
the single-host pilot. `s3` is there for when recordings outgrow one disk or
have to live in Azure Blob / S3 as the MVP stack anticipates.

Recordings are customer voice data. Nothing here makes a public URL — bytes are
only ever served back through an authenticated, role-checked endpoint that
writes an audit entry.
"""

from typing import Protocol


class StorageError(Exception):
    """Raised when an object cannot be stored or read back."""


class StorageProvider(Protocol):
    name: str

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """Store bytes under `key`, replacing anything already there."""

    def get(self, key: str) -> bytes:
        """Read the object back. Raises StorageError if it is not there."""

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None:
        """Remove the object. Succeeds if it was already gone."""
