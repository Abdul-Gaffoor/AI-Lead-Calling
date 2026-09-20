"""Load curated knowledge files from the `knowledge/` directory.

Files are markdown with a YAML front-matter block:

    ---
    title: How net metering works
    category: net-metering
    service: RESIDENTIAL_SOLAR      # optional
    source: Swaraj sales handbook   # optional
    ---

Loading a file never approves it. MVP section 18 puts approved company
information in production RAG and nothing else, so a freshly loaded document
is inert until a curator approves it in the console or over the API.
"""

import logging
import pathlib

import yaml
from sqlalchemy.orm import Session

from backend.knowledge.service import KnowledgeError, upsert_document
from backend.leads.models import ServiceType

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = pathlib.Path(__file__).resolve().parents[2] / "knowledge"


def parse_file(path: pathlib.Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise KnowledgeError(f"{path.name} has no front-matter block")

    _, _, rest = raw.partition("---")
    front, separator, body = rest.partition("\n---")
    if not separator:
        raise KnowledgeError(f"{path.name} has an unterminated front-matter block")

    meta = yaml.safe_load(front) or {}
    if "title" not in meta or "category" not in meta:
        raise KnowledgeError(f"{path.name} needs both title and category")

    service = meta.get("service")
    return {
        "slug": meta.get("slug") or path.stem,
        "title": meta["title"],
        "category": meta["category"],
        "service": ServiceType(service) if service else None,
        "source": meta.get("source"),
        "language": meta.get("language", "en"),
        "body": body.strip(),
    }


def load_directory(db: Session, directory: pathlib.Path | None = None) -> dict:
    """Ingest every markdown file in `knowledge/`. Returns a summary."""
    directory = directory or KNOWLEDGE_DIR
    if not directory.is_dir():
        return {"created": 0, "updated": 0, "failed": 0, "files": 0}

    created = updated = failed = 0
    # README.md documents the directory for people, not the AI.
    files = [path for path in sorted(directory.rglob("*.md"))
             if path.name.lower() != "readme.md"]
    for path in files:
        try:
            fields = parse_file(path)
            _document, was_created = upsert_document(db, **fields)
        except Exception as exc:
            failed += 1
            logger.error("Could not load knowledge file %s: %s", path.name, exc)
            continue
        created += int(was_created)
        updated += int(not was_created)

    return {"created": created, "updated": updated, "failed": failed, "files": len(files)}
