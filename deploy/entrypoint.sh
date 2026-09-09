#!/bin/sh
# Container entrypoint: wait for the database, apply migrations, start the API.
set -e

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

echo "Applying database migrations..."
alembic -c database/alembic.ini upgrade head

echo "Starting API..."
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers "${UVICORN_WORKERS:-2}"
