"""
Shared API state.

The global BM25 index lives here instead of in main.py so that the
orchestrator (services/agents) can read it without importing the app module.
"""

bm25_index = None


def set_bm25_index(index):
    """Set the global BM25 index (called from FastAPI startup)."""
    global bm25_index
    bm25_index = index


def get_bm25_index():
    """Get the global BM25 index (may be None if not yet loaded)."""
    return bm25_index
