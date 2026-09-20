# Summary Tool — Dynamic Batched Implementation

## Overview

The SUMMARY intent provides comprehensive document summaries by fetching ALL chunks from a named policy document and sending them to the LLM in dynamic batches (max 6K tokens per batch).

---

## How It Works

### User Flow

```
User: "Summarize the Backup Policy"
    │
    ▼
classify_intent() → intents=["summary"], doc_phrases=["Backup Policy"]
    │
    ▼
agent_resolve_docs() → resolves "Backup Policy" → doc_id
    │
    ▼
_tool_summary()
    ├── Fetch ALL chunks from resolved document
    ├── Build dynamic batches (max 6K tokens each)
    ├── Single batch? → one LLM call → done
    ├── Multiple batches? → partial summaries + merge
    └── agent_verify() → validate citations
    │
    ▼
Response: structured summary with citations
```

---

## Key Difference vs Other Intents

| | GENERAL | SUMMARY |
|---|---------|---------|
| Retrieval | Vector search → top 5 chunks | **ALL chunks from named document** |
| Document resolution | Optional | **Required** |
| Chunks in context | 5 (best match) | **All chunks** (dynamic batching) |
| Output | Answers specific question | **Structured summary with sections** |
| LLM calls | 1 | 1 (small doc) or 3+ (large doc) |

---

## Dynamic Batching

### Why Dynamic?

Fixed batch sizes (e.g., 25 chunks/batch) are inefficient:
- Small docs (17 chunks) get split unnecessarily
- Large docs (125 chunks) get 5 batches when 2 would suffice

Dynamic batching fills each batch up to a **6K token budget**:
- 500 tokens → system prompt
- 5,500 tokens → available for chunks
- Each chunk ≈ 70 tokens (avg ~343 chars ÷ 4)
- **~64-78 chunks per batch** depending on chunk size

### How `_build_batches()` Works

```python
CHUNK_TOKEN_BUDGET = 5500  # 6K total - 500 for system prompt

def _build_batches(chunks):
    batches = []
    current_batch = []
    current_tokens = 0
    for chunk in chunks:
        chunk_tokens = len(chunk["text"]) // 4  # ~4 chars per token
        if current_tokens + chunk_tokens > CHUNK_TOKEN_BUDGET and current_batch:
            batches.append(current_batch)       # batch full → save it
            current_batch = [chunk]             # start new batch
            current_tokens = chunk_tokens
        else:
            current_batch.append(chunk)          # add to current batch
            current_tokens += chunk_tokens
    if current_batch:
        batches.append(current_batch)
    return batches
```

### Batch Results per Document

| Document | Chunks | ~Tokens | Batches | LLM Calls | Time |
|----------|--------|---------|---------|-----------|------|
| Backup Policy | 17 | ~1,363 | 1 | 2 | ~15s |
| BYOD | 18 | ~1,546 | 1 | 2 | ~15s |
| Password Management | 16 | ~1,350 | 1 | 2 | ~15s |
| Change Management | 57 | ~5,244 | 1 | 2 | ~20s |
| Access Management | 30 | ~2,527 | 1 | 2 | ~15s |
| Incident Response | 70 | ~6,257 | **2** | **3** | ~30s |
| **Business continuity** | **125** | **~10,726** | **2** | **3** | **~40s** |

**24 out of 28 documents fit in 1 batch (no merge needed).**

---

## Single Batch Flow (24 documents)

```
17 chunks → _build_batches() → [batch_1]
    │
    ▼
_summarize_batch(chunks, doc_title, is_partial=False)
    → LLM sees all 17 chunks in one call
    → LLM generates structured summary:
        Purpose, Key Requirements, Scope, Thresholds, Consequences
    → Returns summary text
    │
    ▼
agent_verify() → validates all citations
```

**2 LLM calls total:** 1 summary + 1 verify

---

## Multi-Batch Flow (4 documents)

```
125 chunks → _build_batches() → [batch_1 (64 chunks), batch_2 (61 chunks)]
    │
    ├── Summarize batch_1
    │   _summarize_batch(chunks[0:64], doc_title, is_partial=True)
    │   → LLM generates partial summary (key points only, no intro/conclusion)
    │   → Returns partial_1 text
    │
    ├── Summarize batch_2
    │   _summarize_batch(chunks[64:125], doc_title, is_partial=True)
    │   → LLM generates partial summary
    │   → Returns partial_2 text
    │
    ├── Merge
    │   _merge_summaries([partial_1, partial_2], doc_title, question)
    │   → LLM merges both partials into structured summary
    │   → Removes "Part 1/Part 2" labels
    │   → Removes repetition
    │   → Organizes: Purpose, Requirements, Scope, Thresholds, etc.
    │
    └── agent_verify() → validates all citations
```

**3 LLM calls total:** 2 partials + 1 merge + 1 verify

---

## Functions

### `_build_batches(chunks) → List[List[Dict]]`

Splits chunks into batches that fit within 5,500 tokens each.

- **Input:** List of chunk dicts (each has `text`, `title`, `clause_ref`)
- **Output:** List of batches (each batch is a list of chunks)
- **Logic:** Accumulates chunks by estimated token count (`len(text) // 4`), starts new batch when budget exceeded

### `_summarize_batch(ctx, chunks, doc_title, is_partial=False) → str`

Sends one batch of chunks to the LLM and returns the summary text.

- **`is_partial=False`:** Full structured summary (Purpose, Key Requirements, Scope, Thresholds, Consequences, Related Policies)
- **`is_partial=True`:** Key points only (no intro/conclusion), for batches that will be merged later
- **Model:** AGENT_MODEL (gemma4:31b-cloud)
- **Temperature:** 0.0 (deterministic)
- **Max tokens:** 4096
- **Timeout:** 60s

### `_merge_summaries(ctx, partials, doc_title, question) → None`

Merges multiple partial summaries into one comprehensive summary. Writes result to `ctx.answer`.

- **Input:** List of partial summary strings
- **Prompt:** "Merge into structured sections, remove repetition, keep citations"
- **Fallback:** If merge fails, concatenates partials with `---` separators

### `_tool_summary(ctx) → ToolResult`

Main entry point. Orchestrates the batching flow:

1. Guard: checks `resolved_doc_ids` exists
2. Fetch: `fetch_chunks_by_document()` gets ALL chunks
3. Guard: checks chunks exist
4. Batch: `_build_batches(chunks)`
5. If 1 batch → `_summarize_batch(is_partial=False)`
6. If 2+ batches → partial summaries + `_merge_summaries()`
7. Verify: `agent_verify()`
8. Return: `ToolResult` with answer, verdict, confidence, citations

---

## Context Window Usage

| Metric | Value |
|--------|-------|
| Model context window | 128K tokens |
| Budget per batch | 6K tokens |
| System prompt | ~500 tokens |
| Available for chunks | 5,500 tokens |
| Chunks per batch | ~64-78 (varies by chunk size) |
| Max output | 4,096 tokens |
| **Utilization per batch** | **~4.7% of 128K** |

---

## Token Budget Visualization

```
128K Context Window
┌──────────────────────────────────────────────────────────────────┐
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│ 128K
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
│████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
└──────────────────────────────────────────────────────────────────┘
 ↑ 6K used per batch (~4.7% of 128K)
```

---

## File Changes

| Function | File | Lines | Purpose |
|----------|------|-------|---------|
| `CHUNK_TOKEN_BUDGET = 5500` | `orchestrator.py` | 1 | Token limit constant |
| `_build_batches()` | `orchestrator.py` | ~15 | Dynamic batch splitting |
| `_summarize_batch()` | `orchestrator.py` | ~30 | Single-batch LLM call |
| `_merge_summaries()` | `orchestrator.py` | ~25 | Multi-batch merge |
| `_tool_summary()` (rewrite) | `orchestrator.py` | ~25 | Orchestrates batching |
| **Total** | | **~96 lines** | |

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Document not found | Returns "I couldn't identify which document..." |
| Document has 0 chunks | Returns "I couldn't retrieve content..." |
| 1 chunk | Single batch, single LLM call |
| 125 chunks | 2 batches, 3 LLM calls + merge |
| LLM timeout on partial | Partial returns empty string, merge skips it |
| LLM timeout on merge | Falls back to concatenated partials |
| All partials fail | `ctx.answer` stays empty → agent_verify catches it |
| User asks vague summary | Classifier returns general intent, not summary |

---

## Example Outputs

### Single Batch (Backup Policy, 17 chunks)

```
The Backup Policy ensures critical systems and data are backed up 
regularly, stored securely, and can be easily restored during system 
failures or data loss [Doc: Backup_policy, Clause: 3 Purpose].

**Key Requirements:**
1. Backup Administrator ensures operations follow the policy [Doc: Backup_policy, Clause: 6.1]
2. Backups must include at least 3 copies with encryption [Doc: Backup_policy, Clause: 6.1]
3. Backups stored both on-site and off-site [Doc: Backup_policy, Clause: 6.2]
4. Restoration tested at least yearly [Doc: Backup_policy, Clause: 6.3]

**Who It Applies To:** All employees, contractors, and third parties 
who access company information [Doc: Backup_policy, Clause: 6.1]

**Consequences:** Disciplinary action proportional to violation severity 
[Doc: Backup_policy, Clause: 8]
```

### Multi-Batch (Business continuity, 125 chunks)

**Partial 1 (chunks 1-64):** Key points from emergency response, BCP framework, roles
**Partial 2 (chunks 65-125):** Key points from recovery procedures, testing, maintenance

**Merged Output:**
```
The Business Continuity and Disaster Recovery Plan ensures critical 
business processes can continue during disruptions...

**Purpose:** Maintain operational resilience...

**Key Requirements:**
- BCP must be reviewed annually...
- Recovery Time Objectives defined per system...
- Backup site testing quarterly...

[...structured sections with all citations preserved...]
```
