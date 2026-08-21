"""
Conflict analysis endpoints.

POST /v1/conflicts/compare  — compare 2-3 documents for conflicts
"""

from fastapi import APIRouter, Depends, HTTPException

from packages.shared.schemas import (
    ConflictCompareRequest,
    ConflictCompareResponse,
    ConflictPair,
    ConflictClauseInfo,
)
from packages.shared.db import get_db_session
from packages.shared.models import Document, Chunk
from services.api.dependencies import get_current_active_user
from services.agents.conflict_agent import compare_document_chunks

router = APIRouter(prefix="/v1/conflicts", tags=["conflicts"])


def _resolve_document_ids(
    doc_ids: list[str] | None,
    doc_names: list[str] | None,
    access_level: int,
) -> list[str]:
    """Resolve document IDs or names to validated, accessible IDs."""
    if not doc_ids and not doc_names:
        raise HTTPException(
            status_code=400, detail="Provide document_ids or document_names"
        )

    if doc_ids and doc_names:
        raise HTTPException(
            status_code=400,
            detail="Provide either document_ids or document_names, not both",
        )

    with get_db_session() as session:
        if doc_ids:
            docs = (
                session.query(Document)
                .filter(Document.id.in_(doc_ids), Document.ingestion_status == "ready")
                .all()
            )
            found_ids = {str(d.id) for d in docs}
            accessible_ids = {str(d.id) for d in docs if d.access_level <= access_level}
            missing = [did for did in doc_ids if did not in found_ids]
            restricted = [
                did for did in doc_ids if did in found_ids and did not in accessible_ids
            ]
            if missing:
                raise HTTPException(
                    status_code=404, detail=f"Documents not found: {missing}"
                )
            if restricted:
                raise HTTPException(
                    status_code=403, detail=f"Access denied for: {restricted}"
                )
            return list(accessible_ids)

        resolved = []
        for name in doc_names:
            matches = (
                session.query(Document)
                .filter(
                    Document.title.ilike(name.strip()),
                    Document.ingestion_status == "ready",
                    Document.access_level <= access_level,
                )
                .all()
            )
            if len(matches) == 0:
                raise HTTPException(
                    status_code=404, detail=f"Document not found: '{name}'"
                )
            if len(matches) > 1:
                candidates = [{"id": str(m.id), "title": m.title} for m in matches]
                raise HTTPException(
                    status_code=409,
                    detail=f"Ambiguous document name '{name}'. Matches: {candidates}",
                )
            resolved.append(str(matches[0].id))
        return resolved


@router.post("/compare", response_model=ConflictCompareResponse)
async def compare_documents(
    req: ConflictCompareRequest,
    current_user: dict = Depends(get_current_active_user),
):
    """
    Compare 2-3 documents for conflicting clauses.

    Accepts document_ids or document_names. Returns conflicts grouped
    by document pair with full clause evidence.
    """
    try:
        access_level = current_user.get("access_level", 1)
        doc_ids = _resolve_document_ids(
            req.document_ids, req.document_names, access_level
        )

        if len(doc_ids) < 2:
            raise HTTPException(
                status_code=400, detail="At least 2 accessible documents required"
            )
        if len(doc_ids) > 3:
            raise HTTPException(
                status_code=400, detail="Maximum 3 documents for comparison"
            )

        with get_db_session() as session:
            doc_titles = {}
            docs = session.query(Document).filter(Document.id.in_(doc_ids)).all()
            for d in docs:
                doc_titles[str(d.id)] = d.title

            chunks_by_doc: dict[str, list] = {}
            chunks = (
                session.query(Chunk)
                .filter(Chunk.document_id.in_(doc_ids))
                .order_by(Chunk.document_id, Chunk.section_path)
                .all()
            )
            for c in chunks:
                cid = str(c.document_id)
                chunks_by_doc.setdefault(cid, []).append(
                    {
                        "id": str(c.id),
                        "text": c.text,
                        "document_id": cid,
                        "clause_ref": c.clause_ref,
                        "section_path": c.section_path,
                        "title": doc_titles.get(cid, ""),
                        "similarity": 0.0,
                    }
                )

        result = compare_document_chunks(
            chunks_by_doc=chunks_by_doc,
            access_level=access_level,
            similarity_threshold=req.similarity_threshold,
            max_pairs=req.max_pairs,
            max_llm_calls=req.max_llm_calls,
        )

        return ConflictCompareResponse(
            doc_pairs=result["doc_pairs"],
            total_conflicts=result["total_conflicts"],
            total_candidates=result["total_candidates"],
            total_llm_calls=result["total_llm_calls"],
            similarity_threshold=req.similarity_threshold,
            truncated=result["truncated"],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Conflict comparison failed: {e}")
