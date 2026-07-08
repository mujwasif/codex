from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


# ============ User Models ============

class User(BaseModel):
    username: str
    department: str
    access_level: int  # 1=Standard, 2=Manager, 3=Admin

class UserCreate(BaseModel):
    username: str
    password: str
    department: str
    access_level: int

class UserResponse(BaseModel):
    username: str
    department: str
    access_level: int
    is_active: bool

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None
    access_level: Optional[int] = None


# ============ Query Models ============

class QueryCreate(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)
    intent: Optional[str] = None  # 'policy_lookup', 'approval_check', 'compliance'
    search_mode: Optional[str] = "hybrid"  # "vector" | "hybrid" | "bm25"

class QueryResponse(BaseModel):
    id: str
    user_id: str
    question: str
    intent: Optional[str]
    created_at: str


# ============ Answer Models ============

class CitationResponse(BaseModel):
    id: str
    chunk_id: str
    document_id: str
    clause_ref: Optional[str]
    score: Optional[float]

class AnswerResponse(BaseModel):
    id: str
    query_id: str
    answer: str
    verdict: str  # 'clear', 'abstained', 'conflict'
    confidence: float
    abstained: bool
    latency_ms: Optional[int]
    citations: List[CitationResponse] = []
    search_mode: str = "hybrid"
    created_at: str

class QueryResult(BaseModel):
    """Complete query result with answer and citations."""
    answer: str
    verdict: str
    confidence: float
    citations: List[Dict[str, Any]] = []
    query_id: Optional[str] = None
    answer_id: Optional[str] = None


# ============ Feedback Models ============

class FeedbackCreate(BaseModel):
    answer_id: str
    rating: int = Field(..., ge=1, le=5)
    note: Optional[str] = None

class FeedbackResponse(BaseModel):
    id: str
    answer_id: str
    rating: int
    note: Optional[str]
    reviewer: str
    created_at: str


# ============ Document Models ============

class DocumentResponse(BaseModel):
    id: str
    title: str
    type: Optional[str]
    owner: Optional[str]
    version: Optional[str]
    effective_date: Optional[str]
    status: str
    source_uri: Optional[str]
    access_tags: List[str] = []
    created_at: str
    updated_at: str

class DocumentListResponse(BaseModel):
    """Document with chunk/entity counts."""
    id: str
    title: str
    type: Optional[str]
    status: str
    chunk_count: int = 0
    entity_count: int = 0
    created_at: str

class DocumentDetailResponse(BaseModel):
    """Document with its chunks and entities."""
    document: DocumentResponse
    chunks: List["ChunkResponse"] = []
    entities: List["EntityResponse"] = []

class ChunkResponse(BaseModel):
    id: str
    document_id: str
    section_path: Optional[str]
    clause_ref: Optional[str]
    page: Optional[int]
    text: str
    token_count: Optional[int]
    created_at: str

class PaginatedChunksResponse(BaseModel):
    """Paginated chunk list."""
    chunks: List[ChunkResponse] = []
    total: int
    limit: int
    offset: int

class ChunkDetailResponse(BaseModel):
    """Chunk with document info."""
    chunk: ChunkResponse
    document_title: Optional[str] = None


# ============ Entity Models ============

class EntityResponse(BaseModel):
    id: str
    type: Optional[str]
    name: Optional[str]
    document_id: str
    attrs: Dict[str, Any] = {}
    created_at: str


# ============ Audit Log Models ============

class AuditLogResponse(BaseModel):
    id: str
    actor: Optional[str]
    action: str
    payload: Dict[str, Any] = {}
    ts: str


# ============ Health Check ============

class HealthResponse(BaseModel):
    status: str
    database: str
    llama_server: str
    version: str = "0.1.0"
