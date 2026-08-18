"""
Query endpoints: submit a policy question and view history.
"""

import time
import uuid
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import joinedload

from codex.packages.shared.schemas import (
    QueryCreate, QueryResponse, AnswerResponse, CitationResponse, ConflictAnalysisResponse
)
from codex.packages.shared.db import get_db_session
from codex.packages.shared.models import Query, Answer, Citation, Chunk, Document, User
from codex.services.api.dependencies import (
    get_current_active_user, log_audit_action
)
from codex.packages.shared.config import HISTORY_MAX_TOKENS, HISTORY_TURNS

router = APIRouter(tags=["query"])

def _load_user_history(session, user_id_or_name: str, limit: int = HISTORY_TURNS):
    """
    Load the last N turns for a user as (question, answer) pairs,
    ordered oldest first (so they can be prepended in conversation order).
    """
    # Resolve username to UUID if necessary
    user_uuid = user_id_or_name
    if len(user_id_or_name) < 32:  # Simple heuristic: if it's not a UUID, it's a username
        user = session.query(User).filter(User.username == user_id_or_name).first()
        if user:
            user_uuid = str(user.id)

    q_rows = (
        session.query(Query)
        .filter(Query.user_id == user_uuid)
        .options(joinedload(Query.answers))
        .order_by(Query.created_at.desc())
        .limit(limit)
        .all()
    )

    turns = []
    for q in reversed(q_rows):  # oldest first
        if q.answers:
            a = q.answers[0]  # one answer per query
            turns.append({
                "question": q.question,
                "answer": a.answer,
                "intent": q.intent,
            })
    return turns


def _estimate_tokens(text: str) -> int:
    """Conservatively estimate tokens without loading a tokenizer."""
    return max(1, (len(text) + 3) // 4)


def _build_pipeline_question(
    question: str,
    history: list[dict],
    max_history_tokens: int = HISTORY_MAX_TOKENS,
) -> str:
    """Prepend the newest history that fits the working-memory budget.

    History is selected newest-first, then rendered in chronological order.
    The current question is never truncated or displaced by old answers.
    """
    if not history:
        return question

    selected = []
    used_tokens = 0
    for turn in reversed(history):
        rendered = f"User: {turn['question']}\nCodex: {turn['answer']}"
        turn_tokens = _estimate_tokens(rendered)
        if selected and used_tokens + turn_tokens > max_history_tokens:
            break
        if not selected and turn_tokens > max_history_tokens:
            # Keep a bounded tail of an oversized answer rather than allowing
            # one historical response to consume the entire context budget.
            available_chars = max(4, max_history_tokens * 4)
            question_text = str(turn.get("question", ""))
            answer_text = str(turn.get("answer", ""))
            prefix = f"User: {question_text}\nCodex: "
            answer_budget = max(0, available_chars - len(prefix))
            rendered = prefix + answer_text[:answer_budget]
            turn_tokens = _estimate_tokens(rendered)
        selected.append(rendered)
        used_tokens += turn_tokens

    history_text = "\n".join(reversed(selected))
    return f"Conversation History:\n{history_text}\n\nQuestion: {question}"


@router.post("/query", response_model=AnswerResponse)
async def secure_query(query_data: QueryCreate, current_user: dict = Depends(get_current_active_user)):
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

    # 1. Load user's conversation history from DB
    with get_db_session() as session:
        history = _load_user_history(session, username)

    pipeline_question = _build_pipeline_question(query_data.question, history)

    # 2. Log the query (store raw question, not the contextualized one)
    query_id = uuid.uuid4()
    try:
        with get_db_session() as session:
            query_record = Query(
                id=query_id,
                user_id=username,
                role=f"level_{level}",
                dept=department,
                question=query_data.question,
                intent=query_data.intent
            )
            session.add(query_record)
            session.commit()
    except Exception as e:
        print(f"⚠️ Query logging failed: {e}")

    # 3. Run through state machine orchestrator
    from services.agents.orchestrator import run_pipeline, QueryState, QueryIntent

    # Carry the previous turn's classified intent so bare follow-ups
    # ("for supplier termination?") inherit approval/conflict/... context.
    prior_intent = None
    if history:
        last_intent = history[-1].get("intent")
        if last_intent in {i.value for i in QueryIntent}:
            prior_intent = QueryIntent(last_intent)

    search_mode = query_data.search_mode or "hybrid"
    ctx = run_pipeline(
        question=pipeline_question,
        raw_question=query_data.question,
        user_id=username,
        access_level=level,
        department=department,
        search_mode=search_mode,
        prior_intent=prior_intent,
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
    answer_id = uuid.uuid4()
    try:
        with get_db_session() as session:
            # Persist the classified intent so the next turn can inherit it
            # as `prior_intent` for bare follow-up queries.
            q = session.query(Query).get(query_id)
            if q is not None:
                q.intent = ctx.intent.value

            answer_record = Answer(
                id=answer_id,
                query_id=query_id,
                answer=answer_text,
                verdict=verdict,
                confidence=confidence,
                abstained=abstained,
                latency_ms=latency_ms,
                model_version=os.getenv("LLM_MODEL", "hosted-api")
            )
            session.add(answer_record)

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

    # 6. Log audit action
    log_audit_action(username, "query", {
        "question": query_data.question[:100],
        "verdict": verdict,
        "confidence": confidence,
        "intent": ctx.intent.value,
    })

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
            quote=chunk.get("text")
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
                status="inconclusive" if ctx.conflict_analysis.get("inconclusive") else "complete",
                conflicts=ctx.conflict_analysis.get("conflicts", []),
                total_candidates=ctx.conflict_analysis.get("total_candidates", 0),
                checked_candidates=ctx.conflict_analysis.get("checked_candidates", 0),
                unchecked_candidates=ctx.conflict_analysis.get("unchecked_candidates", 0),
                llm_calls=ctx.conflict_analysis.get("llm_calls", 0),
                truncated=ctx.conflict_analysis.get("truncated", False),
                inconclusive=ctx.conflict_analysis.get("inconclusive", False),
            )
            if ctx.intent.value == "conflict" and ctx.conflict_analysis
            else None
        ),
        next_steps=ctx.build_next_steps(),
        missing=ctx.build_missing(),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S")
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
                .filter(Query.user_id == user_uuid)
                .options(
                    joinedload(Query.answers).joinedload(Answer.citations)
                    .joinedload(Citation.chunk).joinedload(Chunk.document)
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
                            title=c.chunk.document.title if c.chunk and c.chunk.document else None,
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
    summary = {"queries_deleted": 0, "answers_deleted": 0, "citations_deleted": 0, "feedback_deleted": 0}

    try:
        with get_db_session() as session:
            # Authorize as the current user (defense against user_id spoofing)
            summary["citations_deleted"] = session.execute(
                sa_text("""
                    DELETE FROM citations
                    WHERE answer_id IN (
                        SELECT a.id FROM answers a
                        JOIN queries q ON q.id = a.query_id
                        WHERE q.user_id = :uid
                    )
                """),
                {"uid": username},
            ).rowcount
            summary["feedback_deleted"] = session.execute(
                sa_text("""
                    DELETE FROM feedback
                    WHERE answer_id IN (
                        SELECT a.id FROM answers a
                        JOIN queries q ON q.id = a.query_id
                        WHERE q.user_id = :uid
                    )
                """),
                {"uid": username},
            ).rowcount
            summary["answers_deleted"] = session.execute(
                sa_text("""
                    DELETE FROM answers
                    WHERE query_id IN (
                        SELECT id FROM queries WHERE user_id = :uid
                    )
                """),
                {"uid": username},
            ).rowcount
            summary["queries_deleted"] = session.execute(
                sa_text("DELETE FROM queries WHERE user_id = :uid"),
                {"uid": username},
            ).rowcount
            session.commit()
    except Exception as e:
        print(f"⚠️ Failed to clear query history: {e}")
        with get_db_session() as session:
            session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to clear query history: {e}")

    log_audit_action(username, "clear_history", summary)
    return summary
