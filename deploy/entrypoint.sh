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
last_error = None
for attempt in range(30):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        sys.exit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(2)

# Say WHY. "Did not become available" alone cannot distinguish a database that
# is still booting from a wrong password or an unreachable host, which turns a
# two-minute fix into a guessing game. SQLAlchemy masks the password in a URL
# repr and the driver's error text does not contain it, so this is safe to log.
print("Database did not become available in time.", file=sys.stderr)
print(f"  URL:   {engine.url.render_as_string(hide_password=True)}", file=sys.stderr)
print(f"  Error: {type(last_error).__name__}: {last_error}", file=sys.stderr)
sys.exit(1)
PY

case "$ROLE" in
  api)
    # Only the API applies migrations, so workers never race it.
    echo "Applying database migrations..."
    alembic -c database/alembic.ini upgrade head

    echo "Starting API..."
    # --forwarded-allow-ips lets uvicorn honour the X-Forwarded-* headers set by
    # the TLS terminator, so FastAPI sees https:// and the customer's address.
    # The default keeps local runs (no proxy) from trusting arbitrary senders.
    exec uvicorn backend.main:app --host 0.0.0.0 --port 8000 \
      --workers "${UVICORN_WORKERS:-2}" \
      --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
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
