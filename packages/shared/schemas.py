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


class UserUpdate(BaseModel):
    department: Optional[str] = None
    access_level: Optional[int] = Field(None, ge=1, le=3)


class UserListResponse(BaseModel):
    id: str
    username: str
    department: str
    access_level: int
    is_active: bool
    created_at: str


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
    selected_doc_ids: Optional[List[str]] = None  # Phase 2: user-selected doc IDs for conflict analysis


class QueryResponse(BaseModel):
    id: str
    user_id: str
    question: str
    intent: Optional[str]
    created_at: str
    answer: Optional[str] = None
    verdict: Optional[str] = None
    confidence: Optional[float] = None
    citations: List["CitationResponse"] = []


# ============ Answer Models ============


class CitationResponse(BaseModel):
    id: str
    chunk_id: str
    document_id: str
    title: Optional[str] = None  # Source document title
    clause_ref: Optional[str]
    score: Optional[float]
    section_path: Optional[str] = None  # Where the quote sits in the document
    quote: Optional[str] = None  # Exact source text (fetched from retrieved context)


class ResolvedDoc(BaseModel):
    """A document resolved from a natural-language name query."""

    id: str
    title: str
    similarity: float
    selected: bool = True


class DocumentSlot(BaseModel):
    """Per-slot document candidates for user selection."""

    slot: int
    phrase: str
    candidates: List[ResolvedDoc]


class AnswerResponse(BaseModel):
    id: str
    query_id: str
    answer: str
    verdict: str  # 'clear', 'abstained', 'conflict', 'conditional', 'violation'
    confidence: float
    abstained: bool
    latency_ms: Optional[int]
    citations: List[CitationResponse] = []
    search_mode: str = "hybrid"
    reasoning: Optional[Dict[str, Any]] = None  # Agent chain trace
    conflict_analysis: Optional["ConflictAnalysisResponse"] = None
    resolved_documents: Optional[List[ResolvedDoc]] = None  # Selected docs
    document_slots: Optional[List[DocumentSlot]] = None  # Per-slot candidates
    next_steps: List[str] = []  # Actionable recommendations
    missing: List[str] = []  # Required approvals/docs not satisfied
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


class FeedbackResponse(BaseModel):
    id: str
    answer_id: str
    rating: int
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


class SimilarClauseResponse(BaseModel):
    """A chunk with its similarity score to a source clause."""

    chunk: ChunkResponse
    similarity: float
    document_title: Optional[str] = None


# ============ Conflict Models ============


class ConflictClauseInfo(BaseModel):
    """Clause metadata inside a conflict record."""

    id: str
    document_id: str
    document_title: str
    clause_ref: Optional[str] = None
    section_path: Optional[str] = None
    text: str
    origin: Optional[str] = None
    version: Optional[str] = None
    effective_date: Optional[str] = None
    status: Optional[str] = None
    page: Optional[int] = None


class ConflictPair(BaseModel):
    """A confirmed or candidate conflict between two clauses."""

    clause_a: ConflictClauseInfo
    clause_b: ConflictClauseInfo
    similarity: float
    conflict: bool
    reason: str
    source: str  # "llm" | "version_check" | "neo4j"
    status: str = "confirmed_conflict"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    subject: Optional[str] = None
    difference_type: Optional[str] = None
    scope_overlap: bool = False
    missing_context: List[str] = []
    source_requirement: Optional[str] = None
    candidate_requirement: Optional[str] = None


class ConflictAnalysisResponse(BaseModel):
    """Auditable Clause-vs-Corpus analysis metadata."""

    status: str = "complete"
    conflicts: List[ConflictPair] = []
    total_candidates: int = 0
    checked_candidates: int = 0
    unchecked_candidates: int = 0
    llm_calls: int = 0
    truncated: bool = False
    inconclusive: bool = False
    evaluated_top_k: bool = False
    coverage_note: Optional[str] = None
    genuine_failures: int = 0


class ConflictCompareRequest(BaseModel):
    """Request body for POST /v1/conflicts/compare."""

    document_ids: Optional[List[str]] = Field(None, min_length=2, max_length=3)
    document_names: Optional[List[str]] = Field(None, min_length=2, max_length=3)
    similarity_threshold: float = Field(0.6, ge=0.0, le=1.0)
    max_pairs: int = Field(200, ge=1, le=500)
    max_llm_calls: int = Field(100, ge=1, le=200)


class DocumentConflictGroup(BaseModel):
    """Conflicts grouped by a document."""

    document_id: str
    document_title: str
    conflicts: List[ConflictPair] = []
    unchecked_candidate_count: int = 0
    total_candidate_count: int = 0


class ConflictCompareResponse(BaseModel):
    """Response for POST /v1/conflicts/compare."""

    doc_pairs: List[Dict[str, Any]] = []
    total_conflicts: int = 0
    total_candidates: int = 0
    total_llm_calls: int = 0
    similarity_threshold: float = 0.6
    truncated: bool = False


class DocumentConflictsResponse(BaseModel):
    """Response for GET /documents/{doc_id}/conflicts."""

    document_id: str
    document_title: str
    conflicting_documents: List[DocumentConflictGroup] = []
    total_conflicts: int = 0
    total_llm_calls: int = 0
    similarity_threshold: float = 0.6
    truncated: bool = False


# ============ Entity Models ============


class EntityResponse(BaseModel):
    id: str
    type: Optional[str]
    name: Optional[str]
    document_id: str
    attrs: Dict[str, Any] = {}
    created_at: str


# ============ Department Models ============


class DepartmentResponse(BaseModel):
    name: str
    access_level: Optional[int] = None


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
    qwen_server: str = "unknown"
    neo4j: str = "unknown"
    version: str = "0.1.0"
