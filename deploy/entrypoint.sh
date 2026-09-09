#!/bin/sh
# Container entrypoint. First argument selects the role:
#   api    (default) — apply migrations, then serve the API
#   worker           — Celery worker + beat (call dispatcher)
set -e

ROLE="${1:-api}"

echo "Waiting for database..."
python - <<'PY'
import sys
import time

from sqlalchemy import create_engine, text

from backend.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
for attempt in range(30):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        sys.exit(0)
    except Exception:
        time.sleep(2)
print("Database did not become available in time", file=sys.stderr)
sys.exit(1)
PY

case "$ROLE" in
  api)
    # Only the API applies migrations, so workers never race it.
    echo "Applying database migrations..."
    alembic -c database/alembic.ini upgrade head

    echo "Starting API..."
    exec uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers "${UVICORN_WORKERS:-2}"
    ;;
  worker)
    echo "Starting Celery worker with beat..."
    exec celery -A workers.celery_app worker --beat \
      --loglevel "${CELERY_LOG_LEVEL:-info}" \
      --concurrency "${CELERY_CONCURRENCY:-2}" \
      --schedule /tmp/celerybeat-schedule
    ;;
  *)
    echo "Unknown role: $ROLE (expected 'api' or 'worker')" >&2
    exit 1
    ;;
esac
