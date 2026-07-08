# Codex Code Learning Workflow

Recommended order to understand the codebase, from foundation to full system.

---

## Phase 1: Foundation (Start Here)

| # | File | What to Understand | Why First |
|---|------|--------------------|-----------|
| 1 | `infra/db/init.sql` | Database schema (8 tables) | Everything stores data here |
| 2 | `packages/shared/db.py` | SQLAlchemy engine + session | All DB access goes through here |
| 3 | `packages/shared/models.py` | ORM models (Document, Chunk, Query, etc.) | Maps Python objects to tables |
| 4 | `packages/shared/schemas.py` | Pydantic request/response schemas | Defines API contract |

**Key insight:** The database has 3 core entities: `documents` → `chunks` → `queries/answers`. Everything revolves around these.

---

## Phase 2: Ingestion Pipeline (How Data Gets In)

| # | File | What to Understand | Call Chain |
|---|------|--------------------|------------|
| 5 | `packages/shared/doc_parser.py` | DOCX/PDF → structured sections | `parse_document_structure()` → `parse_docx_structure()` |
| 6 | `services/ingestion/clause_detector.py` | Section → individual clauses | `detect_clauses()` → `detect_clauses_llm()` (Phi-4-mini) |
| 7 | `services/ingestion/structure_chunker.py` | Clauses → chunked units with overlap | `chunk_document_clauses()` |
| 8 | `services/ingestion/ingest.py` | Orchestrator: ties it all together | `main()` → `ingest_file()` |

**Flow to trace:**
```
archive/00003082 - Information Security Policy.docx
  → parse_docx_structure()     → 51 sections
    → chunk_document_clauses() → 36 clause-level chunks (10-50 tokens each)
      → model.encode()         → 1024-dim vector per chunk
        → session.add(Chunk)   → PostgreSQL
```

---

## Phase 3: Search & Retrieval (How Queries Work)

| # | File | What to Understand | Call Chain |
|---|------|--------------------|------------|
| 9 | `services/ingestion/bm25_index.py` | BM25 keyword index | `BM25Index()` → `search()` |
| 10 | `services/api/search.py` | Hybrid search + reranking | `search_policy()` → `vector_search()` + `bm25_index.search()` → `reciprocal_rank_fusion()` → rerank |

**Flow to trace:**
```
User: "How often should passwords be changed?"
  → vector_search()         → pgvector cosine → top 20
  → bm25_index.search()     → BM25 keyword   → top 20
  → reciprocal_rank_fusion() → merge → top 20
  → reranker.predict()      → CrossEncoder   → top 3
```

---

## Phase 4: LLM Reasoning (How Answers Are Generated)

| # | File | What to Understand | Call Chain |
|---|------|--------------------|------------|
| 11 | `services/agents/reasoner.py` | LLM answer generation | `generate_grounded_answer()` |
| 12 | `services/agents/verifier.py` | Citation verification | `verify_citations()` |

**Flow to trace:**
```
Top 3 chunks → format as "[Doc: X, Clause: Y]: text"
  → DeepSeek-R1 prompt → answer with [Doc: X, Clause: Y] citations
    → verifier regex → check all citations match retrieved chunks
      → verdict: "clear" or "abstained"
```

---

## Phase 5: API Layer (How Everything Connects)

| # | File | What to Understand | Key Endpoint |
|---|------|--------------------|--------------|
| 13 | `packages/shared/auth.py` | JWT + argon2 password hashing | Used by login/register |
| 14 | `services/api/main.py` | FastAPI app + all endpoints | `POST /query` (line 135) |

**Trace the `/query` endpoint:**
```
POST /query {"question": "...", "search_mode": "hybrid"}
  → Auth check (JWT verify)
  → Log query to DB
  → search_policy() → top 3 chunks
  → generate_grounded_answer() → LLM answer
  → verify_citations() → check grounding
  → Determine verdict
  → Log answer + citations
  → Return AnswerResponse
```

---

## Phase 6: Tests (Verify Understanding)

| # | File | What to Understand |
|---|------|--------------------|
| 15 | `tests/test_chunking.py` | How chunking works in practice |
| 16 | `tests/test_reasoning.py` | End-to-end integration test |

---

## Visual Summary: The Complete Data Flow

```
                    INGESTION (offline)
                    ===================
archive/*.docx
  → doc_parser.py          (parse structure)
  → clause_detector.py     (split into clauses via LLM)
  → structure_chunker.py   (chunk with overlap)
  → ingest.py              (embed + store)
  → PostgreSQL             (1,842 chunks with vectors)


                    QUERY (online)
                    ==============
User: "How often should passwords change?"
  → main.py /query         (auth + orchestrate)
  → search.py              (vector + BM25 + RRF + rerank)
  → reasoner.py            (DeepSeek-R1 generates answer)
  → verifier.py            (check citations match)
  → Response: "Every 90 days [Doc: Password Policy, Clause: 5.5 > Rule 1]"
```

---

## Suggested Reading Order (if you have 1 hour)

1. **5 min:** `init.sql` — understand the 8 tables
2. **5 min:** `models.py` — see how tables map to Python
3. **10 min:** `ingest.py` — trace one document through the pipeline
4. **10 min:** `search.py` — understand hybrid retrieval + RRF
5. **5 min:** `reasoner.py` — see how LLM generates answers
6. **5 min:** `verifier.py` — understand the guardrail
7. **10 min:** `main.py` `/query` endpoint — see how everything connects
8. **10 min:** Run the tests to see it all work

---

## Module Dependency Map

```
                    +-----------+
                    | db.py     |  (SQLAlchemy engine, session, init_db)
                    +-----+-----+
                          |
          +---------------+---------------+
          |               |               |
    +-----v-----+  +-----v-----+  +-----v------+
    | models.py  |  | auth.py   |  | doc_parser |
    | (8 ORM    |  | (JWT +    |  | (DOCX/PDF  |
    |  classes)  |  | argon2)   |  |  parsing)  |
    +-----+------+  +-----+----+  +-----+------+
          |               |              |
          |               |         +----v-----------+
          |               |         | clause_detector |
          |               |         | (LLM + regex   |
          |               |         |  clause split)  |
          |               |         +----+-----------+
          |               |              |
          |         +-----+------+  +----v--------------+
          |         | schemas.py |  | structure_chunker |
          |         | (Pydantic) |  | (clause chunking  |
          |         +-----+------+  |  with overlap)    |
          |               |         +----+--------------+
          |               |              |
    +-----v------+  +----v-----+   +-----v-----+
    | search.py  |  | reasoner |   | ingest.py  | <--- ENTRY POINT A
    | (hybrid    |  | (LLM     |   | (orchestrator)
    |  retrieval |  |  answer) |   +------------+
    |  + rerank) |  +----+-----+
    +-----+------+       |
          |         +----v-----+
          |         | verifier |
          |         | (citation|
          |         |  check)  |
          |         +----+-----+
          |              |
    +-----v--------------v--+
    | main.py               | <--- ENTRY POINT B
    | (FastAPI gateway)     |
    +-----------------------+
```

---

## Configuration Reference

### Models

| Parameter | Value | File |
|-----------|-------|------|
| Retriever | `BAAI/bge-large-en-v1.5` (1024 dims) | `search.py`, `ingest.py` |
| Reranker | `BAAI/bge-reranker-base` | `search.py` |
| Reasoning LLM | `deepseek-r1-distill-llama-8b` | `reasoner.py` |
| Clause detection | `Phi-4-mini-instruct-Q4_K_M` | `clause_detector.py` |

### Search Parameters

| Parameter | Value | File |
|-----------|-------|------|
| `top_k_retrieval` | 20 | `search.py` |
| `top_k_final` | 3 | `search.py` |
| RRF k | 60 | `search.py` |
| `search_mode` default | "hybrid" | `main.py` |

### Chunking Parameters

| Parameter | Value | File |
|-----------|-------|------|
| `MAX_CLAUSE_TOKENS` | 50 | `ingest.py` |
| `OVERLAP_TOKENS` | 3 | `ingest.py` |

### Authentication

| Parameter | Value | File |
|-----------|-------|------|
| Algorithm | HS256 | `auth.py` |
| Token expiry | 30 min | `auth.py` |
| Password hashing | argon2 | `auth.py` |

### Mock Users

| Username | Password | Access Level |
|----------|----------|-------------|
| admin | password123 | 3 (Admin) |
| manager | password123 | 2 (Manager) |
| employee | password123 | 1 (Standard) |

---

## Guardrail Chain (Cite or Abstain)

```
Layer 1 - RBAC (main.py)
  JWT carries access_level → search_policy filters chunks by level
  User physically cannot retrieve restricted data

Layer 2 - System Prompt (reasoner.py)
  LLM is instructed: "Use ONLY provided context"
  Temperature 0.0 for deterministic output
  Forced to say "Insufficient policy basis" when context lacks answer

Layer 3 - Citation Verification (verifier.py)
  Regex extracts [Doc: X, Clause: Y] from answer
  Cross-references against actual retrieved chunk metadata
  Mismatch → system returns verdict="abstained", confidence=0.0
```

**Failure modes:**

| Scenario | Guardrail | Result |
|----------|-----------|--------|
| LLM hallucinates a citation | Verifier regex check | `abstained` |
| LLM uses outside knowledge | System prompt forces "Insufficient" | `abstained` |
| User queries above their level | RBAC in search_policy | No chunks returned → `abstained` |
| Context genuinely has no answer | Reasoner says "Insufficient" | `abstained` |

---

## API Endpoints (18 total)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/register` | POST | Create new user |
| `/login` | POST | Authenticate, return JWT |
| `/query` | POST | Main query pipeline |
| `/query/history` | GET | User's past queries |
| `/feedback` | POST | Rate an answer (1-5) |
| `/feedback/{answer_id}` | GET | Get feedback for answer |
| `/documents` | GET | List all ingested documents |
| `/documents/{id}` | GET | Document detail with chunks + entities |
| `/chunks` | GET | Browse/search chunks |
| `/chunks/{id}` | GET | Single chunk detail |
| `/entities` | GET | Browse extracted entities |
| `/health` | GET | System health check |
| `/audit` | GET | Audit logs (admin only) |
| `/admin/refresh-index` | POST | Rebuild BM25 index (admin only) |

---

## External Services

| Service | Purpose | Port | Launch Script |
|---------|---------|------|---------------|
| PostgreSQL + pgvector | Vector storage + relational DB | 5432 | `infra/setup_postgresql.sh` |
| llama.cpp (DeepSeek-R1) | Q&A reasoning | 8080 | `infra/server.sh` |
| llama.cpp (Phi-4-mini) | Clause detection (ingestion) | 8080 | `infra/server_phi.sh` |

**Note:** Both LLMs use port 8080. Run one at a time: Phi-4-mini for ingestion, DeepSeek-R1 for queries.
