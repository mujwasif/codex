import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from contextlib import contextmanager

# Database configuration
# PostgreSQL only — no SQLite fallback
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://codex_admin:securepassword123@localhost:5432/codex_db")

# Create engine with PostgreSQL settings
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()

@contextmanager
def get_db_session():
    """Context manager for database sessions."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def init_db():
    """Initialize database tables."""
    # Import models to ensure they're registered with Base
    from packages.shared.models import (
        Document, Chunk, Entity, Query, Answer, Citation, Feedback, AuditLog
    )
    Base.metadata.create_all(bind=engine)
    print(f"✅ Database tables created/verified")
