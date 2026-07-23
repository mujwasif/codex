# Codex Search Engine Architecture

`services/api/search.py` is the retrieval core of the Codex system. It is responsible for taking a natural language user query and finding the most relevant policy clauses from the database.

## 1. Overview

The search engine employs a **two-stage retrieval pipeline**:
1.  **Candidate Retrieval (Broad):** Uses Vector search and BM25 to find 20 potential candidates.
2.  **Reranking (Precise):** Uses a CrossEncoder to narrow those 20 candidates down to the top 3 most relevant chunks.

---

## 2. Core Components

### Models (Loaded at Startup)
- **Retriever (`BAAI/bge-large-en-v1.5`):** A `SentenceTransformer` that converts text into a 1024-dimensional vector.
- **Reranker (`BAAI/bge-reranker-base`):** A `CrossEncoder` that calculates a relevance score for a query-chunk pair.

These models are loaded at the module level to avoid the high latency of reloading them per request.

---

## 3. Detailed Process Flow

### Stage 1: Retrieval
Based on the `search_mode` parameter, the system chooses a retrieval strategy:

#### A. Vector Search (`vector_search`)
1.  **Encoding:** The user's query is turned into a vector using `retriever.encode()`.
2.  **Cosine Similarity:** Executes a PostgreSQL query using the `pgvector` operator `<=>` (cosine distance).
3.  **Filtering:** Filters for `status = 'active'` documents.
4.  **Result:** Returns the top 20 most semantically similar chunks.

#### B. BM25 Search (`bm25_index.search`)
1.  **Tokenization:** The query is split into lowercase words.
2.  **Lexical Scoring:** Uses the in-memory `BM25Okapi` index to calculate scores based on term frequency and rarity (IDF).
3.  **Result:** Returns the top 20 chunks containing the exact keywords.

#### C. Hybrid Search (RRF Fusion)
If `search_mode="hybrid"`, both Vector and BM25 results are combined using **Reciprocal Rank Fusion (RRF)**.

**The Formula:**
`score(d) = Σ 1 / (k + rank_i(d))` where `k=60`.

This ensures that a chunk ranking highly in *both* methods gets a massive boost, while those only found by one method are ranked lower.

---

### Stage 2: Reranking
Once 20 candidates are retrieved, they are passed to the CrossEncoder for precision.

1.  **Pairing:** The query and chunk text are paired together: `[[query, chunk1], [query, chunk2], ...]`.
2.  **Prediction:** `reranker.predict(pairs)` outputs a relevance score for each pair.
3.  **Sorting:** Candidates are re-sorted by this new score.
4.  **Truncation:** Only the top **3** results are returned to the orchestrator.

---

## 4. Data Flow Diagram

```text
User Query ("What is MFA?")
      │
      ▼
┌──────────────────────────────────────────────────────────────┐
│ search_policy()                                             │
│                                                              │
│  ┌──────────────────────────────┐    ┌──────────────────────┐  │
│  │   Vector Search (pgvector)  │    │   BM25 Search (RAM)  │  │
│  │  (Semantic/Concept Match)    │    │  (Exact Keyword Match)│  │
│  └──────────────┬───────────────┘    └──────────────┬───────┘  │
│                  │                                  │         │
│                  └──────────────────┬───────────────┘         │
│                                     ▼                               │
│                       Reciprocal Rank Fusion (RRF)                  │
│                       (Merges 20+20 → Top 20)                      │
│                                     │                               │
│                                     ▼                               │
│                       CrossEncoder Reranking                       │
│                       (Deep analysis of 20 pairs)                   │
│                                     │                               │
│                                     ▼                               │
│                       Final Top 3 Chunks                           │
└─────────────────────────────────────┬────────────────────────┘
                                      │
                                      ▼
                          Sent to Reasoner LLM
```

---

## 5. Design Decisions

| Decision | Why |
|----------|-----|
| **Two-Stage Pipeline** | Vector/BM25 are fast but imprecise. CrossEncoders are precise but slow. This hybrid approach gives the best of both worlds. |
| **Symmetric Hybrid (RRF)** | Prevents one search method (e.g., Vector) from totally overriding the other. |
| **Llama-server Integration** | Uses `CAST(:embedding AS vector)` in SQL to bridge Python's list representation and PostgreSQL's vector type. |
| ** top_k_final = 3** | Prevents the LLM from being overwhelmed by too much context ("lost in the middle" phenomenon). |
| **Case-Insensitive BM25** | Ensures "Password" and "password" match equally. |
