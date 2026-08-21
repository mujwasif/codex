"""
Codex Policy Intelligence Engine — FastAPI application factory.

The app is assembled here; endpoint logic lives in the routers package
(services/api/routers/) and shared dependencies in services/api/dependencies.py.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import json

from services.ingestion.bm25_index import BM25Index
from services.api.routers import include_routers
from services.api import state
from packages.shared.config import CORS_ORIGINS

app = FastAPI(title="Codex Policy Intelligence Engine", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=json.loads(CORS_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    # Initialize connection pool for tool calling
    from services.agents.tools.connections import ConnectionPool

    ConnectionPool.initialize()

    # Load global BM25 index (shared with the admin router and orchestrator)
    try:
        bm25 = BM25Index()
        state.set_bm25_index(bm25)
        app.state.bm25_index = bm25
        print(f"✓ BM25 index loaded: {bm25.chunk_count} chunks")
    except Exception as e:
        state.set_bm25_index(None)
        app.state.bm25_index = None
        print(f"⚠️ BM25 index failed to load: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    from services.agents.tools.connections import ConnectionPool

    ConnectionPool.close()


include_routers(app)
