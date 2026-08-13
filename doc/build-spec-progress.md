# Codex — Build Progress

**Reference spec:** `Codex Engineering Build Specification`

---

## Week 1: Ingestion & Index Foundation — 75%

| Task | Status |
|------|--------|
| Repo structure | ✅ |
| PostgreSQL + pgvector | ✅ |
| Auth skeleton (JWT + argon2) | ✅ |
| Document parsing (DOCX/PDF) | ✅ |
| Clause-level chunking | ✅ |
| Embeddings (bge-large-en-v1.5) | ✅ |
| Store with metadata | ✅ |
| Seed corpus ingested (25 docs, 1,842 chunks) | ✅ |
| Searchable index (pgvector + BM25 + RRF + rerank) | ✅ |
| OCR for scanned pages | ❌ |
| Ingest via API | ❌ |
| Object storage (MinIO) | ❌ Removed |

---

## Week 2: RAG Q&A, Citations, Guardrails — 73%

| Task | Status |
|------|--------|
| Orchestrator (state machine) | ✅ |
| Grounded Q&A agent (DeepSeek-R1) | ✅ |
| Hybrid retrieval (vector + BM25) | ✅ |
| CrossEncoder reranking | ✅ |
| Citation resolution to exact clause | ✅ |
| Guardrails: grounding check | ✅ |
| Guardrails: cite-or-abstain | ✅ |
| Guardrails: confidence threshold | ✅ |
| CLI chat | ✅ |
| Web chat UI | ❌ |
| Citation viewer | ❌ |

---

## Week 3: Knowledge Graph & Specialized Agents — 100%

| Task | Status |
|------|--------|
| Neo4j setup (2,228 nodes, 4,303 rels) | ✅ |
| Extract entities/rules into Neo4j | ✅ |
| Approval-Matrix agent | ✅ |
| Conflict-Detector agent | ✅ |
| Risk & Compliance agent | ✅ |
| Multi-agent orchestration (state machine) | ✅ |
| Tool calling system (CircuitBreaker, ToolRegistry) | ✅ |
| Approval + conflict queries working | ✅ |

---

## Week 4: Eval, Dashboard, Integration — 0%

| Task | Status |
|------|--------|
| Evaluation harness + golden Q&A set | ❌ |
| Measure faithfulness, precision, recall | ❌ |
| Tune based on metrics | ❌ |
| Compliance/Admin dashboard | ❌ |
| Teams or Slack integration | ❌ |
| Persistent memory | ❌ |
| Harden RBAC | ❌ |
| Deploy to staging | ❌ |

---


