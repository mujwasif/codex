"""
Pure Python State Machine Orchestrator

Routes queries through a chain of specialized agents based on intent.
No LangChain/LangGraph — just a simple state machine with shared state.

States:
  IDLE → CLASSIFIED → RETRIEVED → REASONED → VERIFIED → DONE
  Any failure → ABSTAINED

Shared state carries: question, intent, chunks, answer, verdict, confidence, citations.
"""

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


class QueryState(enum.Enum):
    IDLE = "idle"
    CLASSIFIED = "classified"
    RETRIEVED = "retrieved"
    REASONED = "reasoned"
    VERIFIED = "verified"
    DONE = "done"
    ABSTAINED = "abstained"


class QueryIntent(enum.Enum):
    GENERAL = "general"
    APPROVAL = "approval"
    CONFLICT = "conflict"
    COMPLIANCE = "compliance"
    PROCEDURE = "procedure"


@dataclass
class QueryContext:
    """Shared state object carried through the pipeline."""
    question: str
    user_id: str
    access_level: int = 1
    department: str = ""

    state: QueryState = QueryState.IDLE
    intent: QueryIntent = QueryIntent.GENERAL
    intent_confidence: float = 0.0

    chunks: List[Dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    verdict: str = "abstained"
    confidence: float = 0.0
    citations: List[Dict[str, Any]] = field(default_factory=list)
    search_mode: str = "hybrid"
    latency_ms: int = 0

    # KG-enriched fields (filled by specialized agents)
    approval_result: Optional[Dict[str, Any]] = None
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    risk_result: Optional[Dict[str, Any]] = None

    error: Optional[str] = None
    start_time: float = 0.0

    def start_timer(self):
        self.start_time = time.time()

    def stop_timer(self):
        self.latency_ms = int((time.time() - self.start_time) * 1000)

    def fail(self, error: str):
        self.error = error
        self.state = QueryState.ABSTAINED
        self.verdict = "abstained"
        self.confidence = 0.0


def classify_intent(question: str) -> tuple[QueryIntent, float]:
    """
    Classify the user's intent from their question.
    Uses keyword matching for speed, with LLM fallback for ambiguous queries.

    Returns (intent, confidence).
    """
    q = question.lower()

    # Approval keywords
    approval_kw = [
        "who can approve", "approval authority", "who approves",
        "needs approval", "requires approval", "approval required",
        "who authorizes", "authorization", "can approve",
        "who signs off", "sign-off", "approval matrix",
        "approval chain", "approval limit"
    ]
    if any(kw in q for kw in approval_kw):
        return QueryIntent.APPROVAL, 0.9

    # Conflict keywords
    conflict_kw = [
        "conflict", "contradict", "inconsisten", "versus", "vs",
        "differs from", "contrary to", "overrides", "supersedes",
        "version change", "updated version", "old version", "new version"
    ]
    if any(kw in q for kw in conflict_kw):
        return QueryIntent.CONFLICT, 0.85

    # Compliance keywords
    compliance_kw = [
        "compliance", "violation", "breach", "regulation", "regulatory",
        "gdpr", "ndpa", "iso 27001", "nist", "hipaa", "sox",
        "are we compliant", "compliant with", "non-compliant",
        "legal", "law", "regulation", "standard", "requirement",
        "mandatory", "must we", "do we need"
    ]
    if any(kw in q for kw in compliance_kw):
        return QueryIntent.COMPLIANCE, 0.85

    # Procedure keywords
    procedure_kw = [
        "how to", "how do i", "what is the process", "procedure",
        "steps", "workflow", "what should", "how often", "when should",
        "what happens if", "what do i do", "process for", "steps to"
    ]
    if any(kw in q for kw in procedure_kw):
        return QueryIntent.PROCEDURE, 0.8

    return QueryIntent.GENERAL, 0.6


# ═══════════════════════════════════════
#  Agent Functions
# ═══════════════════════════════════════

def agent_retrieve(ctx: QueryContext):
    """Retrieve relevant chunks via hybrid search."""
    from services.api.search import search_policy
    from services.api.main import bm25_index

    try:
        ctx.chunks = search_policy(
            ctx.question,
            access_level=ctx.access_level,
            search_mode=ctx.search_mode,
            bm25_index=bm25_index,
        )
        ctx.state = QueryState.RETRIEVED
    except Exception as e:
        ctx.fail(f"Retrieval failed: {e}")


def agent_reason(ctx: QueryContext):
    """Generate grounded answer from retrieved chunks."""
    from services.agents.reasoner import generate_grounded_answer

    try:
        if not ctx.chunks:
            ctx.fail("No relevant policy clauses found")
            return

        ctx.answer = generate_grounded_answer(ctx.question, ctx.chunks)
        ctx.state = QueryState.REASONED
    except Exception as e:
        ctx.fail(f"Reasoning failed: {e}")


def agent_verify(ctx: QueryContext):
    """Verify citations against retrieved chunks."""
    from services.agents.verifier import verify_citations
    from services.api.main import compute_confidence

    try:
        is_valid, msg = verify_citations(ctx.answer, ctx.chunks)

        # Build citations list
        ctx.citations = []
        for chunk in ctx.chunks:
            ctx.citations.append({
                "document": chunk.get("title", ""),
                "version": "v1",
                "section": chunk.get("section_path", ""),
                "clause": chunk.get("clause_ref", ""),
                "score": chunk.get("score", 0.0),
            })

        # Determine verdict
        if not is_valid or "Insufficient" in ctx.answer:
            ctx.verdict = "abstained"
        else:
            ctx.verdict = "clear"

        ctx.confidence = compute_confidence(ctx.chunks, is_valid, ctx.answer)
        ctx.state = QueryState.VERIFIED
    except Exception as e:
        ctx.fail(f"Verification failed: {e}")


def agent_approval(ctx: QueryContext):
    """Query the knowledge graph for approval authority."""
    from services.agents.approval_agent import resolve_approval

    try:
        ctx.approval_result = resolve_approval(ctx.question, ctx.chunks)
        if ctx.approval_result:
            ctx.state = QueryState.REASONED
    except Exception as e:
        ctx.approval_result = {"error": str(e)}


def agent_conflict_check(ctx: QueryContext):
    """Check for conflicts between clauses."""
    from services.agents.conflict_agent import detect_conflicts_in_chunks

    try:
        ctx.conflicts = detect_conflicts_in_chunks(ctx.chunks)
        ctx.state = QueryState.REASONED
    except Exception as e:
        ctx.conflicts = []


def agent_risk_compliance(ctx: QueryContext):
    """Classify risk and map to regulations."""
    from services.agents.risk_agent import assess_risk

    try:
        ctx.risk_result = assess_risk(ctx.question, ctx.chunks)
        ctx.state = QueryState.REASONED
    except Exception as e:
        ctx.risk_result = {"error": str(e)}


# ═══════════════════════════════════════
#  Pipeline Definition
# ═══════════════════════════════════════

# Default pipeline: retrieve → reason → verify
DEFAULT_PIPELINE = [agent_retrieve, agent_reason, agent_verify]

# Intent-specific pipelines
INTENT_PIPELINES = {
    QueryIntent.APPROVAL: [agent_retrieve, agent_approval, agent_reason, agent_verify],
    QueryIntent.CONFLICT: [agent_retrieve, agent_conflict_check, agent_reason, agent_verify],
    QueryIntent.COMPLIANCE: [agent_retrieve, agent_risk_compliance, agent_reason, agent_verify],
    QueryIntent.PROCEDURE: [agent_retrieve, agent_reason, agent_verify],
    QueryIntent.GENERAL: DEFAULT_PIPELINE,
}


# ═══════════════════════════════════════
#  Orchestrator Entry Point
# ═══════════════════════════════════════

def run_pipeline(
    question: str,
    user_id: str,
    access_level: int = 1,
    department: str = "",
    search_mode: str = "hybrid",
) -> QueryContext:
    """
    Run the full query pipeline through the state machine.

    1. Classify intent
    2. Select pipeline based on intent
    3. Execute agents in sequence
    4. Return enriched QueryContext
    """
    ctx = QueryContext(
        question=question,
        user_id=user_id,
        access_level=access_level,
        department=department,
        search_mode=search_mode,
    )
    ctx.start_timer()

    # Step 1: Classify intent
    ctx.intent, ctx.intent_confidence = classify_intent(question)
    ctx.state = QueryState.CLASSIFIED

    # Step 2: Select pipeline
    pipeline = INTENT_PIPELINES.get(ctx.intent, DEFAULT_PIPELINE)

    # Step 3: Execute agents in sequence
    for agent_fn in pipeline:
        if ctx.state == QueryState.ABSTAINED:
            break
        agent_fn(ctx)

    # Step 4: Final state
    if ctx.state != QueryState.ABSTAINED:
        ctx.state = QueryState.DONE

    ctx.stop_timer()
    return ctx
