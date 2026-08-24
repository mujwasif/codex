# Codex RAG Improvements Tracker

> **Status:** Planning phase — edit this file to track priorities, owners, and progress.

--- 

## 🎯 Strategy Overview

| # | Strategy                     | Status      | Priority | Effort | Notes |
|---|------------------------------|-------------|----------|--------|-------|
| 1 | Reranking                    | ✅ Done     | —        | —      | `bge-reranker-base` CrossEncoder |
| 2 | Agentic RAG / Hybrid Search  | ✅ Done     | —        | —      | Orchestrator + RRF + BM25      |
| 3 | Knowledge Graphs             | ✅ Done     | —        | —      | Neo4j for approval/risk        |
| 4 | Contextual Retrieval         | ❌ Missing  | High     | Medium | Prepend doc summary to chunks |
| 5 | Query Expansion              | ❌ Missing  | High     | Low    | LLM-generated query variations |
| 6 | Multi-Query RAG              | ❌ Missing  | Medium   | Medium | Parallel retrieval paths       |
| 7 | Context-Aware Chunking       | ✅ Done     | —        | —      | Clause-level chunks (200/20)   |
| 8 | Late Chunking                | ❌ Missing  | Medium   | High   | Whole-doc embed → split        |
| 9 | Hierarchical RAG             | ✅ Done     | —        | —      | RBAC + title-aware rerank      |
| 10| Self-Reflective RAG          | 🟡 Partial  | High     | Medium | Iterative retrieve→reflect→retrieve |
| 11| Fine-tuned Embeddings        | ❌ Missing  | High     | High   | Domain-specific BGE on policy pairs |

---
## 📋 Implementation Details (Planned)

### Strategy 4 - Contextual Retrieval
**Goal:** Embed each chunk with document-level context.  
**Approach:** Prepend 1-2 sentence document summary to chunks during ingestion.  
**Files:** `services/ingestion/ingest.py`, `services/ingestion/structure_chunker.py`  
**Dependency:** LLM call for summary generation during ingestion  

### Strategy 5 - Query Expansion  
**Goal:** Generate 3-5 query variations to improve recall.  
**Approach:** Add `QueryExpander` agent in orchestrator before retrieval.  
**Files:** `services/agents/orchestrator.py`, `services/api/search.py`  

### Strategy 10 - Self-Reflective RAG
**Goal:** Allow LLM to request re-retrieval if context is insufficient.  
**Approach:** Add reflection loop after verification state; LLM generates reflection + new query.  
**Files:** `services/agents/orchestrator.py`, `services/agents/reasoner.py`

---
## 📅 Prioritized Execution Order
1. **Query Expansion** - Low effort, high impact  
2. **Contextual Retrieval** - Improves chunk quality at source  
3. **Self-Reflective RAG** - Uses existing signals to reduce abstentions  
4. **Fine-tuned Embeddings** - Long-term precision improvement  
5. **Late Chunking** - High effort, evaluate after #2  
6. **Multi-Query RAG** - Depends on query expansion

---
## ✅ Definition of Done
- [ ] Implementation complete + unit tests  
- [ ] Integration test passes (existing test suite green)  
- [ ] Offline eval metric improved (recall@k, nDCG, abstention rate)  
- [ ] Latency within budget (p95 < 2s end-to-end)  
- [ ] Deployed to staging + smoke test  
- [ ] Documentation updated

---

## 🗒️ Notes
- **Created:** 2026-08-21  
- **Action:** Copy this content to `/home/mujtaba/new_folder/codex/doc/improvements.md` when write access is restored  
- **Focus:** High-priority, low-risk improvements first