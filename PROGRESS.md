# Codex Progress Tracker

> **Last updated:** 2026-07-15
> **Build spec:** Codex Engineering Build Specification (1) (1).docx
> **Non-negotiable invariant:** Every answer must cite a governing clause or abstain. Numeric thresholds and approval authorities come from the knowledge graph or cited clause — never generated. The system fails closed.

---

## Overall Status

| Week | Focus | Status |
|------|-------|--------|
| Week 1 | Ingestion & index foundation | ✅ 90% complete |
| Week 2 | RAG Q&A, citations, guardrails | ✅ 85% complete |
| Week 3 | Knowledge graph & specialized agents | ⚠️ 30% complete — Neo4j + KG done, agents pending |
| Week 4 | Eval, dashboard, integration, hardening | ❌ Not started |

---

## Section 01: Scope & Objectives

### In Scope (v1 / MVP)

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1 | Ingestion: Parse documents | ✅ DONE | `doc_parser.py:25` (DOCX), `doc_parser.py:130` (PDF) |
| 2 | Ingestion: OCR for scanned pages | ❌ NOT DONE | No OCR library installed |
| 3 | Ingestion: Structure-aware chunk | ✅ DONE | `structure_chunker.py:6` — clause-level chunks, 10-50 tokens |
| 4 | Ingestion: Embed chunks | ✅ DONE | `ingest.py:157` — bge-large-en-v1.5 (1024 dims) |
| 5 | Ingestion: Extract rules/entities | ⚠️ PARTIAL | `ingest.py:25` — regex extraction only, no LLM-based extraction |
| 6 | Ingestion: Extract into knowledge graph | ❌ NOT DONE | No Neo4j |
| 7 | Retrieval: Hybrid retrieval (vector + BM25) | ✅ DONE | `search.py:124` — RRF fusion |
| 8 | Retrieval: Reranking | ✅ DONE | `search.py:137` — CrossEncoder reranking |
| 9 | Reasoning: Grounded Q&A with citations | ✅ DONE | `reasoner.py:17` — DeepSeek-R1 with citation prompt |
| 10 | Reasoning: Approval-matrix resolution | ❌ NOT DONE | No Neo4j, no Approval-Matrix agent |
| 11 | Reasoning: Conflict detection | ❌ NOT DONE | No Conflict Detector agent |
| 12 | Reasoning: Compliance verdict | ⚠️ PARTIAL | Verifier exists, no Risk & Compliance agent |
| 13 | Surfaces: REST API | ✅ DONE | `main.py` — 18 endpoints |
| 14 | Surfaces: Web chat with citation viewer | ⚠️ PARTIAL | CLI chat exists (`cli.py`), no web UI |
| 15 | Surfaces: Compliance/admin dashboard | ❌ NOT DONE | `web/` directory is empty |
| 16 | Surfaces: One chat integration (Teams/Slack) | ❌ NOT DONE | No integration |
| 17 | Quality: Evaluation harness | ❌ NOT DONE | No eval framework |
| 18 | Quality: Golden Q&A set | ❌ NOT DONE | No golden set |
| 19 | Quality: CI quality gate | ❌ NOT DONE | No CI/CD |

### Out of Scope (Post-MVP) — Confirmed Deferred

| # | Item | Status |
|---|------|--------|
| 1 | Autonomous policy editing | ❌ Post-MVP |
| 2 | Live ERP write-back | ❌ Post-MVP |
| 3 | Regulatory-feed monitoring | ❌ Post-MVP |
| 4 | Multilingual support | ❌ Post-MVP |

### Non-Negotiable Invariant

| # | Rule | Status |
|---|------|--------|
| 1 | Every answer must cite a governing clause or abstain | ✅ DONE — `verifier.py:3` + `main.py:180` |
| 2 | Numeric thresholds from graph/clause only, never generated | ⚠️ PARTIAL — No knowledge graph; thresholds extracted via regex |
| 3 | System fails closed | ✅ DONE — abstention on any failure |

---

## Section 02: System Architecture

### Platform Planes

| # | Plane | Components Required | Status |
|---|-------|-------------------|--------|
| 1 | **Ingestion & Knowledge** | Parsers | ✅ `doc_parser.py` |
| 2 | | OCR | ❌ Missing |
| 3 | | Structure-aware chunker | ✅ `structure_chunker.py` |
| 4 | | Embedding service | ✅ `ingest.py:157` |
| 5 | | Entity/rule extractor | ⚠️ `ingest.py:25` (regex only) |
| 6 | | Vector store (pgvector) | ✅ `db.py` + `init.sql:29` |
| 7 | | Knowledge graph (Neo4j) | ✅ DONE — 2,228 nodes, 4,303 relationships |
| 8 | | Object storage (MinIO) | ❌ Removed by choice |
| 9 | **Reasoning & Orchestration** | LLM orchestrator (LangGraph) | ❌ Missing — hardcoded function chain |
| 10 | | Specialized agents (7) | ⚠️ 2 of 7 done (Reasoner + Verifier) |
| 11 | | Tool calling | ❌ Missing |
| 12 | | Memory (conversation) | ⚠️ CLI only (`cli.py:101`) |
| 13 | **Trust & Governance** | Guardrails | ✅ 4-layer chain |
| 14 | | Citation checks | ✅ `verifier.py:3` |
| 15 | | Confidence + abstention | ✅ `main.py:50` (percentage scoring) |
| 16 | | RBAC | ✅ `main.py:117` |
| 17 | | Audit log | ✅ `main.py:86` |
| 18 | | Evaluation harness | ❌ Missing |
| 19 | **Experience & Integration** | Web app | ❌ `web/` empty |
| 20 | | Citation viewer | ❌ Missing |
| 21 | | Compliance dashboard | ❌ Missing |
| 22 | | Teams/Slack bots | ❌ Missing |
| 23 | | ERP/workflow connectors | ❌ Missing |

### Data Flow

| Step | Required | Status | Evidence |
|------|----------|--------|----------|
| Sources → Ingest | ✅ | ✅ | `ingest.py:main()` |
| Ingest → Stores | ✅ | ✅ | PostgreSQL + Neo4j (2,228 nodes, 4,303 rels) |
| Stores → Orchestrator | ✅ | ⚠️ | Hardcoded function chain, no LangGraph |
| Orchestrator → Multi-Agent | ✅ | ⚠️ | 2 agents (Reasoner, Verifier), missing 5 |
| Multi-Agent → Guardrails | ✅ | ✅ | 4-layer guardrail chain |
| Guardrails → Cited Answer | ✅ | ✅ | Full answer payload |
| Cited Answer → Audit Log | ✅ | ✅ | `main.py:262` |

---

## Section 03: Multi-Agent Design

### Required: LangGraph State Machine

| # | Requirement | Status | Notes |
|---|-------------|--------|-------|
| 1 | Pure Python state machine orchestrator (no LangGraph) | ❌ NOT DONE | User chose no frameworks; build pure Python state machine |
| 2 | Shared state object (question, role, dept, clauses, verdict, citations, confidence) | ⚠️ PARTIAL | Data flows through function calls, not a state object |
| 3 | Conditional edges by intent | ❌ NOT DONE | No intent classification or routing |
| 4 | Failure degradation to abstention | ✅ DONE | `main.py:180` — any failure → abstained |

### Required: 7 Specialized Agents

| # | Agent | Responsibility | Status | File |
|---|-------|---------------|--------|------|
| 1 | **Orchestrator / Planner** | Interpret question, classify intent, route, assemble answer | ⚠️ PARTIAL | `main.py:175` — hardcoded chain, no intent classification |
| 2 | **Retriever** | Hybrid search + reranking + RBAC filtering | ✅ DONE | `search.py:93` |
| 3 | **Policy Reasoner** | Grounded answer from retrieved clauses only | ✅ DONE | `reasoner.py:17` |
| 4 | **Approval-Matrix Agent** | Role → threshold → authority traversal in graph | ❌ NOT DONE | No Neo4j, no agent |
| 5 | **Conflict Detector** | Cross-check clauses for contradictions/version mismatch | ❌ NOT DONE | No agent |
| 6 | **Risk & Compliance** | Classify action (Clear/Conditional/Violation), map to regulations | ❌ NOT DONE | No agent |
| 7 | **Citation & Verifier** | Validate claims are clause-supported, force abstention | ✅ DONE | `verifier.py:3` |

---

## Section 04: AI Engineering Stack & Rationale

| # | Technology | Required | Implemented | Gap |
|---|-----------|----------|-------------|-----|
| 1 | Document parsing (Azure DI / unstructured / LlamaParse) | ✅ | ✅ `python-docx` + `PyPDF2` | Different library, same function |
| 2 | OCR (Document Intelligence / Textract / Tesseract) | ✅ | ❌ | Not implemented |
| 3 | Chunking (structure-aware, clause-level) | ✅ | ✅ | `structure_chunker.py` |
| 4 | Embeddings (text-embedding-3-large / Cohere / bge) | ✅ | ✅ | `bge-large-en-v1.5` |
| 5 | Vector DB (pgvector / Qdrant) | ✅ | ✅ | `pgvector 0.8.0` |
| 6 | Knowledge graph (Neo4j) | ✅ | ❌ | Not implemented |
| 7 | Hybrid retrieval (vector + BM25 + reranker) | ✅ | ✅ | `search.py` |
| 8 | Reasoning LLM (GPT-4-class / Claude / open-weights) | ✅ | ✅ | `DeepSeek-R1-Distill-Llama-8B` |
| 9 | Orchestration (LangGraph / Semantic Kernel) | ✅ | ❌ | Hardcoded function chain |
| 10 | Multi-agent (specialized agents per sub-task) | ✅ | ⚠️ | 2 of 7 agents implemented |
| 11 | Tool calling (structured function calls) | ✅ | ❌ | No tool calling |
| 12 | Guardrails (input/output validation, grounding) | ✅ | ✅ | 4-layer guardrail chain |
| 13 | Memory (conversation + entity context) | ✅ | ⚠️ | CLI history only, no persistent memory |
| 14 | Evaluation (RAGAS-style + custom checks) | ✅ | ❌ | No eval framework |
| 15 | Human-in-the-loop (low-confidence review queue) | ✅ | ❌ | Feedback endpoint exists but no review queue |

---

## Section 05: Data Pipelines

### 5.1 Ingestion Pipeline

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1 | Ingest via API | ❌ NOT DONE | No `POST /v1/ingest` endpoint |
| 2 | Ingest via watched folder | ⚠️ PARTIAL | `ingest.py:202` reads from `archive/` folder, not watched |
| 3 | Ingest via connector | ❌ NOT DONE | No connectors |
| 4 | Store raw file in object storage | ❌ NOT DONE | No MinIO/object storage |
| 5 | Parse to text + layout + tables | ✅ DONE | `doc_parser.py` |
| 6 | OCR fallback for scanned pages | ❌ NOT DONE | No OCR |
| 7 | Segment into clause-level chunks | ✅ DONE | `structure_chunker.py:6` |
| 8 | Capture metadata (document_id, version, effective_date, section_path, clause_ref, page, source_uri, access_tags) | ⚠️ PARTIAL | Most fields present; no `version` tracking on new versions |
| 9 | Embed chunks | ✅ DONE | `ingest.py:157` |
| 10 | Upsert vectors to pgvector | ✅ DONE | `ingest.py:165` |
| 11 | Upsert metadata to PostgreSQL | ✅ DONE | `ingest.py:130` |
| 12 | Build BM25 index | ✅ DONE | `bm25_index.py` |
| 13 | Extract rules/entities to Neo4j | ✅ DONE | `kg_extractor.py` + `migrate_to_neo4j.py` |
| 14 | Extract: roles | ✅ DONE | 60 roles extracted |
| 15 | Extract: thresholds | ⚠️ PARTIAL | No monetary thresholds in corpus |
| 16 | Extract: approval authorities | ✅ DONE | 162 CAN_APPROVE relationships |
| 17 | Extract: document relationships | ✅ DONE | PART_OF, GOVERNS, MAPS_TO, SUPERSEDES |
| 18 | Extract: regulatory references | ✅ DONE | 17 regulations |
| 19 | New version: supersede prior document | ❌ NOT DONE | No versioning |
| 20 | New version: diff changes | ❌ NOT DONE | No versioning |
| 21 | New version: mark superseded clauses | ❌ NOT DONE | No versioning |
| 22 | New version: emit change events | ❌ NOT DONE | No notifications |
| 23 | Async, queued ingestion | ❌ NOT DONE | Synchronous only |
| 24 | Idempotent ingestion | ⚠️ PARTIAL | Re-running duplicates chunks |

### 5.2 Retrieval & Reasoning Pipeline

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1 | Receive query + user context (role, dept) | ✅ DONE | `main.py:175` |
| 2 | Orchestrator classifies intent | ❌ NOT DONE | No intent classification |
| 3 | Orchestrator classifies policy domain | ❌ NOT DONE | No domain classification |
| 4 | Hybrid retrieve (vector + BM25) | ✅ DONE | `search.py:124` |
| 5 | Rerank candidates | ✅ DONE | `search.py:137` |
| 6 | Filter by RBAC access tags | ⚠️ PARTIAL | Access level filtering, not tag-based |
| 7 | If approval intent: query Approval-Matrix agent | ❌ NOT DONE | No Approval-Matrix agent |
| 8 | Policy Reasoner: answer grounded only in retrieved clauses | ✅ DONE | `reasoner.py:17` |
| 9 | Conflict Detector: check contradictions/version mismatch | ❌ NOT DONE | No Conflict Detector agent |
| 10 | Risk & Compliance: assign verdict, missing items, regulatory mapping | ⚠️ PARTIAL | Verdict exists, no regulatory mapping |
| 11 | Citation & Verifier: check each claim against clause | ✅ DONE | `verifier.py:3` |
| 12 | Citation & Verifier: compute confidence | ✅ DONE | `main.py:50` (percentage scoring) |
| 13 | Citation & Verifier: abstain if unsupported | ✅ DONE | `main.py:180` |
| 14 | Return answer payload | ✅ DONE | `main.py:280` |
| 15 | Write audit log | ✅ DONE | `main.py:262` |
| 16 | Write telemetry | ❌ NOT DONE | No telemetry |

### 5.3 Grounding & Guardrails

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1 | Input: PII handling | ❌ NOT DONE | No PII filtering |
| 2 | Input: Prompt-injection filtering | ❌ NOT DONE | No input sanitization |
| 3 | Input: Scope check before retrieval | ⚠️ PARTIAL | RBAC check only |
| 4 | Grounded generation: reference retrieved clause IDs | ✅ DONE | `reasoner.py:12` |
| 5 | Grounded generation: reject ungrounded generation | ✅ DONE | Verifier rejects invalid citations |
| 6 | Verifier pass: clause support check (NLI/LLM-verify) | ⚠️ PARTIAL | Regex-based, not NLI/LLM |
| 7 | Verifier pass: drop unsupported claims | ✅ DONE | Abstention on invalid citations |
| 8 | Abstention: below threshold τ → abstain | ✅ DONE | Confidence scoring + abstention |
| 9 | Hard rule: numeric thresholds from graph/clause only | ⚠️ PARTIAL | Regex extraction, no knowledge graph |
| 10 | Output: schema validation | ✅ DONE | Pydantic models |
| 11 | Output: mandatory citations | ✅ DONE | Citation check in verifier |
| 12 | Output: RBAC redaction of restricted content | ⚠️ PARTIAL | Access level filtering, not redaction |

---

## Section 06: Data Model

### Relational Store (PostgreSQL + pgvector)

| Table | Required Fields | Status | Notes |
|-------|----------------|--------|-------|
| `documents` | id, title, type, owner, version, effective_date, status, source_uri, access_tags | ✅ DONE | `init.sql:7` |
| `chunks` | id, document_id, section_path, clause_ref, page, text, embedding (vector), token_count | ✅ DONE | `init.sql:20` |
| `entities` | id, type, name, document_id, attrs (jsonb) | ✅ DONE | `init.sql:35` |
| `queries` | id, user_id, role, dept, question, intent, created_at | ✅ DONE | `init.sql:43` |
| `answers` | id, query_id, answer, verdict, confidence, abstained, latency_ms | ✅ DONE | `init.sql:52` |
| `citations` | id, answer_id, chunk_id, document_id, clause_ref, score | ✅ DONE | `init.sql:62` |
| `feedback` | id, answer_id, rating, note, reviewer | ✅ DONE | `init.sql:72` |
| `audit_log` | id, actor, action, payload (jsonb), ts | ✅ DONE | `init.sql:79` |

### Knowledge Graph (Neo4j)

| # | Node Type | Required Properties | Status |
|---|-----------|-------------------|--------|
| 1 | Policy | title, version, effective_date, status | ✅ DONE — 24 nodes |
| 2 | Clause | clause_ref, section_path, text_ref | ✅ DONE — 1,842 nodes |
| 3 | Role / Department | name, level | ✅ DONE — 60 roles, 17 departments |
| 4 | Process | name, category | ✅ DONE — 268 nodes |
| 5 | Threshold | amount, currency, basis | ⚠️ PARTIAL — No thresholds in corpus (UnderDefense MAXI docs lack monetary amounts) |
| 6 | Regulation | name (NDPA, ISO, etc.), ref | ✅ DONE — 17 nodes |

### Knowledge Graph Relationships

| # | Relationship | Meaning | Status |
|---|-------------|---------|--------|
| 1 | (Clause)-[:PART_OF]->(Policy) | Clause belongs to policy/version | ✅ DONE — 1,842 rels |
| 2 | (Role)-[:CAN_APPROVE {max_amount}]->(Process) | Approval authority with limit | ✅ DONE — 162 rels (no amounts — corpus lacks monetary thresholds) |
| 3 | (Process)-[:REQUIRES_THRESHOLD]->(Threshold) | Action gated by limit band | ⚠️ PARTIAL — No threshold nodes in corpus |
| 4 | (Clause)-[:GOVERNS]->(Process) | Clause is the rule for a process | ✅ DONE — 2,237 rels |
| 5 | (Policy)-[:MAPS_TO]->(Regulation) | Policy satisfies regulation | ✅ DONE — 39 rels |
| 6 | (Clause)-[:CONFLICTS_WITH]->(Clause) | Detected contradiction | ⚠️ PARTIAL — Code exists, skipped (too expensive for 1,842 clauses) |
| 7 | (Policy)-[:SUPERSEDES]->(Policy) | Version lineage | ✅ DONE — 0 rels (single-version corpus) |
| 8 | (Role)-[:BELONGS_TO]->(Department) | Role belongs to department | ✅ DONE — 23 rels |

### Approval Resolution (Cypher)

```cypher
MATCH (role:Role {name:$role})-[a:CAN_APPROVE]->(p:Process {name:$process})
WHERE $amount <= a.max_amount
RETURN role.name AS authority, a.max_amount AS limit
ORDER BY a.max_amount ASC LIMIT 1;
```

Status: ✅ DONE — Graph has 60 roles, 268 processes, 162 CAN_APPROVE relationships. Query ready for agent.

---

## Section 07: API Surface

| # | Endpoint | Purpose | Auth | Status | File |
|---|----------|---------|------|--------|------|
| 1 | `POST /v1/ingest` | Enqueue document for ingestion | admin | ❌ NOT DONE | — |
| 2 | `GET /v1/documents` | List corpus + version status | scoped | ✅ DONE | `main.py:436` |
| 3 | `GET /v1/documents/{id}` | Document + clause metadata | scoped | ✅ DONE | `main.py:470` |
| 4 | `POST /v1/query` | Ask policy question → answer payload | user | ✅ DONE | `main.py:175` |
| 5 | `POST /v1/review` | Submit feedback / human review | reviewer | ⚠️ PARTIAL | `main.py:327` — feedback only, no review queue |
| 6 | `GET /v1/audit` | Audit-log access | admin | ✅ DONE | `main.py:396` |
| 7 | `GET /v1/metrics` | Eval + usage telemetry | admin | ❌ NOT DONE | — |
| 8 | `POST /v1/integrations/{teams\|slack}` | Inbound chat webhook | signed | ❌ NOT DONE | — |

### Answer Payload Format

```json
{
  "answer": "...",
  "verdict": "clear | conditional | violation | abstained",
  "confidence": 0.0,
  "citations": [
    {"document": "", "version": "", "section": "", "clause": "", "score": 0.0}
  ],
  "reasoning": "...",
  "next_steps": ["..."],
  "missing": ["required document / approval ..."]
}
```

| Field | Status | Notes |
|-------|--------|-------|
| answer | ✅ | — |
| verdict | ⚠️ | Only "clear" and "abstained" — no "conditional" or "violation" |
| confidence | ✅ | Percentage scoring (0-100) |
| citations | ✅ | — |
| reasoning | ❌ | Not returned |
| next_steps | ❌ | Not returned |
| missing | ❌ | Not returned |

### Additional Endpoints (Implemented, Not in Spec)

| Endpoint | Purpose | Status |
|----------|---------|--------|
| `POST /register` | User registration | ✅ Extra |
| `POST /login` | Authentication | ✅ Extra |
| `GET /query/history` | User query history | ✅ Extra |
| `GET /chunks` | Browse/search chunks | ✅ Extra |
| `GET /chunks/{id}` | Single chunk detail | ✅ Extra |
| `GET /entities` | Browse entities | ✅ Extra |
| `GET /health` | System health check | ✅ Extra |
| `POST /admin/refresh-index` | Rebuild BM25 index | ✅ Extra |

---

## Section 08: Engineering Deliverables

| # | Deliverable | Status |
|---|-------------|--------|
| 1 | Working ingestion pipeline | ✅ DONE |
| 2 | Searchable vector index | ✅ DONE |
| 3 | Hybrid retrieval with reranking | ✅ DONE |
| 4 | Grounded Q&A with citations | ✅ DONE |
| 5 | Citation verification guardrail | ✅ DONE |
| 6 | Confidence scoring | ✅ DONE |
| 7 | REST API | ✅ DONE |
| 8 | CLI chat | ✅ DONE |
| 9 | Knowledge graph (Neo4j) | ✅ DONE — 2,228 nodes, 4,303 relationships |
| 10 | 7 specialized agents (LangGraph) | ⚠️ 2 of 7 |
| 11 | Web UI + citation viewer | ❌ NOT DONE |
| 12 | Compliance dashboard | ❌ NOT DONE |
| 13 | Teams/Slack integration | ❌ NOT DONE |
| 14 | Evaluation harness | ❌ NOT DONE |
| 15 | CI/CD pipeline | ❌ NOT DONE |
| 16 | Docker/K8s deployment | ❌ NOT DONE |

---

## Section 09: Evaluation & Quality Gates

| # | Requirement | Status | Notes |
|---|-------------|--------|-------|
| 1 | Golden Q&A set per corpus | ❌ NOT DONE | No golden set |
| 2 | Metric: Faithfulness | ❌ NOT DONE | No RAGAS |
| 3 | Metric: Context precision | ❌ NOT DONE | — |
| 4 | Metric: Context recall | ❌ NOT DONE | — |
| 5 | Metric: Answer relevance | ❌ NOT DONE | — |
| 6 | Metric: Citation accuracy | ⚠️ PARTIAL | `verifier.py` checks citations, but not measured systematically |
| 7 | Metric: Abstention correctness | ⚠️ PARTIAL | Tested in `test_reasoning.py`, not measured |
| 8 | Metric: Threshold-correctness | ❌ NOT DONE | — |
| 9 | CI gate: block deploy on faithfulness drop | ❌ NOT DONE | No CI |
| 10 | CI gate: block deploy on fabricated thresholds | ❌ NOT DONE | — |
| 11 | Red-team set (adversarial, ambiguous, injection) | ❌ NOT DONE | — |
| 12 | Feedback loop: low-confidence → human review queue | ❌ NOT DONE | Feedback endpoint exists, no review queue |
| 13 | Feedback loop: corrections → golden set | ❌ NOT DONE | — |

---

## Section 10: Non-Functional Requirements

| # | Dimension | Requirement | Status |
|---|-----------|-------------|--------|
| 1 | Latency | p95 answer under a few seconds | ⚠️ PARTIAL — Works but LLM latency varies |
| 2 | Scale | Tens of thousands of clauses per tenant | ⚠️ PARTIAL — 1,842 chunks working, untested at scale |
| 3 | Security | Encryption at rest and in transit | ⚠️ PARTIAL — HTTPS not configured |
| 4 | Security | Secrets manager | ❌ NOT DONE | Hardcoded credentials |
| 5 | Security | Per-tenant data isolation | ❌ NOT DONE | Single-tenant |
| 6 | Security | RBAC-scoped retrieval | ✅ DONE | Access level filtering |
| 7 | Security | Immutable audit log | ✅ DONE | `audit_logs` table |
| 8 | Data residency | VPC/on-prem deployment option | ❌ NOT DONE | — |
| 9 | Data residency | Open-weights model path | ✅ DONE | DeepSeek-R1 local |
| 10 | Availability | Health checks | ✅ DONE | `GET /health` |
| 11 | Availability | Graceful degradation (fail to abstain) | ✅ DONE | Any failure → abstained |
| 12 | Observability | Every agent run traced | ❌ NOT DONE | No tracing |
| 13 | Observability | Token/cost/latency metrics | ❌ NOT DONE | Latency tracked, no token/cost |
| 14 | Observability | Grounding rate + abstention rate | ❌ NOT DONE | — |

---

## Section 11: MVP Build Plan — Four Weeks

### Week 1: Ingestion & Index Foundation

| # | Task | Status | Evidence |
|---|------|--------|----------|
| 1 | Stand up repo structure | ✅ DONE | `codex/` monorepo |
| 2 | Infrastructure (Postgres + pgvector) | ✅ DONE | PostgreSQL 16.8 + pgvector 0.8.0 |
| 3 | Object storage (MinIO) | ❌ REMOVED | Removed by choice |
| 4 | Auth skeleton | ✅ DONE | JWT + argon2 (`auth.py`) |
| 5 | Ingestion: upload | ❌ NOT DONE | No upload endpoint |
| 6 | Ingestion: parse | ✅ DONE | `doc_parser.py` |
| 7 | Ingestion: OCR | ❌ NOT DONE | No OCR |
| 8 | Ingestion: structure-aware chunk | ✅ DONE | `structure_chunker.py` |
| 9 | Ingestion: embed | ✅ DONE | `ingest.py` |
| 10 | Ingestion: store with metadata | ✅ DONE | PostgreSQL |
| 11 | Ingest seed corpus | ✅ DONE | 25 DOCX files, 1,842 chunks |
| 12 | **Definition of done:** Searchable index; semantic retrieval end-to-end | ✅ DONE | pgvector + BM25 + RRF + reranking |

### Week 2: RAG Q&A, Citations, Guardrails

| # | Task | Status | Evidence |
|---|------|--------|----------|
| 1 | Orchestrator | ⚠️ PARTIAL | Hardcoded chain, no LangGraph |
| 2 | Grounded-Q&A agent | ✅ DONE | `reasoner.py` |
| 3 | Hybrid retrieval (vector + BM25) | ✅ DONE | `search.py` |
| 4 | Reranking | ✅ DONE | CrossEncoder |
| 5 | Citation resolution to exact clause | ✅ DONE | `verifier.py` + `clause_ref` |
| 6 | Guardrails: grounding check | ✅ DONE | System prompt + verifier |
| 7 | Guardrails: cite-or-abstain | ✅ DONE | `main.py:180` |
| 8 | Guardrails: confidence threshold | ✅ DONE | `main.py:50` (percentage) |
| 9 | Minimal chat UI | ⚠️ PARTIAL | CLI exists, no web UI |
| 10 | Citation viewer | ❌ NOT DONE | No visual viewer |
| 11 | **Definition of done:** Employee can ask policy question, get cited grounded answer | ✅ DONE | Full pipeline working |

### Week 3: Knowledge Graph & Specialized Agents

| # | Task | Status | Evidence |
|---|------|--------|----------|
| 1 | Neo4j setup | ✅ DONE | Neo4j 5.26.0 at `/home/mujtaba/neo4j-community-5.26.0/`, Java 21, auth disabled |
| 2 | Extract entities/rules into Neo4j | ✅ DONE | `kg_extractor.py` + `migrate_to_neo4j.py` — 2,228 nodes, 4,303 relationships |
| 3 | Build Approval-Matrix agent | ❌ NOT DONE | — |
| 4 | Build Conflict-Detector agent | ❌ NOT DONE | — |
| 5 | Build Risk/Compliance agent | ❌ NOT DONE | — |
| 6 | Wire multi-agent orchestration (state machine) | ❌ NOT DONE | No orchestration framework |
| 7 | Tool calling for agents | ❌ NOT DONE | — |
| 8 | **Definition of done:** Approval queries + conflict detection work | ❌ NOT STARTED | — |

### Week 4: Eval, Dashboard, Integration, Hardening

| # | Task | Status | Evidence |
|---|------|--------|----------|
| 1 | Evaluation harness + golden set | ❌ NOT DONE | — |
| 2 | Measure faithfulness, precision, recall | ❌ NOT DONE | — |
| 3 | Tune based on metrics | ❌ NOT DONE | — |
| 4 | Compliance dashboard (analytics, risk, gaps, audit) | ❌ NOT DONE | — |
| 5 | One integration (Teams or Slack) | ❌ NOT DONE | — |
| 6 | Add memory | ⚠️ PARTIAL | CLI only |
| 7 | Harden RBAC | ⚠️ PARTIAL | Basic RBAC exists |
| 8 | Deploy to staging | ❌ NOT DONE | — |
| 9 | **Definition of done:** Demoable, measured, integrated MVP ready for pilot | ❌ NOT STARTED | — |

---

## Section 12: Repository & Environment

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1 | Repo structure per spec | ✅ DONE | `services/`, `packages/`, `infra/`, `eval/`, `web/` |
| 2 | Config: env vars via secrets manager | ❌ NOT DONE | Hardcoded in code |
| 3 | `.env.example` checked in | ❌ NOT DONE | No `.env.example` |
| 4 | `.gitignore` checked in | ❌ NOT DONE | No `.gitignore` |
| 5 | docker-compose: Postgres + pgvector | ✅ DONE | `infra/docker-compose.yaml` |
| 6 | docker-compose: Neo4j | ❌ NOT DONE | — |
| 7 | docker-compose: Redis | ❌ NOT DONE | — |
| 8 | docker-compose: MinIO | ❌ REMOVED | Removed by choice |
| 9 | CI: GitHub Actions (lint → test → eval gate → build → deploy) | ❌ NOT DONE | No CI/CD |
| 10 | `requirements.txt` | ✅ DONE | Created |
| 11 | `AGENTS.md` | ✅ DONE | Created |
| 12 | `PROGRESS.md` (this file) | ✅ DONE | Created |

---

## Section 13: Technical Risks & Open Decisions

| # | Risk | Mitigation | Status |
|---|------|------------|--------|
| 1 | Table-extraction fidelity from scanned approval matrices | Document Intelligence + human-verification step | ❌ No OCR, no human verification |
| 2 | LLM hosting vs. data residency | Decide per client: managed vs. on-prem open-weights | ⚠️ On-prem path via DeepSeek-R1 |
| 3 | Rule extraction accuracy (rules from prose) | Schema-constrained extraction, confidence thresholds, HITL | ⚠️ Regex extraction + confidence scoring |
| 4 | Model/embedding versioning & re-index cost | Pin model versions; incremental re-embed on updates | ⚠️ Pinned versions, no incremental re-embed |
| 5 | Multi-tenancy isolation model | Decide DB-per-tenant vs. row-level security before first pilot | ❌ Single-tenant only |
| 6 | Ground-truth ownership | Compliance sign-off process for golden set per corpus | ❌ No golden set |

---

## Section 14: Stretch / Post-MVP Engineering Roadmap

| # | Theme | Capability | Status |
|---|-------|-----------|--------|
| 1 | Autonomy | Multi-agent collaboration (planner, reasoner, verifier negotiate multi-step questions) | ❌ Post-MVP |
| 2 | Autonomy | Automatic policy updates (watch repos, detect new versions, re-index, diff, alerts) | ❌ Post-MVP |
| 3 | Governance | Regulatory monitoring (ingest NDPC, CBN, NAICOM, ISO feeds; map to internal policy) | ❌ Post-MVP |
| 4 | Governance | Policy authoring copilot (draft, redline, check for conflicts) | ❌ Post-MVP |
| 5 | Workflow | Enterprise workflow automation (auto-route approvals, generate docs, trigger tasks) | ❌ Post-MVP |
| 6 | Connectivity | Microsoft Teams & Slack native bots | ❌ Post-MVP |
| 7 | Connectivity | ERPNext, SAP, Dynamics, Oracle integration | ❌ Post-MVP |
| 8 | Deployment | On-prem & sovereign deployment | ❌ Post-MVP |

---

## Summary Dashboard

### By Category

| Category | Total Items | ✅ Done | ⚠️ Partial | ❌ Missing |
|----------|------------|---------|------------|-----------|
| Section 01: Scope & Objectives | 23 | 13 | 5 | 5 |
| Section 02: System Architecture | 23 | 9 | 4 | 10 |
| Section 03: Multi-Agent Design | 11 | 3 | 1 | 7 |
| Section 04: AI Stack | 15 | 9 | 2 | 4 |
| Section 05.1: Ingestion Pipeline | 24 | 15 | 5 | 4 |
| Section 05.2: Retrieval & Reasoning | 16 | 10 | 4 | 2 |
| Section 05.3: Grounding & Guardrails | 12 | 6 | 4 | 2 |
| Section 06: Data Model (Relational) | 8 | 8 | 0 | 0 |
| Section 06: Data Model (Graph) | 14 | 9 | 2 | 3 |
| Section 07: API Surface | 8 | 3 | 2 | 3 |
| Section 08: Deliverables | 16 | 9 | 1 | 6 |
| Section 09: Evaluation | 13 | 0 | 2 | 11 |
| Section 10: Non-Functional | 14 | 5 | 4 | 5 |
| Section 11: Week 1 | 12 | 9 | 1 | 2 |
| Section 11: Week 2 | 11 | 8 | 2 | 1 |
| Section 11: Week 3 | 8 | 2 | 1 | 5 |
| Section 11: Week 4 | 9 | 0 | 2 | 7 |
| Section 12: Repository | 12 | 5 | 0 | 7 |
| Section 13: Technical Risks | 6 | 0 | 3 | 3 |
| Section 14: Post-MVP | 8 | 0 | 0 | 8 |
| **TOTAL** | **252** | **113** | **41** | **98** |

### Completion

| Metric | Value |
|--------|-------|
| **Total requirements** | 252 |
| **Fully done** | 113 (44.8%) |
| **Partially done** | 41 (16.3%) |
| **Not done** | 98 (38.9%) |

### Top Priority Gaps (Week 3 blockers)

| # | Gap | Impact | Effort |
|---|-----|--------|--------|
| 1 | Pure Python state machine orchestrator | Blocks multi-agent routing | Medium |
| 2 | Approval-Matrix Agent | Core Week 3 feature | Medium |
| 3 | Conflict Detector Agent | Core Week 3 feature | Medium |
| 4 | Risk & Compliance Agent | Core Week 3 feature | Medium |
| 5 | Tool calling | Required for agent coordination | Medium |
| 6 | Intent classification + routing | Required for orchestrator | Medium |

### Quick Wins (can be done anytime)

| # | Item | Effort |
|---|------|--------|
| 1 | `.gitignore` | 5 min |
| 2 | `.env.example` | 10 min |
| 3 | Clean junk files (Zone.Identifier, init.sql/, logs) | 10 min |
| 4 | `POST /v1/ingest` endpoint | 2 hours |
| 5 | `GET /v1/metrics` endpoint | 2 hours |
| 6 | Verdict "conditional" + "violation" | 1 hour |
| 7 | Add `reasoning`, `next_steps`, `missing` to answer payload | 2 hours |
| 8 | PII filtering on input | 2 hours |
| 9 | Prompt-injection filtering | 2 hours |
| 10 | Split `main.py` into routers | 3 hours |

---

## Changelog

| Date | Change |
|------|--------|
| 2026-07-13 | Initial creation — comprehensive audit of all 14 build spec sections |
| 2026-07-15 | Neo4j KG complete — 2,228 nodes, 4,303 relationships. 60 roles, 17 departments, 17 regulations, 268 processes. Entity cleanup done. `kg_extractor.py` + `migrate_to_neo4j.py` validated. Progress: 113/252 (44.8%) |
