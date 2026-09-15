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
from services.agents.tools.llm_tools import llm_generate, AGENT_MODEL

logger = logging.getLogger(__name__)


class QueryState(enum.Enum):
    IDLE = "idle"
    CLASSIFIED = "classified"
    RETRIEVED = "retrieved"
    REASONED = "reasoned"
    VERIFIED = "verified"
    DONE = "done"
    ABSTAINED = "abstained"
    AWAITING_SELECTION = "awaiting_selection"


class QueryIntent(enum.Enum):
    GENERAL = "general"
    APPROVAL = "approval"
    CONFLICT = "conflict"
    COMPLIANCE = "compliance"
    PROCEDURE = "procedure"
    CONVERSATIONAL = "conversational"


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

    # Document name resolution (filled by agent_resolve_docs)
    resolved_documents: List[Dict[str, Any]] = field(default_factory=list)
    resolved_doc_ids: List[str] = field(default_factory=list)
    doc_slots: List[Dict[str, Any]] = field(default_factory=list)
    doc_phrases: List[str] = field(default_factory=list)

    # Conflict subtype (filled after doc resolution)
    conflict_type: Optional[str] = None  # "type_1" | "type_2" | "type_2b"

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
        if self.conflict_type:
            reasoning["conflict_type"] = self.conflict_type
        if self.resolved_documents:
            reasoning["resolved_documents"] = self.resolved_documents
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

    def build_conflict_answer(self) -> str:
        """Conversational conflict answer with inline citations and recommendation."""
        if not self.conflicts:
            return ""

        count = len(self.conflicts)
        if count == 1:
            lines = ["I found a conflict between your policies:\n"]
        else:
            lines = [f"I found {count} conflicts between your policies:\n"]

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

            topic = self._extract_topic(text_a, text_b, reason)
            lines.append(f"**{i}. {topic}**")

            if text_a and len(text_a) > 10:
                snippet_a = text_a[:150].rstrip(".")
                citation_a = f" [Doc: {title_a}, Clause: {ref_a}]" if title_a and ref_a else ""
                lines.append(f'The {title_a} states: "{snippet_a}."{citation_a}')

            if text_b and len(text_b) > 10:
                snippet_b = text_b[:150].rstrip(".")
                citation_b = f" [Doc: {title_b}, Clause: {ref_b}]" if title_b and ref_b else ""
                lines.append(f'However, the {title_b} says: "{snippet_b}."{citation_b}')

            if reason:
                lines.append(f"These requirements contradict each other because {reason.lower().rstrip('.')}.")

            status = c.get("status", "confirmed_conflict")
            if status == "superseded":
                lines.append("One version appears to supersede the other based on the available metadata.")
            elif status == "possible_conflict":
                lines.append("These may conflict, but I'd need more context to confirm.")

            lines.append("")

        # Conversational recommendation
        doc_titles = set()
        for c in self.conflicts:
            ca = c.get("clause_a", {})
            cb = c.get("clause_b", {})
            if isinstance(ca, dict) and ca.get("document_title"):
                doc_titles.add(ca["document_title"])
            if isinstance(cb, dict) and cb.get("document_title"):
                doc_titles.add(cb["document_title"])

        if count == 1:
            lines.append("I'd suggest reviewing this with the policy owners to align on one standard.")
        else:
            lines.append(
                f"With {count} conflicts across your policies, it might be worth "
                "scheduling an alignment review to resolve these inconsistencies."
            )

        if doc_titles:
            lines.append(f"Documents involved: {', '.join(sorted(doc_titles))}.")

        coverage_note = self.conflict_analysis.get("coverage_note")
        if coverage_note:
            lines.append(coverage_note)
        elif self.conflict_analysis.get("inconclusive"):
            lines.append("Note: some candidate clauses couldn't be fully evaluated due to a temporary service issue.")

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
        """Conversational compliance answer from the risk assessment."""
        if not self.risk_result:
            return ""
        res = self.risk_result
        verdict = res.get("verdict", "unknown")
        regulations = res.get("regulations", [])
        obligations = res.get("obligations", {})
        recommendations = res.get("recommendations", [])
        elaboration = res.get("elaboration", "")

        lines = []

        if elaboration and elaboration != "No compliance issues detected.":
            lines.append(elaboration)
        else:
            if verdict == "clear":
                lines.append("No compliance issues detected in the retrieved clauses.")
            elif verdict == "conditional":
                lines.append("Compliance is conditional — certain conditions must be satisfied.")
            elif verdict == "violation":
                lines.append("A compliance issue was detected.")
            elif verdict == "abstained":
                lines.append("Insufficient policy context to assess compliance.")
                return " ".join(lines)

        if regulations:
            lines.append(f"Applicable frameworks: {', '.join(regulations)}.")

        mandatory = sum(1 for v in obligations.values() if v == "mandatory")
        if mandatory:
            lines.append(f"{mandatory} mandatory obligation(s) apply.")

        actionable = [
            r for r in recommendations
            if not r.startswith("CRITICAL:")
            and not r.startswith("NOTICE:")
            and r != "No immediate compliance concerns detected"
        ]
        if actionable:
            lines.append(" ".join(actionable))

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

    conversational_kw = ("hello", "hi ", "hi!", "hey", "good morning", "good afternoon",
                         "good evening", "thank you", "thanks", "bye", "goodbye",
                         "how are you", "what's up", "see you")
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

    for kw in conversational_kw:
        if q.startswith(kw.rstrip()) or q == kw.rstrip() or kw in q:
            return QueryIntent.CONVERSATIONAL, 0.9
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
) -> tuple[QueryIntent, float, Optional[str], List[str]]:
    """
    CoT classification: intent + conflict_type + document phrases.
    Single LLM call with chain-of-thought. Returns
    (intent, confidence, conflict_type, doc_phrases).

    For CONFLICT intent, determines subtype:
      0 docs → type_1, 1 doc → type_2b, 2+ docs → type_2
    """
    import json as _json

    if _is_bare_followup(question) and prior_intent is not None:
        return prior_intent, 0.65, None, []

    VALID_INTENTS = {"approval", "conflict", "compliance", "procedure", "general", "conversational"}

    prompt = f"""Classify the user's question into one of 6 intents.

CRITICAL: The "intent" field MUST be exactly ONE of these 6 words (lowercase):
  approval, conflict, compliance, procedure, general, conversational

Conflict subtypes (only when intent is "conflict"):
  type_1  — 0 document names → generic corpus search
  type_2b — 1 document name  → that doc vs entire corpus
  type_2  — 2+ document names → compare those specific docs

---

# EXAMPLES (with reasoning)

Q: "Check any conflict between backup policy and change management"
→ Thinking: User names 2 policies and wants to check for conflicts.
→ {{"intent":"conflict","confidence":0.95,"conflict_type":"type_2","doc_phrases":["backup policy","change management"]}}

Q: "Compare the Backup Policy and the Data Retention Policy"
→ Thinking: User names 2 policies and wants a comparison.
→ {{"intent":"conflict","confidence":0.92,"conflict_type":"type_2","doc_phrases":["Backup Policy","Data Retention Policy"]}}

Q: "Compare the BYOD Policy and the Remote Access Policy for gaps"
→ Thinking: User names 2 policies and asks about gaps/differences.
→ {{"intent":"conflict","confidence":0.91,"conflict_type":"type_2","doc_phrases":["BYOD Policy","Remote Access Policy"]}}

Q: "How do the Clear Desk Policy and the Physical Security Policy differ?"
→ Thinking: User names 2 policies and asks how they differ.
→ {{"intent":"conflict","confidence":0.90,"conflict_type":"type_2","doc_phrases":["Clear Desk Policy","Physical Security Policy"]}}

Q: "Compare BYOD and Remote Access for conflicts"
→ Thinking: User names 2 policies and explicitly asks to compare them for conflicts. This is a conflict comparison.
→ {{"intent":"conflict","confidence":0.95,"conflict_type":"type_2","doc_phrases":["BYOD Policy","Remote Access Policy"]}}

Q: "Compare the Change Management Policy and the Incident Response Plan for conflicts"
→ Thinking: User names 2 policies and asks to compare them for conflicts.
→ {{"intent":"conflict","confidence":0.94,"conflict_type":"type_2","doc_phrases":["Change Management Policy","Incident Response Plan"]}}

Q: "What are the differences between the Backup Policy and the Data Retention Policy?"
→ Thinking: User asks about differences between 2 named policies. This is a conflict/difference query.
→ {{"intent":"conflict","confidence":0.91,"conflict_type":"type_2","doc_phrases":["Backup Policy","Data Retention Policy"]}}

Q: "Are there conflicts between the Clear Desk Policy and Physical Security Policy?"
→ Thinking: User explicitly asks about conflicts between 2 named policies.
→ {{"intent":"conflict","confidence":0.93,"conflict_type":"type_2","doc_phrases":["Clear Desk Policy","Physical Security Policy"]}}

Q: "Does the Incident Response Plan conflict with anything?"
→ Thinking: User names 1 policy and asks about conflicts with other policies.
→ {{"intent":"conflict","confidence":0.93,"conflict_type":"type_2b","doc_phrases":["Incident Response Plan"]}}

Q: "Are there password conflicts?"
→ Thinking: User asks about a topic conflict but names no specific documents.
→ {{"intent":"conflict","confidence":0.90,"conflict_type":"type_1","doc_phrases":[]}}

Q: "Our policy requires passwords to be changed every 90 days. Does this conflict with any other policy?"
→ Thinking: User states a specific requirement and asks if other policies contradict it. No specific document named — this is a type_1 topic-level conflict search.
→ {{"intent":"conflict","confidence":0.93,"conflict_type":"type_1","doc_phrases":[]}}

Q: "We require VPN for all remote employees. Is this consistent across policies?"
→ Thinking: User describes a requirement and asks about consistency across all policies. This is a conflict/consistency check across the corpus.
→ {{"intent":"conflict","confidence":0.91,"conflict_type":"type_1","doc_phrases":[]}}

Q: "Do any of our policies contradict the requirement for annual security training?"
→ Thinking: User asks whether any policy contradicts a specific requirement. No documents named — type_1 corpus search.
→ {{"intent":"conflict","confidence":0.92,"conflict_type":"type_1","doc_phrases":[]}}

Q: "Is the 90-day password rotation requirement consistent across all policies?"
→ Thinking: User asks whether a specific requirement is consistent across all policies. This is a conflict/consistency check.
→ {{"intent":"conflict","confidence":0.90,"conflict_type":"type_1","doc_phrases":[]}}

Q: "Who can approve a purchase over $10,000?"
→ Thinking: User asks about approval authority and monetary thresholds.
→ {{"intent":"approval","confidence":0.95,"conflict_type":null,"doc_phrases":[]}}

Q: "Are we compliant with data protection regulations?"
→ Thinking: User asks whether a regulation or obligation exists.
→ {{"intent":"compliance","confidence":0.92,"conflict_type":null,"doc_phrases":[]}}

Q: "Is encryption of confidential data mandatory?"
→ Thinking: User asks about a mandatory requirement under a regulation.
→ {{"intent":"compliance","confidence":0.92,"conflict_type":null,"doc_phrases":[]}}

Q: "Are backup integrity tests required?"
→ Thinking: User asks whether a specific test is mandated.
→ {{"intent":"compliance","confidence":0.90,"conflict_type":null,"doc_phrases":[]}}

Q: "What is the tech sector tax classification?"
→ Thinking: User asks about a regulatory classification.
→ {{"intent":"compliance","confidence":0.88,"conflict_type":null,"doc_phrases":[]}}

Q: "Summarize the BYOD Policy"
→ Thinking: User wants a summary or overview of a specific policy. This is an informational query about one document.
→ {{"intent":"procedure","confidence":0.92,"conflict_type":null,"doc_phrases":["BYOD Policy"]}}

Q: "Give me an overview of the backup policy"
→ Thinking: User wants an overview/summary of a policy.
→ {{"intent":"procedure","confidence":0.90,"conflict_type":null,"doc_phrases":["Backup Policy"]}}

Q: "What are the steps for the incident response process?"
→ Thinking: User asks for step-by-step process from a specific plan.
→ {{"intent":"procedure","confidence":0.93,"conflict_type":null,"doc_phrases":["Incident Response Plan"]}}

Q: "How do I install new software on my work computer?"
→ Thinking: User asks for a how-to procedure.
→ {{"intent":"procedure","confidence":0.91,"conflict_type":null,"doc_phrases":[]}}

Q: "Explain the change management process"
→ Thinking: User wants an explanation of a process/procedure.
→ {{"intent":"procedure","confidence":0.88,"conflict_type":null,"doc_phrases":["Change Management Policy"]}}

Q: "What does the Backup Policy require?"
→ Thinking: User asks a general question about a policy's content.
→ {{"intent":"general","confidence":0.72,"conflict_type":null,"doc_phrases":["Backup Policy"]}}

Q: "Hello"
→ Thinking: User is greeting, no substantive question.
→ {{"intent":"conversational","confidence":0.99,"conflict_type":null,"doc_phrases":[]}}

---

Q: "{question}"
→ Thinking:"""

    result = llm_generate(
        model=AGENT_MODEL,
        system_prompt='Think step by step about the user intent. Output a "→ Thinking:" line with your reasoning, then output ONLY the JSON object. The "intent" field MUST be exactly one of: approval, conflict, compliance, procedure, general, conversational.',
        user_message=prompt,
        temperature=0.0,
        max_tokens=2048,
        timeout=12.0,
        enable_thinking=False
    )

    intent_map = {
        "approval": QueryIntent.APPROVAL,
        "conflict": QueryIntent.CONFLICT,
        "compliance": QueryIntent.COMPLIANCE,
        "procedure": QueryIntent.PROCEDURE,
        "general": QueryIntent.GENERAL,
        "conversational": QueryIntent.CONVERSATIONAL,
    }

    if result.success:
        raw = result.data.strip()
        raw = re.sub(r"<thinking>.*?</thinking>", "", raw, flags=re.DOTALL).strip()
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            raw = json_match.group()
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, list) and parsed:
                parsed = parsed[0]
            raw_intent = parsed.get("intent", "").lower().strip()
            confidence = float(parsed.get("confidence", 0.5))
            conflict_type = parsed.get("conflict_type")
            doc_phrases = [str(p).strip() for p in parsed.get("doc_phrases", []) if p]

            # Validate: intent must be a known label, not hallucinated text
            if raw_intent not in intent_map:
                # Try to rescue: scan the raw output for a valid intent keyword
                for kw in intent_map:
                    if kw in raw.lower():
                        raw_intent = kw
                        break

            if raw_intent in intent_map:
                return (
                    intent_map[raw_intent],
                    round(min(max(confidence, 0.0), 1.0), 4),
                    conflict_type,
                    doc_phrases,
                )
        except (_json.JSONDecodeError, ValueError, KeyError):
            pass

    return QueryIntent.GENERAL, 0.1, None, []


# ═══════════════════════════════════════
#  Agent Functions
# ═══════════════════════════════════════

def agent_retrieve(ctx: QueryContext):
    """Retrieve relevant chunks via hybrid search."""
    from services.api.search import search_policy
    from services.api.state import get_bm25_index

    # Skip if agent_resolve_docs already populated chunks from resolved documents
    if ctx.chunks and ctx.state == QueryState.RETRIEVED:
        return

    # Use the user's raw question for retrieval so history augmentation can't
    # drown out the query's signal (e.g. a password question after unrelated
    # supplier/clear-desk turns retrieves password chunks, not supplier ones).
    # Bare follow-ups ("for supplier termination?") keep the history-augmented
    # question so retrieval still inherits the prior turn's context.
    query = ctx.raw_question or ctx.question
    if _is_bare_followup(query):
        query = ctx.question

    # Increase retrieval depth for procedures to capture all requirements
    top_k = 100 if ctx.intent == QueryIntent.PROCEDURE else (50 if ctx.intent == QueryIntent.CONFLICT else 20)

    try:
        ctx.chunks = search_policy(
            query,
            access_level=ctx.access_level,
            search_mode=ctx.search_mode,
            bm25_index=get_bm25_index(),
            top_k_retrieval=top_k,
        )

        # Enrich with resolved document chunks (for GENERAL/PROCEDURE
        # when doc_phrases are available but intent was misclassified)
        if ctx.resolved_doc_ids and ctx.intent in (
            QueryIntent.GENERAL, QueryIntent.PROCEDURE
        ):
            from services.api.search import fetch_chunks_by_document
            doc_chunks_map = fetch_chunks_by_document(
                ctx.resolved_doc_ids, ctx.access_level
            )
            if doc_chunks_map:
                existing_ids = {c.get("id") for c in ctx.chunks}
                for doc_id, chunks in doc_chunks_map.items():
                    for chunk in chunks:
                        if chunk.get("id") not in existing_ids:
                            ctx.chunks.append(chunk)
                            existing_ids.add(chunk.get("id"))

        ctx.state = QueryState.RETRIEVED
    except Exception as e:
        ctx.fail(f"Retrieval failed: {e}")


def _format_approval_for_llm(result: dict) -> str:
    """Format approval result for LLM context."""
    if result.get("error"):
        return f"Error: {result['error']}"
    parts = []
    if result.get("process"):
        parts.append(f"Process: {result['process']}")
    if result.get("matching_roles"):
        parts.append(f"Approval authority: {', '.join(result['matching_roles'])}")
    if result.get("amount"):
        parts.append(f"Requested amount: ${result['amount']:,.0f}")
    if result.get("user_can_approve"):
        parts.append("User has authority to approve: Yes")
    else:
        parts.append("User has authority to approve: No")
    if result.get("matching_processes"):
        parts.append(f"Related processes: {', '.join(result['matching_processes'][:3])}")
    if not parts:
        parts.append("No matching approval process found in the knowledge graph.")
    return "\n".join(parts)


def agent_reason(ctx: QueryContext):
    """Generate grounded answer from retrieved chunks."""
    from services.agents.reasoner import generate_grounded_answer

    try:
        final_query = ctx.question

        # For approval intents, inject the knowledge graph result into the
        # question for the reasoner to generate a natural answer.
        if ctx.intent == QueryIntent.APPROVAL and ctx.approval_result:
            approval_text = _format_approval_for_llm(ctx.approval_result)

            # If approval agent found no match or errored, handle gracefully
            # instead of sending minimal context to the reasoner.
            if not ctx.approval_result.get("approvable") or ctx.approval_result.get("error"):
                if ctx.chunks:
                    final_query = (
                        f"{ctx.raw_question or ctx.question}\n\n"
                        f"KNOWLEDGE GRAPH RESULT:\n{approval_text}\n\n"
                        f"The knowledge graph does not define an approval chain for "
                        f"this action. Based on any relevant policy context above, "
                        f"answer the user's question. If no relevant policy "
                        f"information is found, say: 'This approval process is not "
                        f"defined in the current policy corpus. Please consult your "
                        f"manager or the policy owner for guidance on approval "
                        f"authority for this action.'"
                    )
                else:
                    ctx.answer = (
                        "The knowledge graph does not define an approval chain for "
                        "this action. The policy corpus does not contain approval "
                        "authority information related to your question.\n\n"
                        "**Recommendation:** Please consult your manager or the "
                        "policy owner for guidance on approval authority for this action."
                    )
                    ctx.state = QueryState.REASONED
                    return
            else:
                final_query = (
                    f"{ctx.raw_question or ctx.question}\n\n"
                    f"KNOWLEDGE GRAPH RESULT:\n{approval_text}\n\n"
                    f"Based on the knowledge graph result and any relevant policy "
                    f"context above, answer the user's question in a conversational "
                    f"tone. If the knowledge graph found a matching process, describe "
                    f"the approval chain. If no matching process was found, suggest "
                    f"checking with a manager or policy owner."
                )

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
                    "Sorry, the conflict analysis service hit a temporary hiccup "
                    "and couldn't complete this comparison. Give it another try in a moment."
                )
                ctx.state = QueryState.REASONED
                return
            else:
                coverage = ctx.conflict_analysis.get("coverage_note", "")
                ctx.answer = (
                    "I compared the relevant policy clauses and everything looks "
                    "consistent — no conflicting requirements found. The policies "
                    "align well on this topic."
                    + (f" {coverage}" if coverage else "")
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

        # Safety net: if the reasoner produced "Insufficient policy basis" for
        # a comparison question, the intent was likely misclassified. Append a
        # hint so the user can retry with the correct intent.
        q_lower = (ctx.raw_question or ctx.question).lower()
        if ("Insufficient" in (ctx.answer or "")
                and any(w in q_lower for w in ("compare", "versus", "conflict", "differ"))):
            ctx.answer += (
                "\n\nNote: This question may be better answered as a conflict "
                "analysis. Try rephrasing with 'Compare X and Y for conflicts'."
            )

        # Safety net: if approval agent found no match and the reasoner
        # echoed "Insufficient", replace with a direct helpful answer.
        if (ctx.intent == QueryIntent.APPROVAL
                and ctx.approval_result
                and not ctx.approval_result.get("approvable")
                and "Insufficient" in (ctx.answer or "")):
            ctx.answer = (
                "The knowledge graph does not define an approval chain for "
                "this action. The policy corpus does not contain approval "
                "authority information related to your question.\n\n"
                "**Recommendation:** Please consult your manager or the "
                "policy owner for guidance on approval authority for this action."
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

        # Graph/elaboration-based answers (approval, compliance) are
        # authoritative regardless of chunk citations — don't penalize.
        if ctx.intent in (QueryIntent.APPROVAL, QueryIntent.COMPLIANCE):
            if ctx.verdict == "clear":
                ctx.confidence = max(ctx.confidence, 75.0)
            elif ctx.verdict == "conditional":
                ctx.confidence = max(ctx.confidence, 65.0)
            elif ctx.verdict == "violation":
                ctx.confidence = max(ctx.confidence, 80.0)

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
        ctx.state = QueryState.REASONED
    except Exception as e:
        ctx.approval_result = {"error": str(e)}


def agent_conflict_check(ctx: QueryContext):
    """Unified conflict dispatcher — routes by subtype."""
    if ctx.conflict_type == "type_2":
        agent_conflict_type2(ctx)
    elif ctx.conflict_type == "type_2b":
        agent_conflict_type2b(ctx)
    else:
        agent_retrieve(ctx)
        _run_clause_vs_corpus(ctx)


def _run_clause_vs_corpus(ctx: QueryContext):
    """Type 1: clause-vs-corpus expansion."""
    from services.agents.conflict_agent import analyze_clause_vs_corpus

    try:
        result = analyze_clause_vs_corpus(
            ctx.chunks,
            access_level=ctx.access_level,
            query_text=ctx.raw_question or ctx.question,
            candidate_retrieval_limit=100,
        )
        ctx.conflicts = result.get("conflicts", [])
        ctx.chunks = result.get("clauses_sent", ctx.chunks)
        ctx.conflict_analysis = {
            "status": "complete" if not result.get("truncated") else "inconclusive",
            "type": "type_1",
            "total_candidates": result.get("total_candidates", 0),
            "checked_candidates": result.get("total_conflicts", 0),
            "llm_calls": result.get("total_llm_calls", 1),
            "truncated": result.get("truncated", False),
            "failed_calls": result.get("failed_calls", 0),
        }
        ctx.state = QueryState.REASONED
    except Exception as e:
        ctx.conflicts = []
        ctx.conflict_analysis = {
            "status": "inconclusive", "type": "type_1",
            "total_candidates": 0, "checked_candidates": 0,
            "total_llm_calls": 0, "failed_calls": 0,
            "truncated": True,
        }
        ctx.error = f"Conflict analysis failed: {e}"
        ctx.state = QueryState.REASONED


def agent_conflict_type2(ctx: QueryContext):
    """Type 2: direct doc-vs-doc conflict comparison."""
    from services.agents.conflict_agent import compare_document_chunks
    from services.api.search import fetch_chunks_by_document

    try:
        chunks_by_doc = fetch_chunks_by_document(ctx.resolved_doc_ids, ctx.access_level)
        if not chunks_by_doc:
            ctx.fail("Could not fetch chunks for the resolved documents")
            return

        result = compare_document_chunks(
            chunks_by_doc,
            access_level=ctx.access_level,
            similarity_threshold=0.57,
            max_pairs=200,
            max_llm_calls=200,
            question=ctx.raw_question or ctx.question,
        )

        all_conflicts = []
        for pair in result.get("doc_pairs", []):
            all_conflicts.extend(pair.get("conflicts", []))

        ctx.conflicts = all_conflicts
        ctx.conflict_analysis = {
            "status": "complete", "type": "type_2",
            "total_candidates": result.get("total_candidates", 0),
            "checked_candidates": result.get("total_llm_calls", 0),
            "unchecked_candidates": 0,
            "llm_calls": result.get("total_llm_calls", 0),
            "truncated": result.get("truncated", False),
            "inconclusive": result.get("failed_calls", 0) > 0 and result.get("total_llm_calls", 0) <= result.get("failed_calls", 0),
        }
        seen_ids: set[str] = set()
        citation_chunks: list[Dict[str, Any]] = []
        for conflict in all_conflicts:
            for side in ("clause_a", "clause_b"):
                clause = conflict.get(side, {})
                cid = clause.get("id", "")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    citation_chunks.append({
                        "id": cid,
                        "document_id": clause.get("document_id", ""),
                        "title": clause.get("document_title", ""),
                        "clause_ref": clause.get("clause_ref", ""),
                        "text": clause.get("text", ""),
                        "score": conflict.get("similarity", 0.0),
                    })
        ctx.chunks = citation_chunks
        ctx.state = QueryState.REASONED
    except Exception as e:
        ctx.conflicts = []
        ctx.conflict_analysis = {
            "status": "inconclusive", "type": "type_2",
            "total_candidates": 0, "checked_candidates": 0,
            "unchecked_candidates": 0, "llm_calls": 0,
            "truncated": True, "inconclusive": True, "error": str(e),
        }
        ctx.state = QueryState.REASONED


def agent_conflict_type2b(ctx: QueryContext):
    """Type 2b: single doc-vs-corpus conflict check."""
    from services.agents.conflict_agent import detect_conflicting_documents
    from services.api.search import fetch_chunks_by_document

    try:
        doc_id = ctx.resolved_doc_ids[0]
        chunks_by_doc = fetch_chunks_by_document([doc_id], ctx.access_level)
        doc_chunks = chunks_by_doc.get(doc_id, [])
        if not doc_chunks:
            ctx.fail("Could not fetch chunks for the resolved document")
            return

        doc_title = doc_chunks[0].get("title", "") if doc_chunks else ""

        result = detect_conflicting_documents(
            target_doc_chunks=doc_chunks,
            target_doc_id=doc_id,
            target_doc_title=doc_title,
            access_level=ctx.access_level,
            similarity_threshold=0.57,
            max_pairs=200,
            max_llm_calls=200,
            question=ctx.raw_question or ctx.question,
        )

        all_conflicts = []
        for pair in result.get("doc_pairs", []):
            all_conflicts.extend(pair.get("conflicts", []))

        ctx.conflicts = all_conflicts
        ctx.conflict_analysis = {
            "status": "complete" if not result.get("truncated") else "inconclusive",
            "type": "type_2b",
            "total_candidates": result.get("total_candidates", 0),
            "checked_candidates": result.get("total_llm_calls", 0),
            "unchecked_candidates": 0,
            "llm_calls": result.get("total_llm_calls", 0),
            "truncated": result.get("truncated", False),
            "failed_calls": result.get("failed_calls", 0),
            "inconclusive": result.get("failed_calls", 0) > 0 and result.get("total_llm_calls", 0) <= result.get("failed_calls", 0),
        }
        # Citations = the OTHER-doc clauses from conflict pairs, not the
        # entire source policy (which would just be self-referential junk).
        seen_ids: set[str] = set()
        citation_chunks: list[Dict[str, Any]] = []
        for conflict in all_conflicts:
            cb = conflict.get("clause_b", {})
            cb_id = cb.get("id", "")
            if cb_id and cb_id not in seen_ids:
                seen_ids.add(cb_id)
                citation_chunks.append({
                    "id": cb_id,
                    "document_id": cb.get("document_id", ""),
                    "title": cb.get("document_title", ""),
                    "clause_ref": cb.get("clause_ref", ""),
                    "text": cb.get("text", ""),
                    "score": conflict.get("similarity", 0.0),
                })
        ctx.chunks = citation_chunks
        ctx.state = QueryState.REASONED
    except Exception as e:
        ctx.conflicts = []
        ctx.conflict_analysis = {
            "status": "inconclusive", "type": "type_2b",
            "total_candidates": 0, "checked_candidates": 0,
            "unchecked_candidates": 0, "llm_calls": 0,
            "truncated": True, "inconclusive": True, "error": str(e),
        }
        ctx.state = QueryState.REASONED


def agent_risk_compliance(ctx: QueryContext):
    """Classify risk and map to regulations."""
    from services.agents.risk_agent import assess_risk

    try:
        ctx.risk_result = assess_risk(ctx.question, ctx.chunks)
        ctx.state = QueryState.REASONED
    except Exception as e:
        ctx.risk_result = {"error": str(e)}


def agent_conversational(ctx: QueryContext):
    """Handle greetings, thanks, and casual conversation via LLM."""
from services.agents.tools.llm_tools import llm_generate, AGENT_MODEL

    result = llm_generate(
        model=AGENT_MODEL,
        system_prompt=(
            "You are Codex, a friendly policy intelligence assistant. "
            "The user's message includes a Conversation History section showing recent turns. "
            "Use that context to respond naturally — reference prior answers if they mention them.\n"
            "Rules: Be warm but concise (1-3 sentences). Greet back, acknowledge thanks, "
            "redirect off-topic questions to policy topics. Never fabricate policy information. "
            "Do not use emojis."
        ),
        user_message=ctx.question,
        temperature=0.7,
        max_tokens=600,
        timeout=10.0,
        enable_thinking=True,
    )

    if result.success:
        answer = re.sub(r'<thinking>.*?</thinking>', '', result.data.strip(), flags=re.DOTALL).strip()
        ctx.answer = answer or "Hi! I'm Codex. How can I help with policy questions today?"
    else:
        ctx.answer = "Hi! I'm Codex. How can I help with policy questions today?"

    ctx.verdict = "clear"
    ctx.confidence = 1.0
    ctx.state = QueryState.REASONED


def agent_procedure_reason(ctx: QueryContext):
    """Dedicated reasoning for procedural queries."""
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
            ctx.state = QueryState.RETRIEVED
    except Exception as e:
        logger.error(f"Procedure reasoning failed: {e}")
        ctx.state = QueryState.RETRIEVED


def agent_resolve_docs(ctx: QueryContext):
    """Resolve document names using per-slot pgvector title search.

    For conflict intents: shows top 5 candidates per slot, sets
    AWAITING_SELECTION if user hasn't picked yet.
    For other intents: resolves top 1 for context enrichment.
    """
    from services.api.search import resolve_documents_by_name

    phrases = ctx.doc_phrases
    if not phrases:
        return

    top_k = 5 if ctx.intent == QueryIntent.CONFLICT else 1

    all_selected = []
    for i, phrase in enumerate(phrases):
        matches = resolve_documents_by_name(phrase, ctx.access_level, top_k=top_k, threshold=0.3)
        if not matches:
            continue

        slot_candidates = []
        for j, m in enumerate(matches):
            slot_candidates.append({
                "id": m["id"],
                "title": m["title"],
                "similarity": m["similarity"],
                "selected": j == 0,
            })

        ctx.doc_slots.append({
            "slot": i + 1,
            "phrase": phrase,
            "candidates": slot_candidates,
        })

        if slot_candidates:
            top = slot_candidates[0]
            all_selected.append(top)
            ctx.resolved_doc_ids.append(top["id"])

    ctx.resolved_documents = all_selected

    if ctx.intent == QueryIntent.CONFLICT and ctx.conflict_type is None:
        if len(ctx.resolved_doc_ids) == 1:
            ctx.conflict_type = "type_2b"
        elif len(ctx.resolved_doc_ids) >= 2:
            ctx.conflict_type = "type_2"


# ═══════════════════════════════════════
#  Pipeline Definition
# ═══════════════════════════════════════

DEFAULT_PIPELINE = [agent_retrieve, agent_reason, agent_verify]

INTENT_PIPELINES = {
    QueryIntent.APPROVAL: [agent_resolve_docs, agent_retrieve, agent_approval, agent_reason, agent_verify],
    QueryIntent.CONFLICT: [agent_resolve_docs, agent_conflict_check, agent_reason, agent_verify],
    QueryIntent.COMPLIANCE: [agent_resolve_docs, agent_retrieve, agent_risk_compliance, agent_reason, agent_verify],
    QueryIntent.PROCEDURE: [agent_resolve_docs, agent_retrieve, agent_procedure_reason, agent_verify],
    QueryIntent.GENERAL: [agent_resolve_docs, agent_retrieve, agent_reason, agent_verify],
    QueryIntent.CONVERSATIONAL: [agent_conversational],
}


def run_pipeline(
    question: str,
    user_id: str,
    access_level: int = 1,
    department: str = "",
    search_mode: str = "hybrid",
    raw_question: str = "",
    prior_intent: Optional[QueryIntent] = None,
    selected_doc_ids: Optional[List[str]] = None,
) -> QueryContext:
    """
    Two-phase query pipeline:

    Phase 1 (no selected_doc_ids):
      Classify intent → resolve docs → if type_2/2b, pause and return candidates

    Phase 2 (with selected_doc_ids):
      Run conflict analysis on user-selected documents
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

    # Phase 2: User selected documents — run conflict analysis
    if selected_doc_ids and prior_intent == QueryIntent.CONFLICT:
        ctx.intent = QueryIntent.CONFLICT
        ctx.intent_confidence = 1.0  # confirmed by user document selection
        ctx.resolved_doc_ids = selected_doc_ids
        ctx.state = QueryState.CLASSIFIED

        # Determine conflict subtype from selection count
        if len(selected_doc_ids) == 1:
            ctx.conflict_type = "type_2b"
        elif len(selected_doc_ids) >= 2:
            ctx.conflict_type = "type_2"

        pipeline = [agent_conflict_check, agent_reason, agent_verify]
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

        if ctx.state != QueryState.ABSTAINED:
            ctx.state = QueryState.DONE
        ctx.stop_timer()
        return ctx

    # Phase 1: Classify intent + extract doc phrases (single CoT call)
    ctx.intent, ctx.intent_confidence, ctx.conflict_type, ctx.doc_phrases = classify_intent(
        ctx.raw_question or ctx.question,
        prior_intent=prior_intent,
    )
    ctx.state = QueryState.CLASSIFIED

    # Select pipeline
    pipeline = INTENT_PIPELINES.get(ctx.intent, DEFAULT_PIPELINE)

    # Execute agents
    for agent_fn in pipeline:
        if ctx.state in (QueryState.ABSTAINED, QueryState.AWAITING_SELECTION):
            break
        agent_start = time.time()
        agent_fn(ctx)
        ctx.chain.append({
            "agent": agent_fn.__name__,
            "state": ctx.state.value,
            "latency_ms": int((time.time() - agent_start) * 1000),
            "output": ctx.verdict if agent_fn.__name__ == "agent_verify" else None,
        })

        # Pause after resolve_docs for type_2 and type_2b
        if (agent_fn.__name__ == "agent_resolve_docs"
                and ctx.state != QueryState.ABSTAINED
                and ctx.intent == QueryIntent.CONFLICT
                and ctx.conflict_type in ("type_2", "type_2b")
                and not selected_doc_ids
                and ctx.doc_slots):
            ctx.verdict = "pending_selection"
            ctx.answer = (
                "I found matching documents for your conflict query. "
                "Please select which documents you want to compare from the options below."
            )
            ctx.state = QueryState.AWAITING_SELECTION
            break

    # Step 4: Final state
    if ctx.state != QueryState.ABSTAINED:
        ctx.state = QueryState.DONE

    ctx.stop_timer()
    return ctx
