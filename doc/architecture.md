# System Architecture

## Overview
Codex is an Enterprise Policy Intelligence Engine designed to turn a corpus of policy documents into a grounded, cited Q&A system. It follows a "Retrieve-Reason-Verify" pipeline.

## 1. The Data Pipeline (Ingestion)
The ingestion process transforms raw files into a searchable vector space:
`Documents (PDF/DOCX)` $\rightarrow$ `Structure-aware Chunking` $\rightarrow$ `Clause Detection (LLM)` $\rightarrow$ `Embedding (bge-large-en-v1.5)` $\rightarrow$ `pgvector Storage`.

## 2. The Execution Pipeline (Request Flow)
When a query is processed, it moves through the following layers:

### Layer 1: The Gateway (API)
- Handled by **FastAPI**.
- Manages authentication, input validation, and output formatting.

### Layer 2: The Retrieval Engine (Search)
- **Hybrid Search:** Combines semantic vector search with keyword matching.
- **Reranking:** Uses a `BGE-Reranker` to narrow down the top 20 candidates to the most relevant top 3.
- **RBAC:** Filters results based on the user's access level.

### Layer 3: The Reasoning Brain (LLM)
- **Model:** DeepSeek-R1-Distill-Llama-8B (deployed via `llama.cpp`).
- **Process:** The LLM receives the reranked chunks and the query. It is forced to use only the provided context to generate an answer.

### Layer 4: The Trust Guardrail (Verifier)
- **Grounding Check:** The verifier scans the answer for citations.
- **Cross-Reference:** It ensures every cited clause actually exists in the retrieved chunks.
- **Final Verdict:** If any citation is fake, the system abstains.

## 3. Technology Stack
- **Reasoning:** `llama.cpp` / Phi-4-mini-instruct (clause detection), DeepSeek-R1-Distill-Llama-8B (Q&A)
- **Search:** PostgreSQL + `pgvector`
- **API:** FastAPI + Uvicorn
- **Embeddings:** `BAAI/bge-large-en-v1.5` (1024 dimensions)
- **Reranker:** `BAAI/bge-reranker-base`
- **Auth:** PyJWT + Hashward
