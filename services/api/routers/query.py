"""
Query endpoints: submit a policy question and view history.
"""

import time
import uuid
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import joinedload

from packages.shared.schemas import (
    QueryCreate,
    QueryResponse,
    AnswerResponse,
    CitationResponse,
    ConflictAnalysisResponse,
    ResolvedDoc,
    DocumentSlot,
)
from packages.shared.db import get_db_session
from packages.shared.models import Query, Answer, Citation, Chunk, Document, User
from services.api.dependencies import get_current_active_user, log_audit_action

router = APIRouter(tags=["query"])


@router.post("/query", response_model=AnswerResponse)
async def secure_query(
    query_data: QueryCreate, current_user: dict = Depends(get_current_active_user)
):
    """
    Process a policy query through the state machine orchestrator.
    Routes to specialized agents based on intent classification.
    """
    username = current_user.get("username")
    level = current_user.get("access_level", 1)

    # Look up department from DB
    department = "Unknown"
    try:
        with get_db_session() as session:
            user = session.query(User).filter(User.username == username).first()
            if user:
                department = user.department
    except Exception:
        pass

    # 1. Log the query
    query_id = str(uuid.uuid4())
    try:
        with get_db_session() as session:
            query_record = Query(
                id=query_id,
                user_id=username,
                role=f"level_{level}",
                dept=department,
                question=query_data.question,
                intent=query_data.intent,
            )
            session.add(query_record)
            session.commit()
    except Exception as e:
        print(f"⚠️ Query logging failed: {e}")

    # 2. Run through state machine orchestrator
    from services.agents.orchestrator import run_pipeline, QueryState, QueryIntent

    search_mode = query_data.search_mode or "hybrid"
    selected_doc_ids = query_data.selected_doc_ids

    ctx = run_pipeline(
        question=query_data.question,
        raw_question=query_data.question,
        user_id=username,
        access_level=level,
        department=department,
        search_mode=search_mode,
        selected_doc_ids=query_data.selected_doc_ids,
    )

    # 4. Extract results from context
    from services.api.formatters import format_answer

    answer_text = format_answer(ctx.answer)
    verdict = ctx.verdict
    confidence = ctx.confidence
    latency_ms = ctx.latency_ms
    chunks = ctx.chunks
    abstained = ctx.state == QueryState.ABSTAINED

    # 5. Log the answer
    answer_id = str(uuid.uuid4())
    try:
        with get_db_session() as session:
            q = session.query(Query).get(query_id)
            if q is not None:
                q.intent = ctx.intent.value
                q.tool_selection = ctx.tool_selection

            answer_record = Answer(
                id=answer_id,
                query_id=query_id,
                answer=answer_text,
                verdict=verdict,
                confidence=confidence,
                abstained=abstained,
                latency_ms=latency_ms,
                model_version=os.getenv("LLM_MODEL", "hosted-api"),
            )
            session.add(answer_record)

            for chunk in chunks:
                citation = Citation(
                    answer_id=answer_id,
                    chunk_id=chunk.get("id", str(uuid.uuid4())),
                    document_id=chunk.get("document_id", str(uuid.uuid4())),
                    clause_ref=chunk.get("clause_ref"),
                    score=chunk.get("score", 0.0),
                )
                session.add(citation)

            session.commit()
    except Exception as e:
        print(f"⚠️ Answer logging failed: {e}")

    # 6. Log audit action
    log_audit_action(
        username,
        "query",
        {
            "question": query_data.question[:100],
            "verdict": verdict,
            "confidence": confidence,
            "intent": ctx.intent.value,
        },
    )

    # 7. Build response
    citations_response = [
        CitationResponse(
            id=str(uuid.uuid4()),
            chunk_id=chunk.get("id", ""),
            document_id=chunk.get("document_id", ""),
            title=chunk.get("title"),
            clause_ref=chunk.get("clause_ref"),
            score=chunk.get("score", 0.0),
            section_path=chunk.get("section_path"),
            quote=chunk.get("text"),
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
        reasoning=ctx.build_reasoning(),
        conflict_analysis=(
            ConflictAnalysisResponse(
                status="inconclusive"
                if ctx.conflict_analysis.get("inconclusive")
                else "complete",
                conflicts=ctx.conflict_analysis.get("conflicts", []),
                total_candidates=ctx.conflict_analysis.get("total_candidates", 0),
                checked_candidates=ctx.conflict_analysis.get("checked_candidates", 0),
                unchecked_candidates=ctx.conflict_analysis.get(
                     "unchecked_candidates", 0
                ),
                llm_calls=ctx.conflict_analysis.get("llm_calls", 0),
                truncated=ctx.conflict_analysis.get("truncated", False),
                inconclusive=ctx.conflict_analysis.get("inconclusive", False),
                evaluated_top_k=ctx.conflict_analysis.get("evaluated_top_k", False),
                coverage_note=ctx.conflict_analysis.get("coverage_note"),
                genuine_failures=ctx.conflict_analysis.get("genuine_failures", 0),
            )
            if ctx.intent.value == "conflict" and ctx.conflict_analysis
            else None
        ),
        resolved_documents=[
            ResolvedDoc(
                id=r.get("id", ""),
                title=r.get("title", ""),
                similarity=round(r.get("similarity", 0.0), 3),
                selected=r.get("selected", True),
            )
            for r in ctx.resolved_documents
            if r.get("id")
        ] if ctx.resolved_documents else None,
        document_slots=[
            DocumentSlot(
                slot=s["slot"],
                phrase=s["phrase"],
                candidates=[
                    ResolvedDoc(
                        id=c["id"],
                        title=c["title"],
                        similarity=round(c["similarity"], 3),
                        selected=c.get("selected", False),
                    )
                    for c in s["candidates"]
                ],
            )
            for s in ctx.doc_slots
        ] if ctx.doc_slots else None,
        next_steps=ctx.build_next_steps(),
        missing=ctx.build_missing(),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        tool_selection=ctx.tool_selection,
    )


@router.get("/query/history", response_model=list[QueryResponse])
async def get_query_history(
    limit: int = 500,
    current_user: dict = Depends(get_current_active_user),
):
    """Get query history (with citations) for the current user, newest first."""
    username = current_user.get("username")

    try:
        with get_db_session() as session:
            # Resolve username to UUID
            user = session.query(User).filter(User.username == username).first()
            user_uuid = str(user.id) if user else username

            queries = (
                session.query(Query)
                .filter(Query.user_id.in_([user_uuid, username]))
                .options(
                    joinedload(Query.answers)
                    .joinedload(Answer.citations)
                    .joinedload(Citation.chunk)
                    .joinedload(Chunk.document)
                )
                .order_by(Query.created_at.desc())
                .limit(limit)
                .all()
            )

            return [
                QueryResponse(
                    id=str(q.id),
                    user_id=q.user_id,
                    question=q.question,
                    intent=q.intent,
                    created_at=q.created_at.isoformat() if q.created_at else "",
                    answer=q.answers[0].answer if q.answers else None,
                    verdict=q.answers[0].verdict if q.answers else None,
                    confidence=q.answers[0].confidence if q.answers else None,
                    citations=[
                        CitationResponse(
                            id=str(c.id),
                            chunk_id=str(c.chunk_id) if c.chunk_id else "",
                            document_id=str(c.document_id) if c.document_id else "",
                            title=c.chunk.document.title
                            if c.chunk and c.chunk.document
                            else None,
                            clause_ref=c.clause_ref,
                            score=c.score,
                            section_path=c.chunk.section_path if c.chunk else None,
                            quote=c.chunk.text if c.chunk else None,
                        )
                        for c in (q.answers[0].citations if q.answers else [])
                    ],
                )
                for q in queries
            ]
    except Exception as e:
        print(f"⚠️ Failed to fetch query history: {e}")
        return []


@router.delete("/query/history")
async def clear_query_history(current_user: dict = Depends(get_current_active_user)):
    """Permanently delete the current user's chat history (queries, answers, citations, feedback)."""
    from sqlalchemy import text as sa_text

    username = current_user.get("username")
    summary = {
        "queries_deleted": 0,
        "answers_deleted": 0,
        "citations_deleted": 0,
        "feedback_deleted": 0,
    }

    try:
        with get_db_session() as session:
            user = session.query(User).filter(User.username == username).first()
            user_uuid = str(user.id) if user else username

            summary["citations_deleted"] = session.execute(
                sa_text("""
                    DELETE FROM citations
                    WHERE answer_id IN (
                        SELECT a.id FROM answers a
                        JOIN queries q ON q.id = a.query_id
                        WHERE q.user_id IN (:uid1, :uid2)
                    )
                """),
                {"uid1": username, "uid2": user_uuid},
            ).rowcount
            summary["feedback_deleted"] = session.execute(
                sa_text("""
                    DELETE FROM feedback
                    WHERE answer_id IN (
                        SELECT a.id FROM answers a
                        JOIN queries q ON q.id = a.query_id
                        WHERE q.user_id IN (:uid1, :uid2)
                    )
                """),
                {"uid1": username, "uid2": user_uuid},
            ).rowcount
            summary["answers_deleted"] = session.execute(
                sa_text("""
                    DELETE FROM answers
                    WHERE query_id IN (
                        SELECT id FROM queries WHERE user_id IN (:uid1, :uid2)
                    )
                """),
                {"uid1": username, "uid2": user_uuid},
            ).rowcount
            summary["queries_deleted"] = session.execute(
                sa_text("DELETE FROM queries WHERE user_id IN (:uid1, :uid2)"),
                {"uid1": username, "uid2": user_uuid},
            ).rowcount
            session.commit()
    except Exception as e:
        print(f"⚠️ Failed to clear query history: {e}")
        with get_db_session() as session:
            session.rollback()
        raise HTTPException(
            status_code=500, detail=f"Failed to clear query history: {e}"
        )

    log_audit_action(username, "clear_history", summary)
    return summary
