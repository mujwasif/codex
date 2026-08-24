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
import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from services.agents.tools.llm_tools import llm_generate, QWEN3_8B_MODEL


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
    raw_question: str = ""

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
    conflict_analysis: Dict[str, Any] = field(default_factory=dict)
    risk_result: Optional[Dict[str, Any]] = None
    procedure_results: Optional[Dict[str, Any]] = None

    # Agent chain trace (recorded by run_pipeline)
    chain: List[Dict[str, Any]] = field(default_factory=list)


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

    def build_reasoning(self) -> Dict[str, Any]:
        """Assemble the agent chain trace for the answer payload."""
        reasoning = {
            "intent": self.intent.value,
            "intent_confidence": self.intent_confidence,
            "search_mode": self.search_mode,
            "agents": self.chain,
            "chunks_found": len(self.chunks),
        }
        if self.error:
            reasoning["error"] = self.error
        if self.approval_result:
            res = self.approval_result
            reasoning["approval"] = {
                "process": res.get("process"),
                "matching_roles": res.get("matching_roles", []),
                "matching_processes": res.get("matching_processes", []),
                "user_can_approve": res.get("user_can_approve", False),
                "approvable": res.get("approvable", False),
                "amount": res.get("amount"),
            }
        if self.conflicts:
            reasoning["conflicts"] = self.conflicts
        if self.conflict_analysis:
            reasoning["conflict_analysis"] = {
                key: value for key, value in self.conflict_analysis.items()
                if key not in {"source_clauses", "corpus_candidates", "evidence", "conflicts"}
            }
        if self.risk_result:
            reasoning["risk"] = {
                "verdict": self.risk_result.get("verdict"),
                "risk_level": self.risk_result.get("risk_level"),
                "regulations": self.risk_result.get("regulations", []),
                "obligations": self.risk_result.get("obligations", {}),
            }
        return reasoning

    def build_approval_answer(self) -> str:
        """Prose answer for an approval intent, built from the graph result."""
        if not self.approval_result:
            return ""
        res = self.approval_result
        if res.get("error"):
            return f"Approval resolution failed: {res['error']}"

        process = res.get("process")
        roles = res.get("matching_roles", [])
        amount = res.get("amount")
        user_can = res.get("user_can_approve", False)

        lines = []
        if process:
            if roles:
                lines.append(f"Approval authority for '{process}': {', '.join(roles)}.")
            else:
                lines.append(
                    f"No approval process matching your question was found in the knowledge graph."
                )
            if amount:
                lines.append(f"Requested amount: ${amount:,.2f}.")
        if roles:
            if user_can:
                lines.append(
                    f"Your role has sufficient authority to approve this action."
                )
            else:
                lines.append(
                    f"Approval from {', '.join(roles)} is required."
                )
        elif not process:
            lines.append(
                "The policy corpus does not define an approval chain for this action."
            )
        return " ".join(lines)

    def build_conflict_answer(self) -> str:
        """Human-readable conflict answer with full detail and recommendations."""
        if not self.conflicts:
            return ""

        count = len(self.conflicts)
        lines = [f"I found {count} {'conflict' if count == 1 else 'conflicts'} between your policies:\n"]

        for i, c in enumerate(self.conflicts, 1):
            ca = c.get("clause_a", {})
            cb = c.get("clause_b", {})
            reason = c.get("reason", "")

            title_a = ca.get("document_title", "") if isinstance(ca, dict) else ""
            title_b = cb.get("document_title", "") if isinstance(cb, dict) else ""
            ref_a = ca.get("clause_ref", "") if isinstance(ca, dict) else ""
            ref_b = cb.get("clause_ref", "") if isinstance(cb, dict) else ""
            text_a = ca.get("text", "") if isinstance(ca, dict) else ""
            text_b = cb.get("text", "") if isinstance(cb, dict) else ""
            section_a = ca.get("section_path", "") if isinstance(ca, dict) else ""
            section_b = cb.get("section_path", "") if isinstance(cb, dict) else ""

            topic = self._extract_topic(text_a, text_b, reason)
            lines.append(f"{i}. **{topic}**")

            if text_a and len(text_a) > 10:
                snippet_a = text_a[:120].rstrip(".")
                lines.append(f'   {title_a or "Document A"} says: "{snippet_a}."')
            if text_b and len(text_b) > 10:
                snippet_b = text_b[:120].rstrip(".")
                lines.append(f'   {title_b or "Document B"} says: "{snippet_b}."')

            if reason:
                lines.append(f"   Conflict: {reason}.")

            status = c.get("status", "confirmed_conflict")
            if status == "superseded":
                lines.append("   Precedence: the available version metadata indicates that one requirement supersedes the other.")
            elif status == "possible_conflict":
                lines.append("   Uncertainty: the clauses may conflict, but the available metadata does not establish the outcome conclusively.")

            src_parts = []
            if title_a:
                src_parts.append(f"{title_a} §{ref_a}" if ref_a else title_a)
            if title_b:
                src_parts.append(f"{title_b} §{ref_b}" if ref_b else title_b)
            if src_parts:
                lines.append(f"   Source: {' vs '.join(src_parts)}")
            if title_a and ref_a:
                lines.append(f"   Citation A: [Doc: {title_a}, Clause: {ref_a}]")
            if title_b and ref_b:
                lines.append(f"   Citation B: [Doc: {title_b}, Clause: {ref_b}]")

            lines.append("")

        if self.conflict_analysis.get("inconclusive"):
            lines.append("Conflict analysis was inconclusive because some candidate clauses could not be evaluated.")
        lines.append(self._build_conflict_recommendation())
        return "\n".join(lines)

    def _extract_topic(self, text_a: str, text_b: str, reason: str) -> str:
        """Extract a short topic label from clause texts or reason."""
        combined = (text_a + " " + text_b + " " + reason).lower()
        for keyword, label in [
            ("password", "Password Policy"),
            ("retention", "Data Retention"),
            ("multi-factor", "Multi-Factor Authentication"),
            ("mfa", "Multi-Factor Authentication"),
            ("access log", "Access Logging"),
            ("access must", "Access Control"),
            ("vendor data", "Vendor Data Retention"),
            ("vendor access", "Vendor Access"),
            ("compliance", "Compliance"),
            ("audit", "Audit Requirements"),
            ("badge", "Badge / Physical Access"),
            ("onboarding", "Onboarding"),
            ("termination", "Termination"),
            ("incident", "Incident Response"),
            ("encryption", "Encryption"),
            ("backup", "Backup Policy"),
            ("data must", "Data Handling"),
            ("employee data", "Employee Data"),
            ("record", "Record Keeping"),
            ("review", "Review Cycle"),
            ("security", "Security Standard"),
        ]:
            if keyword in combined:
                return label
        return "Policy Conflict"

    def _build_conflict_recommendation(self) -> str:
        """Generate a recommendation based on detected conflicts."""
        if not self.conflicts:
            return ""

        count = len(self.conflicts)
        lines = ["**Recommendation:**"]

        if count == 1:
            lines.append(
                "Review the conflicting clauses above and align them to the stricter "
                "standard, or document an approved exception with the policy owner."
            )
        else:
            lines.append(
                f"With {count} conflicts detected across your policies, I recommend "
                "a policy alignment review. Prioritize aligning to the stricter standard "
                "for each conflict to reduce compliance risk."
            )

        doc_titles = set()
        for c in self.conflicts:
            ca = c.get("clause_a", {})
            cb = c.get("clause_b", {})
            if isinstance(ca, dict) and ca.get("document_title"):
                doc_titles.add(ca["document_title"])
            if isinstance(cb, dict) and cb.get("document_title"):
                doc_titles.add(cb["document_title"])
        if doc_titles:
            lines.append(f"Policies involved: {', '.join(sorted(doc_titles))}.")

        return "\n".join(lines)

    def build_risk_answer(self) -> str:
        """Prose answer for a compliance intent, built from the risk assessment."""
        if not self.risk_result:
            return ""
        res = self.risk_result
        verdict = res.get("verdict", "unknown")
        risk_level = res.get("risk_level", "unknown")
        regulations = res.get("regulations", [])
        obligations = res.get("obligations", {})
        recommendations = res.get("recommendations", [])

        lines = []
        if verdict == "clear":
            lines.append("No compliance issues detected.")
        elif verdict == "conditional":
            lines.append("Compliance is conditional — certain conditions must be satisfied.")
        elif verdict == "violation":
            lines.append("A policy/regulation violation was detected.")
        elif verdict == "abstained":
            lines.append("Insufficient policy basis to assess compliance.")
        lines.append(f"Risk level: {risk_level}.")

        if regulations:
            lines.append(f"Applicable regulations: {', '.join(regulations)}.")
        mandatory = sum(1 for v in obligations.values() if v == "mandatory")
        if mandatory:
            lines.append(f"{mandatory} mandatory obligation(s) apply to the retrieved clauses.")
        if recommendations:
            lines.append("Recommended actions: " + "; ".join(recommendations) + ".")
        return " ".join(lines)

    def build_next_steps(self) -> List[str]:
        """Actionable recommendations surfaced to the user."""
        steps: List[str] = []
        if self.risk_result:
            steps.extend(self.risk_result.get("recommendations", []))
        if self.approval_result:
            roles = self.approval_result.get("matching_roles", [])
            process = self.approval_result.get("process")
            if roles:
                steps.append(
                    f"Approval required: contact {', '.join(roles)} for '{process}'"
                )
            if self.approval_result.get("user_can_approve") is False and roles:
                steps.append("You do not have sufficient authority for this approval.")
        if not steps and self.verdict == "clear":
            steps.append("No further action required.")
        return steps

    def build_missing(self) -> List[str]:
        """Required approvals/docs not satisfied by the current answer."""
        missing_items: List[str] = []
        if self.approval_result and self.approval_result.get("matching_roles"):
            missing_items.append(
                f"Approval from {', '.join(self.approval_result['matching_roles'])} is required"
            )
        if self.verdict == "abstained":
            missing_items.append("Sufficient policy basis was not found in the current corpus")
        return missing_items


def _keyword_classify(question: str) -> tuple[QueryIntent, float]:
    """Deterministic keyword heuristic. Returns (intent, confidence)."""
    q = question.lower()

    approval_kw = ("approv", "author", "sign-off", "sign off", "escalate", "who approves",
                   "who can", "who author", "who signs", "needs approval", "requires approval",
                   "approval matrix", "approval chain", "approval limit", "approval authority")
    conflict_kw = ("conflict", "contradict", "inconsisten", "versus", " v2", " v1", "override",
                   "supersede", "differs from", "contrary", "version change", "updated version",
                   "old version", "new version", "clash", "mismatch", "disagreement")
    compliance_kw = ("complian", "violat", "breach", "regulat", "gdpr", "ndpa", "iso 27001",
                     "nist", "hipaa", "sox", "legal", "standard", "mandatory", "penalty",
                     "fine", "sanction", "audit", "obligation", "must we", "are we compliant")
    procedure_kw = ("how to", "how do", "how can", "procedure", "steps", "workflow", "what should",
                    "how often", "when should", "what happens", "what do i", "process for",
                    "walk me through", "guide me", "instructions", "step by step")

    for kw in approval_kw:
        if kw in q:
            return QueryIntent.APPROVAL, 0.6
    for kw in conflict_kw:
        if kw in q:
            return QueryIntent.CONFLICT, 0.6
    for kw in compliance_kw:
        if kw in q:
            return QueryIntent.COMPLIANCE, 0.6
    for kw in procedure_kw:
        if kw in q:
            return QueryIntent.PROCEDURE, 0.6
    return QueryIntent.GENERAL, 0.5


def agent_procedure_reason(ctx: QueryContext):
    """
    Dedicated reasoning for procedural queries.
    Bypasses the general reasoner to produce a structured workflow.
    """
    from services.agents.procedure_agent import ProcedureAgent
    
    if ctx.intent != QueryIntent.PROCEDURE:
        return
        
    try:
        proc_agent = ProcedureAgent()
        workflow = proc_agent.process(ctx.question, ctx.chunks)
        
        if workflow:
            ctx.answer = workflow
            ctx.state = QueryState.REASONED
        else:
            # Fallback: if the dedicated agent can't form a workflow, 
            # we mark it as RETRIEVED so the general agent_reason can try.
            ctx.state = QueryState.RETRIEVED
    except Exception as e:
        logger.error(f"Procedure reasoning failed: {e}")
        ctx.state = QueryState.RETRIEVED

_FOLLOWUP_START = (
    "for ", "and ", "and for ", "what about ", "how about ", "regarding ",
    "what about the ", "and the ", "and what about ", "about the ", "for the ",
    "also ", "same for ", "likewise ", "similar for ", "what if ",
)


def _is_bare_followup(question: str) -> bool:
    """True when a question is a qualifier/relative phrase lacking a kernel verb."""
    q = question.strip().lower().rstrip("?.!")
    if not q:
        return False
    return any(q.startswith(prefix) for prefix in _FOLLOWUP_START)


def classify_intent(
    question: str,
    prior_intent: Optional[QueryIntent] = None,
) -> tuple[QueryIntent, float]:
    """
    Classify the user's intent from their question using the LLM.
    Falls back to deterministic keyword matching when the LLM is
    unavailable or returns a low-confidence (< 0.7) result.
    Returns (intent, confidence).
    """
    # Bare follow-ups inherit the previous turn's intent (e.g. "for supplier
    # termination?" after an approval question is still an approval question).
    if _is_bare_followup(question) and prior_intent is not None:
        return prior_intent, 0.65

    prompt = f"""Classify. Output: intent,confidence

approval: approve,authorize,sign-off,approval limit,approval chain,who can,who approves,needs approval,requires approval,approval required,approval authority,who authorizes,who signs off,approval matrix,can approve,approval needed,escalate to,seek approval,get approval
  "Who can approve a purchase over $10,000?" → approval,0.95
  "Does the CFO need to sign off on budgets?" → approval,0.92
  "What is my approval limit as a manager?" → approval,0.90
  "Who authorizes travel requests?" → approval,0.93
  "This expense needs director approval" → approval,0.91
  "What is the approval chain for hiring?" → approval,0.89
  "Can my supervisor approve overtime?" → approval,0.88

conflict: conflict,contradict,inconsistency,inconsistent,versus,vs,override,supersedes,differs from,contrary to,version change,updated version,old version,new version,saying different things,clash,disagreement,mismatch
  "These two policies contradict each other" → conflict,0.95
  "Is there a conflict between v1 and v2?" → conflict,0.92
  "The handbook says X but policy says Y" → conflict,0.90
  "Which version overrides the other?" → conflict,0.91
  "This clause differs from the old policy" → conflict,0.88
  "Are there inconsistencies in these rules?" → conflict,0.89

compliance: compliance,compliant,non-compliant,violation,breach,regulation,regulatory,gdpr,ndpa,iso 27001,nist,hipaa,sox,legal,law,standard,requirement,mandatory,penalty,fine,sanction,audit,obligation,must we,do we need,are we compliant,compliant with,mandatory requirement
  "Are we GDPR compliant for data storage?" → compliance,0.95
  "What happens if we violate security policy?" → compliance,0.92
  "Is ISO 27001 mandatory for our team?" → compliance,0.93
  "What are the penalties for non-compliance?" → compliance,0.91
  "Do we need to follow this regulation?" → compliance,0.89
  "Is this a legal requirement?" → compliance,0.90
  "What are our audit obligations?" → compliance,0.88

procedure: how to,how do i,how can i,what is the process,procedure,steps,step by step,workflow,what should i,what should we,how often,when should,what happens if,what do i do,process for,steps to,walk me through,guide me,instructions
  "How do I reset my password?" → procedure,0.95
  "What are the steps for onboarding?" → procedure,0.93
  "What should I do if I lose my badge?" → procedure,0.90
  "Walk me through the incident response process" → procedure,0.92
  "How often should passwords be changed?" → procedure,0.89
  "What happens after I submit the form?" → procedure,0.87
  "Guide me through the travel request process" → procedure,0.91

general: explain,tell me about,what is,what does,does the policy cover,can you explain,describe,summarize,i want to know,information about,overview,clarify
  "Tell me about the remote work policy" → general,0.65
  "What does the policy say about overtime?" → general,0.70
  "Can you explain the dress code policy?" → general,0.68
  "Give me an overview of security policies" → general,0.66
  "I want to know about company benefits" → general,0.64

Question: {question}"""

    result = llm_generate(
        model=QWEN3_8B_MODEL,
        system_prompt="Classify questions into: approval, conflict, compliance, procedure, general",
        user_message=prompt,
        temperature=0.0,
        max_tokens=15,
        timeout=8.0,
        enable_thinking=False
    )

    if result.success:
        raw = result.data.strip().lower()
        intent_map = {
            "approval": QueryIntent.APPROVAL,
            "conflict": QueryIntent.CONFLICT,
            "compliance": QueryIntent.COMPLIANCE,
            "procedure": QueryIntent.PROCEDURE,
            "general": QueryIntent.GENERAL,
        }

        # Format 1: "procedure,0.95"
        match = re.search(
            r"(approval|conflict|compliance|procedure|general)[,:\-]\s*([\d.]+)",
            raw
        )
        if match:
            raw_intent = match.group(1)
            try:
                confidence = min(max(float(match.group(2)), 0.0), 1.0)
            except ValueError:
                confidence = None
        else:
            # Format 2: "intent: X" (or bare intent) with optional "confidence: high|medium|low"
            intent_match = re.search(
                r"(approval|conflict|compliance|procedure|general)", raw
            )
            conf_match = re.search(
                r"confidence[\s:]*?(high|medium|low)", raw
            )
            raw_intent = intent_match.group(1) if intent_match else None
            confidence = {
                "high": 0.9,
                "medium": 0.7,
                "low": 0.4,
            }.get(conf_match.group(1), 0.7) if conf_match else 0.7

        if confidence is None:
            confidence = 0.7

        if raw_intent:
            if confidence >= 0.7:
                return intent_map[raw_intent], round(confidence, 4)
            # Low-confidence LLM result → cross-validate with keyword heuristic
            kw_intent, kw_conf = _keyword_classify(question)
            if kw_intent != QueryIntent.GENERAL:
                return kw_intent, round(max(confidence, kw_conf), 4)
            return intent_map[raw_intent], round(confidence, 4)

    # Fallback: keyword matching when LLM unavailable or unparseable
    return _keyword_classify(question)


# ═══════════════════════════════════════
#  Agent Functions
# ═══════════════════════════════════════

def agent_retrieve(ctx: QueryContext):
    """Retrieve relevant chunks via hybrid search."""
    from services.api.search import search_policy
    from services.api.state import get_bm25_index

    # Use the user's raw question for retrieval so history augmentation can't
    # drown out the query's signal (e.g. a password question after unrelated
    # supplier/clear-desk turns retrieves password chunks, not supplier ones).
    # Bare follow-ups ("for supplier termination?") keep the history-augmented
    # question so retrieval still inherits the prior turn's context.
    query = ctx.raw_question or ctx.question
    if _is_bare_followup(query):
        query = ctx.question

    # Increase retrieval depth for procedures to capture all requirements
    top_k = 100 if ctx.intent == QueryIntent.PROCEDURE else 20

    try:
        ctx.chunks = search_policy(
            query,
            access_level=ctx.access_level,
            search_mode=ctx.search_mode,
            bm25_index=get_bm25_index(),
            top_k_retrieval=top_k,
        )
        ctx.state = QueryState.RETRIEVED
    except Exception as e:
        ctx.fail(f"Retrieval failed: {e}")


def agent_reason(ctx: QueryContext):
    """Generate grounded answer from retrieved chunks."""
    from services.agents.reasoner import generate_grounded_answer

    try:
        # For approval intents the knowledge-graph answer is authoritative;
        # keep it instead of regenerating a generic LLM answer from chunks.
        if ctx.intent == QueryIntent.APPROVAL and ctx.answer.strip():
            ctx.state = QueryState.REASONED
            return

        # For conflict/compliance intents the specialized agent verdict is
        # the primary answer once its structured result is available.
        if ctx.intent == QueryIntent.CONFLICT:
            if ctx.conflicts:
                conflict_answer = ctx.build_conflict_answer()
                if conflict_answer:
                    ctx.answer = conflict_answer
                    ctx.state = QueryState.REASONED
                    return
            if ctx.conflict_analysis.get("inconclusive"):
                ctx.answer = (
                    "Conflict analysis was inconclusive because some candidate clauses "
                    "could not be evaluated. No definitive absence of conflict can be reported."
                )
                ctx.state = QueryState.REASONED
                return
            else:
                ctx.answer = (
                    "I searched for conflicts across the relevant policy clauses "
                    "and did not find any contradictory requirements. The clauses "
                    "appear to be consistent with each other."
                )
                ctx.state = QueryState.REASONED
                return
        if ctx.intent == QueryIntent.COMPLIANCE and ctx.risk_result:
            risk_answer = ctx.build_risk_answer()
            if risk_answer:
                ctx.answer = risk_answer
                ctx.state = QueryState.REASONED
                return

        # Handle procedural results: enrich the question to force detailed listing
        final_query = ctx.question
        if ctx.intent == QueryIntent.PROCEDURE and ctx.procedure_results:
            proc_data = ctx.procedure_results.get("processes", [])
            if proc_data:
                process_list = "\n".join([f"- {p['name']}" for p in proc_data])
                final_query += f"\n\nSTRUCTURED DATA FOUND:\n{process_list}\n"
                final_query += "Please provide a detailed, long explanation of all these processes, including every mandatory requirement and every sequential step, with citations."

        if not ctx.chunks:
            ctx.fail("No relevant policy clauses found")
            return

        from services.api.search import get_global_feedback_guidance
        feedback_guidance = get_global_feedback_guidance(ctx.intent.value)
        ctx.answer = generate_grounded_answer(
            final_query, ctx.chunks, feedback_guidance=feedback_guidance
        )
        ctx.state = QueryState.REASONED
    except Exception as e:
        ctx.fail(f"Reasoning failed: {e}")



def agent_verify(ctx: QueryContext):
    """Verify citations against retrieved chunks."""
    from services.agents.verifier import verify_citations
    from packages.shared.confidence import compute_confidence

    try:
        is_valid, msg = verify_citations(ctx.answer, ctx.chunks)
        if ctx.conflicts and not re.search(r"\[Doc: .*?, Clause: .*?\]", ctx.answer):
            is_valid = False
            msg = "Conflict answer does not cite both clause sources."

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
        if not is_valid or "Insufficient" in ctx.answer or ctx.conflict_analysis.get("inconclusive"):
            ctx.verdict = "abstained"
        elif ctx.risk_result and ctx.risk_result.get("verdict") in ("conditional", "violation"):
            # Surface compliance risk surfaced by the risk agent
            ctx.verdict = ctx.risk_result["verdict"]
        elif ctx.conflicts:
            ctx.verdict = "conflict"
        else:
            ctx.verdict = "clear"

        ctx.confidence = compute_confidence(ctx.chunks, is_valid, ctx.answer)

        # A verdict-driven confidence floor/ceiling: when we abstain (no policy
        # basis / no valid citations) the percentage must not masquerade as
        # high certainty — the original multi-signal score could reach ~89% on
        # an "Insufficient policy basis" abstention, which is misleading.
        if ctx.verdict == "abstained":
            ctx.confidence = min(ctx.confidence, 30.0)

        ctx.state = QueryState.VERIFIED
    except Exception as e:
        ctx.fail(f"Verification failed: {e}")


def agent_approval(ctx: QueryContext):
    """Query the knowledge graph for approval authority."""
    from services.agents.approval_agent import resolve_approval

    try:
        ctx.approval_result = resolve_approval(
            ctx.raw_question or ctx.question, ctx.chunks
        )
        if ctx.approval_result:
            # Surface the approval decision as the primary answer. The generic
            # LLM reasoner runs afterwards; if it produces a cleaner grounded
            # answer we keep that, otherwise the approval prose wins.
            approval_answer = ctx.build_approval_answer()
            if approval_answer and not ctx.answer:
                ctx.answer = approval_answer
            ctx.state = QueryState.REASONED
    except Exception as e:
        ctx.approval_result = {"error": str(e)}


def agent_conflict_check(ctx: QueryContext):
    """Check for conflicts between clauses (Type 1: clause-vs-corpus expansion)."""
    from services.agents.conflict_agent import analyze_clause_vs_corpus

    try:
        ctx.conflict_analysis = analyze_clause_vs_corpus(
            ctx.chunks,
            access_level=ctx.access_level,
            candidate_retrieval_limit=20,
            minimum_conflict_targets=3,
            maximum_llm_comparisons=15,
            threshold=0.5,
        )
        # Citation verification and API persistence must see every clause that
        # contributed evidence, not only the initial query retrieval.
        ctx.chunks = ctx.conflict_analysis.get("evidence", ctx.chunks)
        ctx.conflicts = ctx.conflict_analysis.get("conflicts", [])
        ctx.state = QueryState.REASONED
    except Exception as e:
        ctx.conflicts = []
        ctx.conflict_analysis = {
            "status": "inconclusive",
            "total_candidates": 0,
            "checked_candidates": 0,
            "unchecked_candidates": 0,
            "llm_calls": 0,
            "truncated": True,
            "inconclusive": True,
            "error": str(e),
        }
        ctx.error = f"Conflict analysis failed: {e}"
        ctx.state = QueryState.REASONED


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
    QueryIntent.PROCEDURE: [agent_retrieve, agent_procedure_reason, agent_verify],
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
    raw_question: str = "",
    prior_intent: Optional[QueryIntent] = None,
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
        raw_question=raw_question,
        search_mode=search_mode,
    )
    ctx.start_timer()

    # Step 1: Classify intent (on the raw question, not the history-augmented one)
    ctx.intent, ctx.intent_confidence = classify_intent(
        ctx.raw_question or ctx.question,
        prior_intent=prior_intent,
    )
    ctx.state = QueryState.CLASSIFIED

    # Step 2: Select pipeline
    pipeline = INTENT_PIPELINES.get(ctx.intent, DEFAULT_PIPELINE)

    # Step 3: Execute agents in sequence
    for agent_fn in pipeline:
        if ctx.state == QueryState.ABSTAINED:
            break
        agent_start = time.time()
        agent_fn(ctx)
        ctx.chain.append({
            "agent": agent_fn.__name__,
            "state": ctx.state.value,
            "latency_ms": int((time.time() - agent_start) * 1000),
            "output": ctx.verdict if agent_fn.__name__ == "agent_verify" else None,
        })

    # Step 4: Final state
    if ctx.state != QueryState.ABSTAINED:
        ctx.state = QueryState.DONE

    ctx.stop_timer()
    return ctx
