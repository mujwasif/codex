import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Text, CheckConstraint, JSON
from sqlalchemy.orm import relationship
from packages.shared.db import Base


class Document(Base):
    """Policy corpus registry."""
    __tablename__ = 'documents'
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True)
    title = Column(Text, nullable=False)
    type = Column(String(50))
    owner = Column(String(100))
    version = Column(String(20))
    effective_date = Column(DateTime)
    status = Column(String(20), default='active')
    source_uri = Column(Text)
    access_tags = Column(JSON, default=[])
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")
    entities = relationship("Entity", back_populates="document")



class Chunk(Base):
    """Clause-level units + vectors."""
    __tablename__ = 'chunks'
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'))
    section_path = Column(Text)
    clause_ref = Column(String(100))
    page = Column(Integer)
    text = Column(Text, nullable=False)
    embedding = Column(Text)  # Store as JSON string for SQLite, vector for PostgreSQL
    token_count = Column(Integer)
    version = Column(String(20))  # "v1", "v2", etc.
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    document = relationship("Document", back_populates="chunks")
    citations = relationship("Citation", back_populates="chunk")


class Entity(Base):
    """Extracted rules/entities mirror."""
    __tablename__ = 'entities'
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    type = Column(String(50))
    name = Column(String(200))
    document_id = Column(String(36), ForeignKey('documents.id'))
    attrs = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    document = relationship("Document", back_populates="entities")


class Query(Base):
    """Inbound questions."""
    __tablename__ = 'queries'
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(100))
    role = Column(String(50))
    dept = Column(String(100))
    question = Column(Text, nullable=False)
    intent = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    answers = relationship("Answer", back_populates="query")


class Answer(Base):
    """Responses + outcome."""
    __tablename__ = 'answers'
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    query_id = Column(String(36), ForeignKey('queries.id'))
    answer = Column(Text)
    verdict = Column(String(20))
    confidence = Column(Float)
    abstained = Column(Boolean, default=False)
    latency_ms = Column(Integer)
    model_version = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    query = relationship("Query", back_populates="answers")
    citations = relationship("Citation", back_populates="answer", cascade="all, delete-orphan")
    feedback = relationship("Feedback", back_populates="answer", cascade="all, delete-orphan")


class Citation(Base):
    """Answer-to-clause links."""
    __tablename__ = 'citations'
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    answer_id = Column(String(36), ForeignKey('answers.id'))
    chunk_id = Column(String(36), ForeignKey('chunks.id'))
    document_id = Column(String(36), ForeignKey('documents.id'))
    clause_ref = Column(String(100))
    score = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    answer = relationship("Answer", back_populates="citations")
    chunk = relationship("Chunk", back_populates="citations")


class Feedback(Base):
    """Human review signal."""
    __tablename__ = 'feedback'
    __table_args__ = (
        CheckConstraint('rating BETWEEN 1 AND 5', name='check_rating_range'),
        {'extend_existing': True}
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    answer_id = Column(String(36), ForeignKey('answers.id'))
    rating = Column(Integer)
    note = Column(Text)
    reviewer = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    answer = relationship("Answer", back_populates="feedback")


class AuditLog(Base):
    """Immutable audit trail."""
    __tablename__ = 'audit_log'
    __table_args__ = {'extend_existing': True}

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor = Column(String(100))
    action = Column(String(50))
    payload = Column(JSON, default={})
    ts = Column(DateTime, default=datetime.utcnow)
