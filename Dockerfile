# Codex Policy Intelligence Engine - Docker Image
# Python packages are mounted from the host venv at runtime (not copied at build time).
FROM python:3.12-slim

# Install system dependencies for pgvector, psycopg2, and llama.cpp build
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    cmake \
    git \
    && rm -rf /var/lib/apt/lists/*

# Build llama.cpp for the llama-server binary
RUN git clone --depth 1 https://github.com/ggerganov/llama.cpp.git /tmp/llama.cpp \
    && cd /tmp/llama.cpp \
    && cmake -B build -DLLAMA_CURL=OFF \
    && cmake --build build --config Release -j$(nproc) \
    && cp build/bin/llama-server /usr/local/bin/llama-server \
    && cp build/bin/lib*.so* /usr/local/lib/ \
    && ldconfig \
    && rm -rf /tmp/llama.cpp

WORKDIR /app

# Copy project code
COPY codex/ /app/

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python3", "-m", "uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
