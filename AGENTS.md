# Codex — Policy Intelligence Engine

Enterprise document intelligence system that ingests corporate policy documents and serves grounded, cited answers via API. Every answer must cite a governing clause or abstain — no hallucinated responses allowed.

---

## Architecture

```
                    INGESTION (offline)
                    ===================
archive/*.docx
  → doc_parser.py          (parse DOCX/PDF structure)
  → clause_detector.py     (split into individual clauses via Phi-4-mini LLM)
  → structure_chunker.py   (clause-level chunks with 3-token overlap)
  → ingest.py              (embed with bge-large-en-v1.5, extract entities)
  → PostgreSQL + pgvector  (1,842 chunks with vector(1024) embeddings)


                    QUERY (online)
                    ==============
User: "How often should passwords change?"
  → main.py /query         (authenticate, orchestrate pipeline)
  → search.py              (vector + BM25 → RRF fusion → CrossEncoder rerank → top 3)
  → reasoner.py            (DeepSeek-R1 generates grounded answer with citations)
  → verifier.py            (regex check — all citations match retrieved chunks?)
  → confidence.py          (multi-signal confidence score as percentage)
  → Response: {answer, verdict, confidence, citations, search_mode}
```

---

## Agents

### 1. Orchestrator (State Machine)

| Property | Value |
|----------|-------|
| **File** | `services/agents/orchestrator.py` |
| **Function** | `run_pipeline()` |
| **Purpose** | Pure Python state machine. Classifies intent, selects pipeline, routes through specialized agents in sequence. No LangChain/LangGraph. |
| **Input** | question, user_id, access_level, department, search_mode |
| **Output** | QueryContext with answer, verdict, confidence, citations, intent, approval_result, conflicts, risk_result |
| **States** | IDLE → CLASSIFIED → RETRIEVED → REASONED → VERIFIED → DONE (or ABSTAINED) |
| **Intents** | GENERAL, APPROVAL, CONFLICT, COMPLIANCE, PROCEDURE |
| **Dependencies** | classify_intent, agent_retrieve, agent_reason, agent_verify, agent_approval, agent_conflict_check, agent_risk_compliance |

### 2. Retriever

| Property | Value |
|----------|-------|
| **File** | `services/api/search.py:93` |
| **Function** | `search_policy()` |
| **Purpose** | Three-stage retrieval pipeline: (1) vector search via pgvector + BM25 keyword search, (2) Reciprocal Rank Fusion to merge results, (3) CrossEncoder reranking to select top 3. Supports three modes: "vector", "bm25", "hybrid". |
| **Input** | query string, access_level, search_mode |
| **Output** | List of 3 chunk dicts with id, text, clause_ref, title, score |
| **Sub-components** | `vector_search()` (line 42), `reciprocal_rank_fusion()` (line 15), BM25 via `bm25_index.search()` |
| **Models** | BAAI/bge-large-en-v1.5 (embeddings), BAAI/bge-reranker-base (reranking), rank_bm25 (BM25) |

### 3. Reasoner

| Property | Value |
|----------|-------|
| **File** | `services/agents/reasoner.py:17` |
| **Function** | `generate_grounded_answer()` |
| **Purpose** | Sends retrieved chunks + user question to DeepSeek-R1 LLM with strict system prompt. Forces the LLM to cite every claim with `[Doc: X, Clause: Y]` or say "Insufficient policy basis". Temperature 0.0 for deterministic output. |
| **Input** | query string, context_chunks (list of 3 chunk dicts) |
| **Output** | Answer string with bracketed citations |
| **Model** | DeepSeek-R1-Distill-Llama-8B (Q4_K_M quantized) via llama.cpp on port 8080 |
| **System Prompt** | 5 strict rules: use only context, cite every claim, handle conflicts, no apologies |

### 4. Verifier

| Property | Value |
|----------|-------|
| **File** | `services/agents/verifier.py:3` |
| **Function** | `verify_citations()` |
| **Purpose** | Guardrail agent. Extracts all `[Doc: X, Clause: Y]` patterns from the LLM answer via regex, then checks each one against the actual retrieved chunks. If any citation doesn't match a real chunk, returns invalid. |
| **Input** | answer string, context_chunks (list of chunk dicts) |
| **Output** | `(is_valid: bool, message: str)` |
| **Failure mode** | If citation doesn't match → answer is marked "abstained", confidence drops to 0 |

### 5. Approval-Matrix Agent

| Property | Value |
|----------|-------|
| **File** | `services/agents/approval_agent.py` |
| **Function** | `resolve_approval()` |
| **Purpose** | Queries Neo4j knowledge graph to resolve approval authority. Finds which roles can approve a given process, extracts monetary amounts from questions, checks if user's role has sufficient authority. |
| **Input** | question string, chunks list, user_role |
| **Output** | `{process, matching_roles, user_can_approve, amount, answer_text}` |
| **Neo4j Query** | `MATCH (r:Role)-[:CAN_APPROVE]->(p:Process) WHERE toLower(p.name) CONTAINS toLower($process) RETURN r.name, p.name` |

### 6. Conflict Detector Agent

| Property | Value |
|----------|-------|
| **File** | `services/agents/conflict_agent.py` |
| **Function** | `detect_conflicts_in_chunks()` |
| **Purpose** | Cross-checks retrieved clauses for contradictions. Uses Neo4j CONFLICTS_WITH relationships when available, detects version mismatches (same clause_ref from different chunks), and falls back to LLM-based pairwise conflict detection (top 5 pairs). |
| **Input** | chunks list |
| **Output** | List of `{clause_a, clause_b, reason, source}` |
| **Sources** | Neo4j (pre-computed), version check, LLM (Phi-4-mini) |

### 7. Risk & Compliance Agent

| Property | Value |
|----------|-------|
| **File** | `services/agents/risk_agent.py` |
| **Function** | `assess_risk()` |
| **Purpose** | Classifies query outcomes as CLEAR/CONDITIONAL/VIOLATION. Maps clauses to regulations via Neo4j MAPS_TO relationships, checks obligation levels (mandatory/optional), and generates risk-level recommendations. |
| **Input** | question string, chunks list |
| **Output** | `{verdict, regulations, obligations, risk_level, recommendations}` |
| **Neo4j Queries** | `MATCH (p:Policy)-[:MAPS_TO]->(reg:Regulation)`, `MATCH (c:Clause) RETURN c.obligation` |

### 8. Ingestion Agent

| Property | Value |
|----------|-------|
| **File** | `services/ingestion/ingest.py:100` |
| **Function** | `ingest_file()` |
| **Purpose** | Orchestrates the full ingestion pipeline for a single document: parse structure → detect clauses via LLM → chunk with overlap → generate embeddings → extract entities → store in PostgreSQL. |
| **Input** | file path (DOCX or PDF) |
| **Output** | Document record + N chunk records + entity records in PostgreSQL |
| **Sub-components** | `parse_document_structure()` (doc_parser.py), `chunk_document_clauses()` (structure_chunker.py), `detect_clauses()` (clause_detector.py), `SentenceTransformer.encode()` (embeddings), `extract_entities()` (regex) |

---

## Data Flow: Query Pipeline

```
1. POST /query {"question": "Who can approve a purchase over $10,000?", "search_mode": "hybrid"}
   │
2. Authentication (JWT verify → get_current_active_user)
   │
3. Log query to PostgreSQL (queries table)
   │
4. State Machine Orchestrator: run_pipeline()
   │
   ├── classify_intent() → APPROVAL (90% confidence)
   │
   ├── agent_retrieve()
   │   ├── vector_search()          → pgvector cosine distance → top 20 candidates
   │   ├── bm25_index.search()      → BM25Okapi keyword match  → top 20 candidates
   │   ├── reciprocal_rank_fusion() → merge + deduplicate      → top 20 merged
   │   └── reranker.predict()       → CrossEncoder scoring     → top 3 final
   │
   ├── agent_approval()  (APPROVAL intent only)
   │   └── Neo4j Cypher: MATCH (r:Role)-[:CAN_APPROVE]->(p:Process) → approval authority
   │
   ├── agent_reason()
   │   └── POST to llama.cpp:8080 → DeepSeek-R1 with strict prompt → answer with citations
   │
   └── agent_verify()
       ├── verify_citations() → regex extract [Doc: X, Clause: Y] → validate
       └── compute_confidence() → sigmoid×0.40 + coverage×0.35 + availability×0.25
   │
5. Log answer + citations to PostgreSQL (answers + citations tables)
   │
6. Log audit action (audit_logs table)
   │
7. Return AnswerResponse {answer, verdict, confidence, citations, search_mode}
```

---

## Configuration

### Models

| Model | Purpose | Path | Port |
|-------|---------|------|------|
| BAAI/bge-large-en-v1.5 | Embedding (1024 dims) | Auto-downloaded | — |
| BAAI/bge-reranker-base | CrossEncoder reranking | Auto-downloaded | — |
| DeepSeek-R1-Distill-Llama-8B | Q&A reasoning | `/home/mujtaba/models/DeepSeek-R1-Distill-Llama-8B-Q4_K_M.gguf` | 8080 |
| Phi-4-mini-instruct | Clause detection (ingestion) | `/home/mujtaba/models/Phi-4-mini-instruct-Q4_K_M.gguf` | 8080 |

### Search Parameters

| Parameter | Value | Location |
|-----------|-------|----------|
| `top_k_retrieval` | 20 | search.py:96 |
| `top_k_final` | 3 | search.py:97 |
| RRF k | 60 | search.py:127 |
| `search_mode` default | "hybrid" | main.py:200 |
| Reranker weight | 40% | main.py:57 |
| Citation coverage weight | 35% | main.py:58 |
| Chunk availability weight | 25% | main.py:59 |

### Chunking Parameters

| Parameter | Value | Location |
|-----------|-------|----------|
| `MAX_CLAUSE_TOKENS` | 50 | ingest.py:21 |
| `OVERLAP_TOKENS` | 3 | ingest.py:22 |
| Embedding dimensions | 1024 | ingest.py:157 |

### Authentication

| Parameter | Value | Location |
|-----------|-------|----------|
| Algorithm | HS256 | auth.py |
| Token expiry | 30 minutes | auth.py |
| Password hashing | argon2 (hashward) | auth.py |

### Mock Users

| Username | Password | Access Level | Department |
|----------|----------|-------------|------------|
| admin | password123 | 3 | IT |
| manager | password123 | 2 | Finance |
| employee | password123 | 1 | HR |

---

## Commands

### Start PostgreSQL
```bash
export PATH="/home/mujtaba/postgresql/bin:$PATH"
pg_ctl -D /home/mujtaba/pgdata -l /home/mujtaba/pgdata/logfile start
```

### Start LLM Server (Phi-4-mini for ingestion)
```bash
/home/mujtaba/new_folder/codex/infra/server_phi.sh
```

### Start LLM Server (DeepSeek-R1 for queries)
```bash
/home/mujtaba/new_folder/codex/infra/server.sh
```

### Start FastAPI Server
```bash
source /home/mujtaba/new_folder/fastmcp/venv/bin/activate
PYTHONPATH=/home/mujtaba/new_folder python3 -m uvicorn services.api.main:app --host 0.0.0.0 --port 8000
```

### Run Ingestion (requires PostgreSQL + Phi-4-mini running)
```bash
source /home/mujtaba/new_folder/fastmcp/venv/bin/activate
PYTHONPATH=/home/mujtaba/new_folder/codex python3 /home/mujtaba/new_folder/codex/services/ingestion/ingest.py
```

### Run Tests
```bash
source /home/mujtaba/new_folder/fastmcp/venv/bin/activate
PYTHONPATH=/home/mujtaba/new_folder/codex python3 /home/mujtaba/new_folder/codex/tests/test_chunking.py
PYTHONPATH=/home/mujtaba/new_folder/codex python3 /home/mujtaba/new_folder/codex/tests/test_reasoning.py
```

### Run CLI Chat
```bash
source /home/mujtaba/new_folder/fastmcp/venv/bin/activate
PYTHONPATH=/home/mujtaba/new_folder python3 -m services.chat.cli
```

### Refresh BM25 Index (after new ingestion)
```bash
curl -X POST http://localhost:8000/admin/refresh-index \
  -H "Authorization: Bearer <admin_token>"
```

---

## Guardrails: Cite-or-Abstain Chain

```
Layer 1 — RBAC (main.py:117)
  JWT carries access_level → search_policy filters chunks by level
  User physically cannot retrieve restricted data

Layer 2 — System Prompt (reasoner.py:6-14)
  LLM is instructed: "Use ONLY provided context"
  Temperature 0.0 for deterministic output
  Forced to say "Insufficient policy basis" when context lacks answer

Layer 3 — Citation Verification (verifier.py:3-17)
  Regex extracts [Doc: X, Clause: Y] from answer
  Cross-references against actual retrieved chunk metadata
  Mismatch → verdict="abstained", confidence drops

Layer 4 — Confidence Threshold
  Multi-signal scoring: reranker + citation coverage + chunk availability
  Low confidence → abstained verdict even if citations pass
```

### Failure Modes

| Scenario | Guardrail | Result |
|----------|-----------|--------|
| LLM hallucinates a citation | Verifier regex check | abstained |
| LLM uses outside knowledge | System prompt forces "Insufficient" | abstained |
| User queries above their level | RBAC in search_policy | No chunks returned → abstained |
| Context genuinely has no answer | Reasoner says "Insufficient" | abstained |
| Weak retrieval scores | Confidence scoring | Low % → abstained |

---

## Confidence Scoring

### Formula
```
confidence = (
    sigmoid(top_reranker_score) × 0.40
  + citation_coverage           × 0.35
  + chunk_availability          × 0.25
) × 100  → percentage (0.0 - 100.0)
```

### Components

| Signal | Weight | How It Works |
|--------|--------|-------------|
| Reranker score | 40% | CrossEncoder logit → sigmoid → 0.0-1.0 |
| Citation coverage | 35% | valid_citations / total_citations |
| Chunk availability | 25% | len(chunks) / 3.0 (max 3 chunks) |

### Example Scores

| Scenario | Confidence |
|----------|-----------|
| Strong match, all cited | ~99% |
| Decent match, partial citations | ~78% |
| Weak match, no citations | ~10% |
| Correct abstention ("Insufficient") | ~60% |
| No chunks found | 0% |

---

## Database Schema (8 Tables)

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `documents` | Policy document metadata | id, title, type, status, source_uri |
| `chunks` | Clause-level text units | id, document_id, clause_ref, text, embedding (vector 1024) |
| `entities` | Extracted thresholds, approvals, deadlines | id, document_id, type, name, attrs |
| `queries` | User questions | id, user_id, question, intent |
| `answers` | LLM responses | id, query_id, answer, verdict, confidence, latency_ms |
| `citations` | Links answers to source chunks | id, answer_id, chunk_id, clause_ref, score |
| `feedback` | User ratings on answers | id, answer_id, rating (1-5), note |
| `audit_logs` | Action audit trail | id, actor, action, payload, ts |

---

## Knowledge Graph (Neo4j)

| Node | Count | Properties |
|------|-------|------------|
| Policy | 24 | id, title, version, status, effective_date, process_name |
| Clause | 1,842 | id, clause_ref, section_path, text_ref, version, obligation |
| Process | 268 | name, source_policy |
| Role | 60 | name |
| Department | 17 | name |
| Regulation | 17 | name |

| Relationship | Count | Meaning |
|-------------|-------|---------|
| PART_OF | 1,842 | Clause belongs to Policy |
| GOVERNS | 2,237 | Clause governs Process |
| CAN_APPROVE | 162 | Role can approve Process |
| MAPS_TO | 39 | Policy maps to Regulation |
| BELONGS_TO | 23 | Role belongs to Department |
| SUPERSEDES | 0 | Policy supersedes older version (single-version corpus) |
| CONFLICTS_WITH | 0 | Detected contradictions (skipped — too expensive) |

### Key Cypher Queries

```cypher
-- Approval resolution
MATCH (r:Role {name:$role})-[:CAN_APPROVE]->(p:Process)
WHERE toLower(p.name) CONTAINS toLower($process)
RETURN r.name, p.name

-- Regulation mapping
MATCH (p:Policy {id:$doc_id})-[:MAPS_TO]->(reg:Regulation)
RETURN reg.name

-- Obligation check
MATCH (c:Clause {id:$cid})
RETURN c.obligation
```

---

## Known Gaps

| Gap | Priority | Impact |
|-----|----------|--------|
| No `.gitignore` | High | __pycache__ and logs tracked in git |
| `main.py` too large (~694 lines) | High | Should be split into FastAPI routers |
| No POST /ingest endpoint | Medium | Can't upload documents via API |
| Thin test coverage | Medium | No tests for search, BM25, auth, doc_parser |
| No OCR support | Low | Scanned PDFs produce no text |
| No web UI | Low | CLI-only interface |
| No retry/error handling on LLM calls | Medium | LLM failures crash individual agents |
| No rate limiting | Low | API vulnerable to abuse |
| Mock user store (not Postgres) | Low | Users reset on restart |
| No CONFLICTS_WITH in Neo4j | Low | Skipped (too expensive for 1,842 clauses) |
| Log files at root | Low | Should be in logs/ |
| Empty directories (eval/, web/) | Low | Placeholder clutter |
| 23 Zone.Identifier files | Low | Windows metadata, useless on Linux |
| `init.sql/` empty dir at root | Low | Actual file is at infra/db/init.sql |
