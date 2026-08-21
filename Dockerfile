# Dockerfile for Codex Policy Intelligence Engine
# Mirrors README native Ubuntu flow: python:3.10-slim + pg/neo drivers + app deps
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1

# System deps: build-essential/gcc/libpq-dev for psycopg2/torch, curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (mirrors production hardening; matches compose user 10001)
RUN adduser --disabled-password --gecos '' --uid 10001 appuser

WORKDIR /app

# Leverage layer cache: deps first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app (respects .dockerignore: .env, archive, models, logs, .git excluded)
COPY . .

# Runtime dirs bind-mounted in compose; ensure writable for non-root
RUN mkdir -p /app/logs /app/state /app/.state /app/archive && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000 8501

# Overridden per-service in docker-compose.yml (api/ui/worker)
CMD ["python", "-m", "uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
