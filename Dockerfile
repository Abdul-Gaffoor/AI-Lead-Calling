FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# postgresql-client supplies pg_dump, which the nightly backup task runs
# (MVP §32). Without it the task fails with a FileNotFoundError that reads
# like a bug rather than a missing package.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend backend
COPY workers workers
COPY database database
COPY frontend frontend
# The §37 corpus, so `backend.cli evaluate` works on the server too.
COPY evaluation evaluation
COPY knowledge knowledge
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN useradd --create-home --shell /usr/sbin/nologin appuser
USER appuser

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
