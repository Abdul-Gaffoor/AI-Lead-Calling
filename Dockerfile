FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend backend
COPY workers workers
COPY database database
COPY frontend frontend
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN useradd --create-home --shell /usr/sbin/nologin appuser
USER appuser

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
