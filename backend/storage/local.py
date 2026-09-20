"""Recordings on the server's own disk."""

import pathlib

from backend.storage.base import StorageError


class LocalStorageProvider:
    """Files under a directory, one file per object key.

    Good enough for the pilot on a single host: point the directory at a Docker
    volume and recordings survive a redeploy like the database does.
    """

    name = "local"

    def __init__(self, root: str):
        self._root = pathlib.Path(root)

    def _path(self, key: str) -> pathlib.Path:
        # Keys are built by this application, never by a customer, but a
        # traversal here would write anywhere the process can reach — so it is
        # checked rather than trusted.
        candidate = (self._root / key).resolve()
        root = self._root.resolve()
        if not candidate.is_relative_to(root):
            raise StorageError(f"Refusing to use a key that escapes the store: {key!r}")
        return candidate

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary name and move it into place, so a crash or a
        # concurrent read never sees a half-written recording.
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(data)
        temporary.replace(path)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"No stored object for {key!r}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()
