"""
Router inclusion helper.
"""

from fastapi import FastAPI

from .auth import router as auth_router
from .query import router as query_router
from .documents import router as documents_router
from .admin import router as admin_router
from .health import router as health_router
from .integrations import router as integrations_router
from .users import router as users_router


def include_routers(app: FastAPI, bm25_index=None):
    """Register all routers with the FastAPI app.

    Args:
        app: The FastAPI application instance.
        bm25_index: Global BM25 index shared with the admin router.
    """
    app.include_router(auth_router)
    app.include_router(query_router)
    app.include_router(documents_router)
    app.include_router(admin_router)
    app.include_router(health_router)
    app.include_router(integrations_router)
    app.include_router(users_router)
