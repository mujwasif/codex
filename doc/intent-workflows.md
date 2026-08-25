# Intent Workflows

Complete workflow documentation for each of the 5 intents in the Codex Policy Intelligence Engine, from query to final answer.

---

## 1. GENERAL Intent

**Example**: *"What is the remote work policy?"*

```
User Question
    │
    ▼
[Classify Intent] ─── LLM classifies "general" (0.65 confidence)
    │                   or keyword fallback catches "what is", "tell me about"
    ▼
[Retrieve] ─── Hybrid search (vector + BM25 → RRF fusion → CrossEncoder rerank)
    │           Returns top 20 chunks (or 100 for procedures)
    │           Filtered by user's access_level (RBAC)
    ▼
[Reason] ─── Sends all chunks + question to Qwen3-8B
    │         System prompt: "Use ONLY provided context. Cite every claim."
    │         Now with CoT: LLM reasons in <thinking> block before answering
    │         Temperature 0.0 for deterministic output
    ▼
[Verify] ─── Regex extracts every [Doc: X, Clause: Y] from the answer
    │         Cross-references against actual retrieved chunks
    │         Any mismatch → answer marked "abstained"
    │         Confidence computed: sigmoid(reranker_score)×0.40 + citation_coverage×0.35 + chunk_availability×0.25
    ▼
[Done] ─── Returns answer with verdict, confidence, citations
```

**Key characteristics**: Simplest pipeline. No specialized agent — just the general reasoner. If the context doesn't contain the answer, the LLM says "Insufficient policy basis."

---

## 2. APPROVAL Intent

**Example**: *"Who can approve a purchase over $10,000?"*

```
User Question
    │
    ▼
[Classify Intent] ─── LLM classifies "approval" (0.95 confidence)
    │                   Keywords: "approve", "who can", "sign-off", "approval limit"
    ▼
[Retrieve] ─── Hybrid search → top 20 chunks
    │
    ▼
[Approval Agent] ─── Queries Neo4j knowledge graph:
    │                 MATCH (r:Role)-[:CAN_APPROVE]->(p:Process)
    │                 Returns all Role→Process pairs
    │
    │              ─── Extracts keywords from question:
    │                   "purchase" → hint "purchase", "10,000" → amount $10,000
    │
    │              ─── Scores each Process name against question keywords:
    │                   token overlap + substring matching + normalization
    │                   Minimum score threshold (1.0) to avoid false positives
    │
    │              ─── Resolves top-matching process → its approving roles
    │              ─── Checks if user's role is in the approving roles
    │
    │              ─── Builds structured result:
    │                   {process, matching_roles, user_can_approve, amount, approvable}
    │
    ▼
[Reason] ─── SKIPS general reasoner if approval answer exists
    │         (approval_agent answer is authoritative)
    │         If no approval result → falls back to general reasoner
    ▼
[Verify] ─── Citation check + confidence scoring (same as General)
    ▼
[Done] ─── Returns approval authority answer
```

**Key characteristics**: The approval agent bypasses the general LLM reasoner. The answer comes directly from the Neo4j knowledge graph — deterministic, not generated. If no matching process is found in the graph, it says "No approval chain defined."

---

## 3. CONFLICT Intent

**Example**: *"These two policies contradict each other"*

```
User Question
    │
    ▼
[Classify Intent] ─── LLM classifies "conflict" (0.95 confidence)
    │                   Keywords: "conflict", "contradict", "versus", "inconsisten"
    ▼
[Retrieve] ─── Hybrid search → top 20 chunks
    │
    ▼
[Conflict Check Agent] ─── Three-layer conflict detection:
    │
    │   Layer 1: Neo4j CONFLICTS_WITH relationships (pre-computed)
    │            MATCH (c:Clause)-[:CONFLICTS_WITH]->(other:Clause)
    │            Instant, deterministic
    │
    │   Layer 2: Version mismatch detection
    │            Same clause_ref across different documents
    │            e.g. "4.2" in Policy A vs "4.2" in Policy B = potential conflict
    │
    │   Layer 3: LLM semantic pairwise comparison
    │            For each cross-document pair of similar clauses:
    │            1. Neo4j check (already done above)
    │            2. Version check (already done above)
    │            3. LLM call with CoT prompt:
    │               - Identify subject of each clause
    │               - Check if same role/process/asset
    │               - Compare thresholds, obligation levels, time periods
    │               - Different roles = complementary, NOT conflicting
    │            4. Deterministic validation:
    │               - Role scope check (different roles → complementary)
    │               - Subject alignment check
    │               - Number verification (requirement values must appear in text)
    │               - Confidence threshold (confirmed_conflict needs ≥0.85)
    │
    │   LLM budget management: max 15 comparisons per query
    │   Evidence expansion: finds similar clauses from OTHER documents via vector search
    │
    ▼
[Reason] ─── Builds conflict answer from structured results:
    │         "I found 2 conflicts between your policies:"
    │         For each conflict:
    │           - Topic label (Password Policy, Data Retention, etc.)
    │           - Quote from Clause A with source
    │           - Quote from Clause B with source
    │           - Conflict reason
    │           - Citations for both sides
    │         Adds recommendation: "Align to stricter standard"
    │
    ▼
[Verify] ─── Extra check: conflict answer MUST cite both clause sources
    │         If only one citation → marked "abstained"
    ▼
[Done] ─── Returns conflict analysis with recommendations
```

**Key characteristics**: The most complex agent (901 lines). Uses evidence expansion to find candidates beyond the initial retrieval. Every conflict is validated through deterministic safety checks after the LLM call. The LLM budget prevents runaway costs.

---

## 4. COMPLIANCE Intent

**Example**: *"Are we GDPR compliant for data storage?"*

```
User Question
    │
    ▼
[Classify Intent] ─── LLM classifies "compliance" (0.95 confidence)
    │                   Keywords: "compliant", "violation", "GDPR", "regulation", "mandatory"
    ▼
[Retrieve] ─── Hybrid search → top 20 chunks
    │
    ▼
[Risk Agent] ─── Three-pronged approach:
    │
    │   Prong 1: Neo4j regulation mapping
    │            MATCH (p:Policy)-[:MAPS_TO]->(reg:Regulation)
    │            Returns which regulations apply (e.g. "GDPR", "ISO 27001")
    │
    │   Prong 2: Neo4j obligation levels
    │            MATCH (c:Clause) RETURN c.obligation
    │            Classifies each clause as "mandatory" or "optional"
    │
    │   Prong 3: LLM CoT classifier
    │            Sends top 10 chunks to Qwen3-8B with strict prompt:
    │            1. <thinking> — identify requirement, search context, check obligation level,
    │                           compare current state vs policy requirement
    │            2. Final output — JSON: {verdict, elaboration}
    │            Verdict is one of: "clear", "conditional", "violation"
    │            Default: "clear" if LLM fails (safe fallback)
    │
    │   Generates recommendations based on verdict:
    │            - violation: "CRITICAL: [elaboration]"
    │            - conditional: "NOTICE: [elaboration]"
    │            - clear: "No compliance concerns"
    │
    ▼
[Reason] ─── Builds risk answer from structured results:
    │         "A policy/regulation violation was detected."
    │         "Risk level: [level]"
    │         "Applicable regulations: GDPR, ISO 27001"
    │         "N mandatory obligation(s) apply"
    │         "Recommended actions: [specific steps]"
    │
    ▼
[Verify] ─── Citation check + confidence scoring
    ▼
[Done] ─── Returns compliance verdict with elaboration
```

**Key characteristics**: Hybrid approach — Neo4j provides objective regulation/obligation data, while the LLM provides semantic analysis. The LLM is forced to reason through a CoT before classifying. Violations trigger specific, actionable recommendations.

---

## 5. PROCEDURE Intent

**Example**: *"How do I reset my password?"*

```
User Question
    │
    ▼
[Classify Intent] ─── LLM classifies "procedure" (0.95 confidence)
    │                   Keywords: "how to", "steps", "workflow", "process for", "guide me"
    ▼
[Retrieve] ─── Hybrid search → top 100 chunks (5x more than other intents)
    │           Ensures ALL steps of a process are captured
    │
    ▼
[Procedure Agent] ─── Dedicated reasoner (bypasses general reasoner entirely):
    │
    │   1. Sends all 100 chunks to Qwen3-8B with CoT prompt:
    │      <thinking>
    │        - Identify the primary goal of the procedure
    │        - List all roles, departments, required documents
    │        - Map sequential dependencies (step 1 → step 2 → step 3)
    │        - Check mandatory prerequisites
    │        - Verify if steps are missing or ambiguous
    │      </thinking>
    │
    │   2. Final output (structured format):
    │      ### Procedure: [Process Name]
    │      **Prerequisites**: [items with citations]
    │      **Steps**:
    │        Step 1. **[Action]**: Description. [Doc: X, Clause: Y]
    │        Step 2. **[Action]**: Description. [Doc: X, Clause: Y]
    │      **Responsibility**: [role/department]
    │
    │   3. Post-processing:
    │      - Strip <thinking> block from output
    │      - If "Insufficient policy basis" → return None → fallback to general reasoner
    │      - If None → pipeline ends (no general reasoner in PROCEDURE chain)
    │
    ▼
[Verify] ─── Citation check + confidence scoring
    ▼
[Done] ─── Returns structured workflow
```

**Key characteristics**: Completely separate from the general reasoner. The procedure agent generates structured output (prerequisites → numbered steps → responsibility) instead of prose. The CoT ensures logical sequencing before output. Retrieves 100 chunks to capture multi-step processes.

---

## Shared: The Verification Guardrail

All intents pass through the same verification layer:

```
Answer from any agent
    │
    ▼
[Verify Citations] ─── Regex: \[Doc: (.*?), Clause: (.*?)\]
    │                    Each citation checked against retrieved chunks
    │                    Lenient matching: clause "4.2" matches "Rule 4.2 and Rule 4.3"
    │
    ▼
[Compute Confidence] ─── sigmoid(reranker_score) × 0.40
    │                      + citation_coverage (valid/total) × 0.35
    │                      + chunk_availability (found/5) × 0.25
    │
    ▼
[Verdict Assignment]
    │  Invalid citations or "Insufficient" → abstained (confidence capped at 30%)
    │  Risk violation/conditional → surfaces that verdict
    │  Conflicts detected → conflict verdict
    │  Otherwise → clear
    │
    ▼
[Final Response]
    │  {answer, verdict, confidence, citations, search_mode, intent}
    │  Logged to PostgreSQL (queries + answers + citations + audit_logs)
```
