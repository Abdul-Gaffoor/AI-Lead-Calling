"""Database backups (MVP section 32).

`pg_dump` to the same object storage the recordings use, so a deployment
that has already configured S3 gets off-host backups with no extra moving
parts, and one that has not gets them on a volume — which still survives
the container being replaced, which is the common failure.

Deliberate choices:

* **Custom format (`-Fc`), not plain SQL.** It is compressed, and
  `pg_restore` can take one table out of it. A plain dump of this database
  would be mostly recordings metadata and knowledge embeddings.
* **The dump is streamed to a temporary file, not held in memory.** This
  runs on a 4 GB server beside the app.
* **Retention is enforced on the way out**, so a backup job that has been
  running for a year has not quietly filled the disk.
* **The password goes in the environment, never the command line.**
  Anything on the command line is visible in `ps` to every process on the
  host.

This is a backup, not a disaster-recovery plan. It does not test restores,
and a backup nobody has restored is a hypothesis. `docs/DEPLOYMENT.md`
says how to exercise one.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from backend.core.config import settings
from backend.storage.factory import get_storage_provider

logger = logging.getLogger(__name__)

#: Where backups live inside the storage provider.
PREFIX = "backups"


class BackupError(Exception):
    """Raised when a backup could not be taken or stored."""


@dataclass
class BackupResult:
    key: str
    size_bytes: int
    took_seconds: float
    pruned: int = 0


def _connection_parts() -> dict:
    """Split the configured DSN into pg_dump arguments."""
    url = urlparse(settings.database_url.replace("postgresql+psycopg2://", "postgresql://"))
    if url.scheme not in ("postgresql", "postgres"):
        raise BackupError(
            f"Backups need PostgreSQL; this deployment is on {url.scheme!r}"
        )
    return {
        "host": url.hostname or "localhost",
        "port": str(url.port or 5432),
        "user": url.username or "postgres",
        "password": url.password or "",
        "database": (url.path or "/").lstrip("/"),
    }


def run_backup(*, now: dt.datetime | None = None) -> BackupResult:
    """Dump the database and store it. Returns where it went."""
    started = dt.datetime.now(dt.timezone.utc)
    stamp = (now or started).strftime("%Y%m%dT%H%M%SZ")
    parts = _connection_parts()
    key = f"{PREFIX}/{parts['database']}-{stamp}.dump"

    environment = dict(os.environ)
    if parts["password"]:
        # Never on the command line: ps would show it to every process.
        environment["PGPASSWORD"] = parts["password"]

    with tempfile.TemporaryDirectory() as workspace:
        path = Path(workspace) / "dump"
        command = [
            "pg_dump",
            "--host", parts["host"],
            "--port", parts["port"],
            "--username", parts["user"],
            "--format", "custom",
            "--no-owner",
            "--no-privileges",
            "--file", str(path),
            parts["database"],
        ]
        try:
            completed = subprocess.run(
                command, env=environment, capture_output=True, timeout=1800, check=False
            )
        except FileNotFoundError as exc:
            raise BackupError(
                "pg_dump is not installed in this image. Add postgresql-client "
                "to the Dockerfile."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise BackupError("pg_dump timed out after 30 minutes") from exc

        if completed.returncode != 0:
            # stderr can carry the connection string; it is already masked
            # by the logging filter, but keep the message short regardless.
            detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
            raise BackupError(f"pg_dump failed: {detail[-1] if detail else 'unknown error'}")

        payload = path.read_bytes()

    if not payload:
        raise BackupError("pg_dump produced an empty file")

    storage = get_storage_provider()
    storage.put(key, payload, content_type="application/octet-stream")

    took = (dt.datetime.now(dt.timezone.utc) - started).total_seconds()
    pruned = prune(now=now)
    logger.info(
        "Database backup stored: %s (%.1f MB, %.1fs, %d pruned)",
        key, len(payload) / 1_048_576, took, pruned,
    )
    return BackupResult(key=key, size_bytes=len(payload), took_seconds=took, pruned=pruned)


def prune(*, now: dt.datetime | None = None) -> int:
    """Delete backups older than the retention period. Returns how many."""
    days = settings.backup_retention_days
    if days <= 0:
        return 0

    cutoff = (now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=days)
    storage = get_storage_provider()
    removed = 0

    for key in storage.list(PREFIX):
        stamp = _stamp_of(key)
        if stamp is None:
            # Something else is in the backups prefix. Leaving an unknown
            # file alone is the safer mistake.
            logger.debug("Skipping unrecognised backup key %s", key)
            continue
        if stamp < cutoff:
            try:
                storage.delete(key)
                removed += 1
            except Exception:
                logger.warning("Could not delete old backup %s", key, exc_info=True)
    return removed


def _stamp_of(key: str) -> dt.datetime | None:
    """Read the timestamp back out of a backup's name."""
    name = key.rsplit("/", 1)[-1]
    if not name.endswith(".dump") or "-" not in name:
        return None
    stamp = name[: -len(".dump")].rsplit("-", 1)[-1]
    try:
        return dt.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        return None
