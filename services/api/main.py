import time
import uuid
import math
import re
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import text
from codex.packages.shared.auth import verify_token, create_access_token, get_pwd_hash, verify_password
from codex.packages.shared.schemas import (
    Token, UserCreate, UserResponse, QueryCreate, QueryResponse,
    AnswerResponse, FeedbackCreate, FeedbackResponse, CitationResponse,
    HealthResponse, AuditLogResponse,
    DocumentResponse, DocumentListResponse, DocumentDetailResponse,
    ChunkResponse, ChunkDetailResponse, PaginatedChunksResponse,
    EntityResponse
)
from codex.services.api.search import search_policy
from codex.services.agents.reasoner import generate_grounded_answer
from codex.services.agents.verifier import verify_citations
from codex.packages.shared.db import get_db_session
from codex.packages.shared.models import (
    Query, Answer, Citation, Feedback, AuditLog, Document, Chunk, Entity
)
from codex.services.ingestion.bm25_index import BM25Index

app = FastAPI(title="Codex Policy Intelligence Engine", version="0.1.0")
outh2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Global BM25 index
bm25_index = None

# MOCK USER DATABASE for MVP (Normally in Postgres)
# Passwords are hashed with argon2 at startup (via hashward CryptContext)
MOCK_USERS = {
    "admin": {"username": "admin", "password": get_pwd_hash("password123"), "department": "IT", "access_level": 3, "is_active": True},
    "manager": {"username": "manager", "password": get_pwd_hash("password123"), "department": "Finance", "access_level": 2, "is_active": True},
    "employee": {"username": "employee", "password": get_pwd_hash("password123"), "department": "HR", "access_level": 1, "is_active": True},
}


# ============ Startup Event ============

@app.on_event("startup")
async def startup_event():
    global bm25_index
    try:
        bm25_index = BM25Index()
        print(f"✓ BM25 index loaded: {bm25_index.chunk_count} chunks")
    except Exception as e:
        print(f"⚠️ BM25 index failed to load: {e}")


# ============ Audit Logging Middleware ============

def log_audit_action(actor: str, action: str, payload: dict = None):
    """Log an action to the audit trail."""
    try:
        with get_db_session() as session:
            audit_entry = AuditLog(
                actor=actor,
                action=action,
                payload=payload or {}
            )
            session.add(audit_entry)
            session.commit()
    except Exception as e:
        print(f"⚠️ Audit logging failed: {e}")


# ============ Core Logic ============

def compute_confidence(chunks, is_valid, answer_text):
    """
    Compute confidence score as a percentage (0-100).
    
    Weights:
    - Reranker score normalized by sigmoid (40%)
    - Citation coverage (35%)
    - Chunk availability (25%)
    """
    # 1. Reranker score → sigmoid normalization (40%)
    if chunks:
        top_score = chunks[0].get("score", 0.0)
        reranker_norm = 1.0 / (1.0 + math.exp(-top_score))
    else:
        reranker_norm = 0.0
    
    # 2. Citation coverage (35%)
    citations_found = re.findall(r"\[Doc: (.*?), Clause: (.*?)\]", answer_text)
    if citations_found:
        valid_pairs = {(c['title'], c.get('clause_ref', 'N/A')) for c in chunks}
        valid_count = sum(1 for doc, clause in citations_found if (doc, clause) in valid_pairs)
        citation_coverage = valid_count / len(citations_found)
    elif "Insufficient" in answer_text:
        citation_coverage = 1.0  # Correct abstention
    else:
        citation_coverage = 0.0  # No citations, not abstaining
    
    # 3. Chunk availability (25%)
    chunk_avail = min(len(chunks) / 3.0, 1.0)
    
    # Weighted combination
    confidence = (reranker_norm * 0.40 + citation_coverage * 0.35 + chunk_avail * 0.25)
    
    # Convert to percentage, round to 1 decimal
    return round(confidence * 100, 1)

# ============ Authentication Dependencies ============

async def get_current_user(token: str = Depends(outh2_scheme)):
    user_data = verify_token(token)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_data


def get_current_active_user(current_user: dict = Depends(get_current_user)):
    username = current_user.get("username")
    user = MOCK_USERS.get(username)
    if not user or not user.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive or deleted user"
        )
    return current_user


# ============ User Management Endpoints ============

@app.post("/register", response_model=UserResponse)
async def register(user: UserCreate):
    if user.username in MOCK_USERS:
        raise HTTPException(status_code=400, detail="Username already exists")

    MOCK_USERS[user.username] = {
        "username": user.username,
        "password": get_pwd_hash(user.password),
        "department": user.department,
        "access_level": user.access_level,
        "is_active": True
    }

    # Log audit action
    log_audit_action(user.username, "register", {
        "department": user.department,
        "access_level": user.access_level
    })

    return MOCK_USERS[user.username]


@app.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = MOCK_USERS.get(form_data.username)
    if not user or not verify_password(form_data.password, user["password"]):
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    if not user.get("is_active", False):
        raise HTTPException(status_code=403, detail="Inactive user")

    access_token = create_access_token(data={"username": user["username"], "access_level": user["access_level"]})

    # Log audit action
    log_audit_action(user["username"], "login", {"success": True})

    return {"access_token": access_token, "token_type": "bearer"}


# ============ Query Endpoints ============

@app.post("/query", response_model=AnswerResponse)
async def secure_query(query_data: QueryCreate, current_user: dict = Depends(get_current_active_user)):
    """
    Process a policy query with full logging and citation tracking.
    """
    username = current_user.get("username")
    level = current_user.get("access_level", 1)
    start_time = time.time()

    # 1. Log the query
    query_id = uuid.uuid4()
    try:
        with get_db_session() as session:
            query_record = Query(
                id=query_id,
                user_id=username,
                role=f"level_{level}",
                dept=MOCK_USERS.get(username, {}).get("department", "Unknown"),
                question=query_data.question,
                intent=query_data.intent
            )
            session.add(query_record)
            session.commit()
    except Exception as e:
        print(f"⚠️ Query logging failed: {e}")

    # 2. Retrieve and Rerank
    search_mode = query_data.search_mode or "hybrid"
    chunks = search_policy(
        query_data.question,
        access_level=level,
        search_mode=search_mode,
        bm25_index=bm25_index,
    )

    # 3. Generate Grounded Answer
    answer_text = generate_grounded_answer(query_data.question, chunks)

    # 4. Verify Grounding
    is_valid, msg = verify_citations(answer_text, chunks)

    # 5. Calculate latency
    latency_ms = int((time.time() - start_time) * 1000)

    # 6. Determine verdict
    confidence = compute_confidence(chunks, is_valid, answer_text)

    if not is_valid:
        verdict = "abstained"
        abstained = True
    elif "Insufficient" in answer_text:
        verdict = "abstained"
        abstained = True
    else:
        verdict = "clear"
        abstained = False
    
    # Note: confidence is now computed as a percentage (0-100)
    # regardless of whether the answer is clear or abstained.

    # 7. Log the answer
    answer_id = uuid.uuid4()
    try:
        with get_db_session() as session:
            answer_record = Answer(
                id=answer_id,
                query_id=query_id,
                answer=answer_text,
                verdict=verdict,
                confidence=confidence,
                abstained=abstained,
                latency_ms=latency_ms,
                model_version="deepseek-r1-distill-llama-8b"
            )
            session.add(answer_record)

            # Log citations
            for chunk in chunks:
                citation = Citation(
                    answer_id=answer_id,
                    chunk_id=uuid.UUID(chunk.get("id", str(uuid.uuid4()))),
                    document_id=uuid.UUID(chunk.get("document_id", str(uuid.uuid4()))),
                    clause_ref=chunk.get("clause_ref"),
                    score=chunk.get("score", 0.0)
                )
                session.add(citation)

            session.commit()
    except Exception as e:
        print(f"⚠️ Answer logging failed: {e}")

    # 8. Log audit action
    log_audit_action(username, "query", {
        "question": query_data.question[:100],
        "verdict": verdict,
        "confidence": confidence
    })

    # 9. Build response
    citations_response = [
        CitationResponse(
            id=str(uuid.uuid4()),
            chunk_id=chunk.get("id", ""),
            document_id=chunk.get("document_id", ""),
            clause_ref=chunk.get("clause_ref"),
            score=chunk.get("score", 0.0)
        )
        for chunk in chunks
    ]

    return AnswerResponse(
        id=str(answer_id),
        query_id=str(query_id),
        answer=answer_text,
        verdict=verdict,
        confidence=confidence,
        abstained=abstained,
        latency_ms=latency_ms,
        citations=citations_response,
        search_mode=search_mode,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S")
    )


@app.get("/query/history", response_model=list[QueryResponse])
async def get_query_history(current_user: dict = Depends(get_current_active_user)):
    """Get query history for the current user."""
    username = current_user.get("username")

    try:
        with get_db_session() as session:
            queries = session.query(Query).filter(
                Query.user_id == username
            ).order_by(Query.created_at.desc()).limit(50).all()

            return [
                QueryResponse(
                    id=str(q.id),
                    user_id=q.user_id,
                    question=q.question,
                    intent=q.intent,
                    created_at=q.created_at.isoformat() if q.created_at else ""
                )
                for q in queries
            ]
    except Exception as e:
        print(f"⚠️ Failed to fetch query history: {e}")
        return []


# ============ Feedback Endpoints ============

@app.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(feedback_data: FeedbackCreate, current_user: dict = Depends(get_current_active_user)):
    """Submit feedback for an answer."""
    username = current_user.get("username")

    try:
        with get_db_session() as session:
            # Verify answer exists
            answer = session.query(Answer).filter(Answer.id == feedback_data.answer_id).first()
            if not answer:
                raise HTTPException(status_code=404, detail="Answer not found")

            # Create feedback
            feedback = Feedback(
                answer_id=feedback_data.answer_id,
                rating=feedback_data.rating,
                note=feedback_data.note,
                reviewer=username
            )
            session.add(feedback)
            session.commit()

            # Log audit action
            log_audit_action(username, "feedback", {
                "answer_id": feedback_data.answer_id,
                "rating": feedback_data.rating
            })

            return FeedbackResponse(
                id=str(feedback.id),
                answer_id=str(feedback.answer_id),
                rating=feedback.rating,
                note=feedback.note,
                reviewer=feedback.reviewer,
                created_at=feedback.created_at.isoformat() if feedback.created_at else ""
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit feedback: {e}")


@app.get("/feedback/{answer_id}", response_model=list[FeedbackResponse])
async def get_feedback_for_answer(answer_id: str, current_user: dict = Depends(get_current_active_user)):
    """Get feedback for a specific answer."""
    try:
        with get_db_session() as session:
            feedbacks = session.query(Feedback).filter(
                Feedback.answer_id == answer_id
            ).all()

            return [
                FeedbackResponse(
                    id=str(f.id),
                    answer_id=str(f.answer_id),
                    rating=f.rating,
                    note=f.note,
                    reviewer=f.reviewer,
                    created_at=f.created_at.isoformat() if f.created_at else ""
                )
                for f in feedbacks
            ]
    except Exception as e:
        print(f"⚠️ Failed to fetch feedback: {e}")
        return []


# ============ Audit Log Endpoints ============

@app.get("/audit", response_model=list[AuditLogResponse])
async def get_audit_logs(
    action: str = None,
    actor: str = None,
    limit: int = 100,
    current_user: dict = Depends(get_current_active_user)
):
    """Get audit logs (admin only)."""
    # Check if user is admin
    if current_user.get("access_level", 1) < 3:
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        with get_db_session() as session:
            query = session.query(AuditLog)

            if action:
                query = query.filter(AuditLog.action == action)
            if actor:
                query = query.filter(AuditLog.actor == actor)

            logs = query.order_by(AuditLog.ts.desc()).limit(limit).all()

            return [
                AuditLogResponse(
                    id=str(log.id),
                    actor=log.actor,
                    action=log.action,
                    payload=log.payload,
                    ts=log.ts.isoformat() if log.ts else ""
                )
                for log in logs
            ]
    except Exception as e:
        print(f"⚠️ Failed to fetch audit logs: {e}")
        return []


# ============ Document Browsing Endpoints ============

@app.get("/documents", response_model=list[DocumentListResponse])
async def list_documents(
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_active_user)
):
    """List all ingested documents with chunk/entity counts."""
    try:
        with get_db_session() as session:
            documents = session.query(Document).order_by(
                Document.created_at.desc()
            ).limit(limit).offset(offset).all()

            result = []
            for doc in documents:
                chunk_count = session.query(Chunk).filter(Chunk.document_id == doc.id).count()
                entity_count = session.query(Entity).filter(Entity.document_id == doc.id).count()

                result.append(DocumentListResponse(
                    id=str(doc.id),
                    title=doc.title,
                    type=doc.type,
                    status=doc.status,
                    chunk_count=chunk_count,
                    entity_count=entity_count,
                    created_at=doc.created_at.isoformat() if doc.created_at else ""
                ))

            return result
    except Exception as e:
        print(f"⚠️ Failed to fetch documents: {e}")
        return []


@app.get("/documents/{document_id}", response_model=DocumentDetailResponse)
async def get_document_detail(
    document_id: str,
    current_user: dict = Depends(get_current_active_user)
):
    """Get document details with its chunks and entities."""
    try:
        with get_db_session() as session:
            doc = session.query(Document).filter(Document.id == document_id).first()
            if not doc:
                raise HTTPException(status_code=404, detail="Document not found")

            # Get chunks
            chunks = session.query(Chunk).filter(
                Chunk.document_id == document_id
            ).order_by(Chunk.section_path).all()

            # Get entities
            entities = session.query(Entity).filter(
                Entity.document_id == document_id
            ).all()

            return DocumentDetailResponse(
                document=DocumentResponse(
                    id=str(doc.id),
                    title=doc.title,
                    type=doc.type,
                    owner=doc.owner,
                    version=doc.version,
                    effective_date=doc.effective_date.isoformat() if doc.effective_date else None,
                    status=doc.status,
                    source_uri=doc.source_uri,
                    access_tags=doc.access_tags or [],
                    created_at=doc.created_at.isoformat() if doc.created_at else "",
                    updated_at=doc.updated_at.isoformat() if doc.updated_at else ""
                ),
                chunks=[
                    ChunkResponse(
                        id=str(c.id),
                        document_id=str(c.document_id),
                        section_path=c.section_path,
                        clause_ref=c.clause_ref,
                        page=c.page,
                        text=c.text,
                        token_count=c.token_count,
                        created_at=c.created_at.isoformat() if c.created_at else ""
                    )
                    for c in chunks
                ],
                entities=[
                    EntityResponse(
                        id=str(e.id),
                        type=e.type,
                        name=e.name,
                        document_id=str(e.document_id),
                        attrs=e.attrs or {},
                        created_at=e.created_at.isoformat() if e.created_at else ""
                    )
                    for e in entities
                ]
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch document: {e}")


@app.get("/chunks", response_model=PaginatedChunksResponse)
async def list_chunks(
    search: str = None,
    document_id: str = None,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_active_user)
):
    """Browse/search all chunks with filters."""
    try:
        with get_db_session() as session:
            query = session.query(Chunk)

            # Apply filters
            if document_id:
                query = query.filter(Chunk.document_id == document_id)

            if search:
                # Search in clause_ref and text
                search_filter = f"%{search}%"
                query = query.filter(
                    (Chunk.clause_ref.ilike(search_filter)) |
                    (Chunk.text.ilike(search_filter))
                )

            # Get total count
            total = query.count()

            # Get paginated results
            chunks = query.order_by(Chunk.section_path).limit(limit).offset(offset).all()

            return PaginatedChunksResponse(
                chunks=[
                    ChunkResponse(
                        id=str(c.id),
                        document_id=str(c.document_id),
                        section_path=c.section_path,
                        clause_ref=c.clause_ref,
                        page=c.page,
                        text=c.text[:500] + "..." if len(c.text) > 500 else c.text,  # Truncate for listing
                        token_count=c.token_count,
                        created_at=c.created_at.isoformat() if c.created_at else ""
                    )
                    for c in chunks
                ],
                total=total,
                limit=limit,
                offset=offset
            )
    except Exception as e:
        print(f"⚠️ Failed to fetch chunks: {e}")
        return PaginatedChunksResponse(chunks=[], total=0, limit=limit, offset=offset)


@app.get("/chunks/{chunk_id}", response_model=ChunkDetailResponse)
async def get_chunk_detail(
    chunk_id: str,
    current_user: dict = Depends(get_current_active_user)
):
    """Get a specific chunk's full text and metadata."""
    try:
        with get_db_session() as session:
            chunk = session.query(Chunk).filter(Chunk.id == chunk_id).first()
            if not chunk:
                raise HTTPException(status_code=404, detail="Chunk not found")

            # Get document title
            doc = session.query(Document).filter(Document.id == chunk.document_id).first()
            doc_title = doc.title if doc else None

            return ChunkDetailResponse(
                chunk=ChunkResponse(
                    id=str(chunk.id),
                    document_id=str(chunk.document_id),
                    section_path=chunk.section_path,
                    clause_ref=chunk.clause_ref,
                    page=chunk.page,
                    text=chunk.text,  # Full text, not truncated
                    token_count=chunk.token_count,
                    created_at=chunk.created_at.isoformat() if chunk.created_at else ""
                ),
                document_title=doc_title
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch chunk: {e}")


@app.get("/entities", response_model=list[EntityResponse])
async def list_entities(
    type: str = None,
    document_id: str = None,
    limit: int = 100,
    current_user: dict = Depends(get_current_active_user)
):
    """Browse extracted entities (thresholds, approvals, deadlines)."""
    try:
        with get_db_session() as session:
            query = session.query(Entity)

            # Apply filters
            if type:
                query = query.filter(Entity.type == type)
            if document_id:
                query = query.filter(Entity.document_id == document_id)

            entities = query.order_by(Entity.created_at.desc()).limit(limit).all()

            return [
                EntityResponse(
                    id=str(e.id),
                    type=e.type,
                    name=e.name,
                    document_id=str(e.document_id),
                    attrs=e.attrs or {},
                    created_at=e.created_at.isoformat() if e.created_at else ""
                )
                for e in entities
            ]
    except Exception as e:
        print(f"⚠️ Failed to fetch entities: {e}")
        return []


# ============ Health Check ============

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Check system health."""
    db_status = "unknown"
    llama_status = "unknown"

    # Check database
    try:
        with get_db_session() as session:
            session.execute(text("SELECT 1"))
            db_status = "healthy"
    except Exception:
        db_status = "unavailable"

    # Check llama.cpp server
    try:
        import requests
        response = requests.get("http://localhost:8080/health", timeout=5)
        llama_status = "healthy" if response.status_code == 200 else "unhealthy"
    except Exception:
        llama_status = "unavailable"

    return HealthResponse(
        status="healthy" if db_status == "healthy" else "degraded",
        database=db_status,
        llama_server=llama_status
    )


# ============ Admin Endpoints ============

@app.post("/admin/refresh-index")
async def refresh_index(current_user: dict = Depends(get_current_active_user)):
    """Rebuild BM25 index after new ingestion. Admin only."""
    if current_user.get("access_level", 1) < 3:
        raise HTTPException(status_code=403, detail="Admin access required")

    global bm25_index
    try:
        bm25_index.refresh()
        return {"status": "ok", "chunk_count": bm25_index.chunk_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to refresh index: {e}")
