"""
Health and feedback endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from packages.shared.schemas import HealthResponse, FeedbackCreate, FeedbackResponse
from packages.shared.db import get_db_session
from packages.shared.models import Answer, Feedback, Query
from services.api.dependencies import get_current_active_user, log_audit_action

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Check system health."""
    db_status = "unknown"
    reasoner_status = "unknown"
    ingestion_llm_status = "unknown"

    try:
        with get_db_session() as session:
            session.execute(text("SELECT 1"))
            db_status = "healthy"
    except Exception:
        db_status = "unavailable"

    try:
        from packages.shared.llm_client import health_check

        reasoner_status = "healthy" if health_check() else "unhealthy"
    except Exception:
        reasoner_status = "unavailable"

    try:
        from packages.shared.config import LLM_BASE_URL

        ingestion_llm_status = "configured" if LLM_BASE_URL else "unconfigured"
    except Exception:
        ingestion_llm_status = "unavailable"

    return HealthResponse(
        status="healthy" if db_status == "healthy" else "degraded",
        database=db_status,
        llama_server=reasoner_status,
        qwen_server=ingestion_llm_status,
    )


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    feedback_data: FeedbackCreate, current_user: dict = Depends(get_current_active_user)
):
    """Submit feedback for an answer."""
    username = current_user.get("username")

    try:
        with get_db_session() as session:
            answer = (
                session.query(Answer)
                .join(Query, Answer.query_id == Query.id)
                .filter(Answer.id == feedback_data.answer_id, Query.user_id == username)
                .first()
            )
            if not answer:
                raise HTTPException(status_code=404, detail="Answer not found")

            feedback = (
                session.query(Feedback)
                .filter(
                    Feedback.answer_id == feedback_data.answer_id,
                    Feedback.reviewer == username,
                )
                .first()
            )
            if feedback is None:
                feedback = Feedback(
                    answer_id=feedback_data.answer_id,
                    rating=feedback_data.rating,
                    reviewer=username,
                )
                session.add(feedback)
            else:
                feedback.rating = feedback_data.rating
            session.commit()

            log_audit_action(
                username,
                "feedback",
                {"answer_id": feedback_data.answer_id, "rating": feedback_data.rating},
            )

            return FeedbackResponse(
                id=str(feedback.id),
                answer_id=str(feedback.answer_id),
                rating=feedback.rating,
                reviewer=feedback.reviewer,
                created_at=feedback.created_at.isoformat()
                if feedback.created_at
                else "",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit feedback: {e}")


@router.get("/feedback/{answer_id}", response_model=list[FeedbackResponse])
async def get_feedback_for_answer(
    answer_id: str, current_user: dict = Depends(get_current_active_user)
):
    """Get feedback for a specific answer."""
    try:
        with get_db_session() as session:
            feedbacks = (
                session.query(Feedback)
                .join(Answer, Feedback.answer_id == Answer.id)
                .join(Query, Answer.query_id == Query.id)
                .filter(
                    Feedback.answer_id == answer_id,
                    Query.user_id == current_user.get("username"),
                )
                .all()
            )

            return [
                FeedbackResponse(
                    id=str(f.id),
                    answer_id=str(f.answer_id),
                    rating=f.rating,
                    reviewer=f.reviewer,
                    created_at=f.created_at.isoformat() if f.created_at else "",
                )
                for f in feedbacks
            ]
    except Exception as e:
        print(f"⚠️ Failed to fetch feedback: {e}")
        return []
