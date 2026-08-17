"""
Centralized configuration — single source of truth.

Reads from environment variables with sensible defaults for local development.
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _path_env(name: str, default: Path) -> str:
    return str(Path(os.getenv(name, str(default))).expanduser())

# ── Database ──────────────────────────────────────────────
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "codex_admin")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "password123")
POSTGRES_DB = os.getenv("POSTGRES_DB", "codex_db")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}",
)
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))

# ── Neo4j ─────────────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS", "")
if not NEO4J_PASS and os.getenv("NEO4J_AUTH", "").startswith("neo4j/"):
    NEO4J_PASS = os.getenv("NEO4J_AUTH", "").split("/", 1)[1]

# ── LLM Provider ──────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", ""))
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_INGESTION_MODEL = os.getenv("LLM_INGESTION_MODEL", LLM_MODEL)


def get_llm_provider() -> str:
    """Resolve auto mode to hosted API when a key exists, otherwise local."""
    if LLM_PROVIDER == "auto":
        return "api" if LLM_API_KEY else "local"
    return LLM_PROVIDER

# ── Local LLM Servers (used when LLM_PROVIDER=local) ──────
LLAMA_8B_URL = os.getenv("LLAMA_8B_URL", "http://127.0.0.1:8080")
LLAMA_4B_URL = os.getenv("LLAMA_4B_URL", "http://127.0.0.1:8081")
QWEN3_8B_MODEL = os.getenv("QWEN3_8B_MODEL", "Qwen3-8B-Q4_K_M.gguf")
QWEN3_4B_MODEL = os.getenv(
    "QWEN3_4B_MODEL", "Qwen3-4B-Instruct-2507-UD-Q4_K_XL.gguf"
)

# ── API ───────────────────────────────────────────────────
CODEX_API_URL = os.getenv("CODEX_API_URL", "http://127.0.0.1:8000")
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
HISTORY_MAX_TOKENS = int(os.getenv("HISTORY_MAX_TOKENS", "2500"))

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
CODEX_DIR = _path_env("CODEX_DIR", PROJECT_ROOT)
VENV_DIR = _path_env("VENV_DIR", PROJECT_ROOT / ".venv")
MODELS_DIR = _path_env("MODELS_DIR", PROJECT_ROOT / "models")
ARCHIVE_DIR = _path_env("ARCHIVE_DIR", PROJECT_ROOT / "archive")
DATA_DIR = _path_env("DATA_DIR", PROJECT_ROOT / ".data")
LOG_DIR = _path_env("LOG_DIR", PROJECT_ROOT / "logs")
STATE_DIR = _path_env("STATE_DIR", PROJECT_ROOT / ".state")
WORKER_LOCK_PATH = _path_env("WORKER_LOCK_PATH", Path(STATE_DIR) / "ingestion_worker.pid")
MIGRATION_STATE_FILE = _path_env("MIGRATION_STATE_FILE", Path(STATE_DIR) / "processed_docs.txt")
LLAMA_CPP_BIN = os.getenv("LLAMA_CPP_BIN", "llama-server")
PG_BIN = os.getenv("PG_BIN", "")
PG_DATA = _path_env("PG_DATA", Path(DATA_DIR) / "postgres")
NEO4J_HOME = os.getenv("NEO4J_HOME", "")
