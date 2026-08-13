"""
Centralized service configuration.

Reads connection URLs from environment variables with sensible defaults
for local development. In Docker, compose sets these to container names.
"""
import os

# PostgreSQL
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://codex_admin:securepassword123@localhost:5432/codex_db"
)

# Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")

# llama-server (Qwen3-8B on port 8080)
LLAMA_8B_URL = os.getenv("LLAMA_8B_URL", "http://localhost:8080")

# llama-server (Qwen3-4B on port 8081)
LLAMA_4B_URL = os.getenv("LLAMA_4B_URL", "http://localhost:8081")
