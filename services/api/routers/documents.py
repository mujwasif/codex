"""
Document browsing endpoints: documents, chunks, and entities.
"""

from fastapi import APIRouter, Depends, HTTPException

from codex.packages.shared.schemas import (
    DocumentResponse, DocumentListResponse, DocumentDetailResponse,
    ChunkResponse, ChunkDetailResponse, PaginatedChunksResponse,
    EntityResponse
)
from codex.packages.shared.db import get_db_session
from codex.packages.shared.models import Document, Chunk, Entity
from codex.services.api.dependencies import get_current_active_user

router = APIRouter(tags=["documents"])


@router.get("/documents", response_model=list[DocumentListResponse])
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


@router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
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

            chunks = session.query(Chunk).filter(
                Chunk.document_id == document_id
            ).order_by(Chunk.section_path).all()

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


@router.get("/chunks", response_model=PaginatedChunksResponse)
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

            if document_id:
                query = query.filter(Chunk.document_id == document_id)

            if search:
                search_filter = f"%{search}%"
                query = query.filter(
                    (Chunk.clause_ref.ilike(search_filter)) |
                    (Chunk.text.ilike(search_filter))
                )

            total = query.count()
            chunks = query.order_by(Chunk.section_path).limit(limit).offset(offset).all()

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


@router.get("/chunks/{chunk_id}", response_model=ChunkDetailResponse)
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

            doc = session.query(Document).filter(Document.id == chunk.document_id).first()
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
                    created_at=chunk.created_at.isoformat() if chunk.created_at else ""
                ),
                document_title=doc_title
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch chunk: {e}")


@router.get("/entities", response_model=list[EntityResponse])
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
