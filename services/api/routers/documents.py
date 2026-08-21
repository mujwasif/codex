"""
Document browsing endpoints: documents, chunks, and entities.
"""

from fastapi import APIRouter, Depends, HTTPException

from packages.shared.schemas import (
    DocumentResponse,
    DocumentListResponse,
    DocumentDetailResponse,
    ChunkResponse,
    ChunkDetailResponse,
    PaginatedChunksResponse,
    EntityResponse,
    SimilarClauseResponse,
    DocumentConflictsResponse,
    DocumentConflictGroup,
    ConflictPair,
    ConflictClauseInfo,
)
from packages.shared.db import get_db_session
from packages.shared.models import Document, Chunk, Entity
from services.api.dependencies import get_current_active_user

router = APIRouter(tags=["documents"])


@router.get("/documents", response_model=list[DocumentListResponse])
async def list_documents(
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_active_user),
):
    """List all ingested documents with chunk/entity counts."""
    try:
        with get_db_session() as session:
            documents = (
                session.query(Document)
                .order_by(Document.created_at.desc())
                .limit(limit)
                .offset(offset)
                .all()
            )

            result = []
            for doc in documents:
                chunk_count = (
                    session.query(Chunk).filter(Chunk.document_id == doc.id).count()
                )
                entity_count = (
                    session.query(Entity).filter(Entity.document_id == doc.id).count()
                )

                result.append(
                    DocumentListResponse(
                        id=str(doc.id),
                        title=doc.title,
                        type=doc.type,
                        status=doc.status,
                        chunk_count=chunk_count,
                        entity_count=entity_count,
                        created_at=doc.created_at.isoformat() if doc.created_at else "",
                    )
                )

            return result
    except Exception as e:
        print(f"⚠️ Failed to fetch documents: {e}")
        return []


@router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
async def get_document_detail(
    document_id: str, current_user: dict = Depends(get_current_active_user)
):
    """Get document details with its chunks and entities."""
    try:
        with get_db_session() as session:
            doc = session.query(Document).filter(Document.id == document_id).first()
            if not doc:
                raise HTTPException(status_code=404, detail="Document not found")

            chunks = (
                session.query(Chunk)
                .filter(Chunk.document_id == document_id)
                .order_by(Chunk.section_path)
                .all()
            )

            entities = (
                session.query(Entity).filter(Entity.document_id == document_id).all()
            )

            return DocumentDetailResponse(
                document=DocumentResponse(
                    id=str(doc.id),
                    title=doc.title,
                    type=doc.type,
                    owner=doc.owner,
                    version=doc.version,
                    effective_date=doc.effective_date.isoformat()
                    if doc.effective_date
                    else None,
                    status=doc.status,
                    source_uri=doc.source_uri,
                    access_tags=doc.access_tags or [],
                    created_at=doc.created_at.isoformat() if doc.created_at else "",
                    updated_at=doc.updated_at.isoformat() if doc.updated_at else "",
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
                        created_at=c.created_at.isoformat() if c.created_at else "",
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
                        created_at=e.created_at.isoformat() if e.created_at else "",
                    )
                    for e in entities
                ],
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch document: {e}")


@router.get("/chunks", response_model=PaginatedChunksResponse)
async def list_chunks(
    search: str = None,
    document_id: str = None,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_active_user),
):
    """Browse/search all chunks with filters."""
    try:
        with get_db_session() as session:
            query = session.query(Chunk)

            if document_id:
                query = query.filter(Chunk.document_id == document_id)

            if search:
                search_filter = f"%{search}%"
                query = query.filter(
                    (Chunk.clause_ref.ilike(search_filter))
                    | (Chunk.text.ilike(search_filter))
                )

            total = query.count()
            chunks = (
                query.order_by(Chunk.section_path).limit(limit).offset(offset).all()
            )

            return PaginatedChunksResponse(
                chunks=[
                    ChunkResponse(
                        id=str(c.id),
                        document_id=str(c.document_id),
                        section_path=c.section_path,
                        clause_ref=c.clause_ref,
                        page=c.page,
                        text=c.text[:500] + "..." if len(c.text) > 500 else c.text,
                        token_count=c.token_count,
                        created_at=c.created_at.isoformat() if c.created_at else "",
                    )
                    for c in chunks
                ],
                total=total,
                limit=limit,
                offset=offset,
            )
    except Exception as e:
        print(f"⚠️ Failed to fetch chunks: {e}")
        return PaginatedChunksResponse(chunks=[], total=0, limit=limit, offset=offset)


@router.get("/chunks/{chunk_id}", response_model=ChunkDetailResponse)
async def get_chunk_detail(
    chunk_id: str, current_user: dict = Depends(get_current_active_user)
):
    """Get a specific chunk's full text and metadata."""
    try:
        with get_db_session() as session:
            chunk = session.query(Chunk).filter(Chunk.id == chunk_id).first()
            if not chunk:
                raise HTTPException(status_code=404, detail="Chunk not found")

            doc = (
                session.query(Document).filter(Document.id == chunk.document_id).first()
            )
            doc_title = doc.title if doc else None

            return ChunkDetailResponse(
                chunk=ChunkResponse(
                    id=str(chunk.id),
                    document_id=str(chunk.document_id),
                    section_path=chunk.section_path,
                    clause_ref=chunk.clause_ref,
                    page=chunk.page,
                    text=chunk.text,
                    token_count=chunk.token_count,
                    created_at=chunk.created_at.isoformat() if chunk.created_at else "",
                ),
                document_title=doc_title,
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch chunk: {e}")


@router.get("/chunks/{chunk_id}/similar", response_model=list[SimilarClauseResponse])
async def find_similar_clauses(
    chunk_id: str,
    top_k: int = 5,
    threshold: float = 0.7,
    current_user: dict = Depends(get_current_active_user),
):
    """Find semantically similar clauses across all accessible documents."""
    try:
        access_level = current_user.get("access_level", 1)
        with get_db_session() as session:
            chunk = session.query(Chunk).filter(Chunk.id == chunk_id).first()
            if not chunk:
                raise HTTPException(status_code=404, detail="Chunk not found")

            source_doc = (
                session.query(Document).filter(Document.id == chunk.document_id).first()
            )
            if source_doc and source_doc.access_level > access_level:
                raise HTTPException(status_code=403, detail="Access denied")

        from services.api.search import find_similar_clauses as _find_similar

        similar = _find_similar(
            clause_text=chunk.text,
            access_level=access_level,
            exclude_doc_id=str(chunk.document_id),
            top_k=top_k,
            threshold=threshold,
        )

        results = []
        for sim in similar:
            doc_title = ""
            with get_db_session() as session:
                doc = (
                    session.query(Document)
                    .filter(Document.id == sim["document_id"])
                    .first()
                )
                doc_title = doc.title if doc else ""
            results.append(
                SimilarClauseResponse(
                    chunk=ChunkResponse(
                        id=sim["id"],
                        document_id=sim["document_id"],
                        section_path=sim.get("section_path"),
                        clause_ref=sim.get("clause_ref"),
                        page=None,
                        text=sim["text"],
                        token_count=None,
                        created_at="",
                    ),
                    similarity=sim["similarity"],
                    document_title=doc_title,
                )
            )
        return results
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Similar clause search failed: {e}"
        )


@router.get(
    "/documents/{document_id}/conflicts", response_model=DocumentConflictsResponse
)
async def get_document_conflicts(
    document_id: str,
    similarity_threshold: float = 0.5,
    max_pairs: int = 100,
    max_llm_calls: int = 15,
    current_user: dict = Depends(get_current_active_user),
):
    """Find which documents conflict with the given document (Type 2b)."""
    try:
        access_level = current_user.get("access_level", 1)

        with get_db_session() as session:
            doc = session.query(Document).filter(Document.id == document_id).first()
            if not doc:
                raise HTTPException(status_code=404, detail="Document not found")
            if doc.access_level > access_level:
                raise HTTPException(status_code=403, detail="Access denied")

            target_title = doc.title
            chunks = (
                session.query(Chunk)
                .filter(Chunk.document_id == document_id)
                .order_by(Chunk.section_path)
                .all()
            )
            target_chunks = [
                {
                    "id": str(c.id),
                    "text": c.text,
                    "document_id": document_id,
                    "clause_ref": c.clause_ref,
                    "section_path": c.section_path,
                    "title": target_title,
                    "similarity": 0.0,
                }
                for c in chunks
            ]

        from services.agents.conflict_agent import detect_conflicting_documents

        result = detect_conflicting_documents(
            target_doc_chunks=target_chunks,
            target_doc_id=document_id,
            target_doc_title=target_title,
            access_level=access_level,
            similarity_threshold=similarity_threshold,
            max_pairs=max_pairs,
            max_llm_calls=max_llm_calls,
        )

        conflicting_docs = []
        for doc_group in result["conflicting_documents"]:
            conflicts = []
            for c in doc_group.get("conflicts", []):
                ca = c.get("clause_a", {})
                cb = c.get("clause_b", {})
                conflicts.append(
                    ConflictPair(
                        clause_a=ConflictClauseInfo(**ca),
                        clause_b=ConflictClauseInfo(**cb),
                        similarity=c.get("similarity", 0.0),
                        conflict=c.get("conflict", False),
                        reason=c.get("reason", ""),
                        source=c.get("source", ""),
                    )
                )
            conflicting_docs.append(
                DocumentConflictGroup(
                    document_id=doc_group["document_id"],
                    document_title=doc_group["document_title"],
                    conflicts=conflicts,
                    unchecked_candidate_count=doc_group.get(
                        "unchecked_candidate_count", 0
                    ),
                    total_candidate_count=doc_group.get("total_candidate_count", 0),
                )
            )

        return DocumentConflictsResponse(
            document_id=document_id,
            document_title=target_title,
            conflicting_documents=conflicting_docs,
            total_conflicts=result["total_conflicts"],
            total_llm_calls=result["total_llm_calls"],
            similarity_threshold=similarity_threshold,
            truncated=result["truncated"],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Document conflict check failed: {e}"
        )


@router.get("/entities", response_model=list[EntityResponse])
async def list_entities(
    type: str = None,
    document_id: str = None,
    limit: int = 100,
    current_user: dict = Depends(get_current_active_user),
):
    """Browse extracted entities (thresholds, approvals, deadlines)."""
    try:
        with get_db_session() as session:
            query = session.query(Entity)

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
                    created_at=e.created_at.isoformat() if e.created_at else "",
                )
                for e in entities
            ]
    except Exception as e:
        print(f"⚠️ Failed to fetch entities: {e}")
        return []
