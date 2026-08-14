"""
Centralized configuration — single source of truth.

Reads from environment variables with sensible defaults for local dev.
In Docker, docker-compose.yaml sets these to container names.
"""
import os

# ── Database ──────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://codex_admin:password123@localhost:5432/codex_db",
)
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))

# ── Neo4j ─────────────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")

# ── LLM Servers ───────────────────────────────────────────
LLAMA_8B_URL = os.getenv("LLAMA_8B_URL", "http://localhost:8080")
LLAMA_4B_URL = os.getenv("LLAMA_4B_URL", "http://localhost:8081")
QWEN3_8B_MODEL = os.getenv("QWEN3_8B_MODEL", "Qwen3-8B-Q4_K_M.gguf")
QWEN3_4B_MODEL = os.getenv(
    "QWEN3_4B_MODEL", "Qwen3-4B-Instruct-2507-UD-Q4_K_XL.gguf"
)

# ── API ───────────────────────────────────────────────────
CODEX_API_URL = os.getenv("CODEX_API_URL", "http://localhost:8000")
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-to-a-random-secure-string")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
TOKEN_EXPIRE_MINUTES = int(os.getenv("TOKEN_EXPIRE_MINUTES", "30"))
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS", '["http://localhost:8501","http://127.0.0.1:8501"]'
)

# ── UI ────────────────────────────────────────────────────
UI_PORT = int(os.getenv("UI_PORT", "8501"))

# ── Embedding Models ──────────────────────────────────────
RETRIEVER_MODEL = os.getenv("RETRIEVER_MODEL", "BAAI/bge-large-en-v1.5")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-base")
RETRIEVER_DEVICE = os.getenv("RETRIEVER_DEVICE", "cpu")
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", "cpu")
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")

# ── Search Tuning ─────────────────────────────────────────
TOP_K_RETRIEVAL = int(os.getenv("TOP_K_RETRIEVAL", "20"))
TOP_K_FINAL = int(os.getenv("TOP_K_FINAL", "5"))
RRF_K = int(os.getenv("RRF_K", "60"))
DEFAULT_SEARCH_MODE = os.getenv("DEFAULT_SEARCH_MODE", "hybrid")
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "5"))

# ── Ingestion ─────────────────────────────────────────────
MAX_CLAUSE_TOKENS = int(os.getenv("MAX_CLAUSE_TOKENS", "200"))
OVERLAP_TOKENS = int(os.getenv("OVERLAP_TOKENS", "20"))
USE_LLM_FOR_CHUNKING = os.getenv("USE_LLM_FOR_CHUNKING", "true").lower() == "true"
GRAPH_BATCH_SIZE = int(os.getenv("GRAPH_BATCH_SIZE", "30"))
CLAIM_INTERVAL = int(os.getenv("CLAIM_INTERVAL", "3"))

# ── Slack ─────────────────────────────────────────────────
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_DEFAULT_USERNAME = os.getenv("SLACK_DEFAULT_USERNAME", "employee")
SLACK_QUERY_TIMEOUT = int(os.getenv("SLACK_QUERY_TIMEOUT", "120"))

# ── Infrastructure Paths ──────────────────────────────────
CODEX_DIR = os.getenv("CODEX_DIR", "/home/mujtaba/new_folder/codex")
VENV_DIR = os.getenv("VENV_DIR", "/home/mujtaba/new_folder/fastmcp/venv")
MODELS_DIR = os.getenv("MODELS_DIR", "/home/mujtaba/models")
