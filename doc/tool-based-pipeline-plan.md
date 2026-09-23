# Tool-Based Pipeline Plan

Convert the intent classification system to LLM-driven tool selection with parallel execution and synthesis.

## Architecture

```
User query
  │
  ▼
select_tools(query) → [ToolSelection(name, phrase, conflict_type), ...]
  │
  ▼
Shared steps: agent_resolve_docs() + agent_retrieve()
  │
  ▼
Batch execution (max_workers=2):
  Batch 1: Tool A (isolated ctx) + Tool B (isolated ctx)
  Batch 2: Tool C (isolated ctx) + ...
  │
  ▼
synthesize_answer(tool_results, original_chunks) → final answer
```

## Files to Create

### 1. `services/agents/tools/catalog.py`

Tool catalog + selection function. Each tool has a name, description, and the LLM picks which tools to run.

- `TOOL_CATALOG` — list of 6 tools: approval, conflict, compliance, procedure, summary, conversational
- `ToolSelection` dataclass — `tool_name`, `phrase`, `conflict_type`
- `select_tools()` — LLM call that returns tool selections with focused phrases
- Prompt includes conflict type rules (type_1, type_2, type_2b) and phrase extraction

### 2. `services/agents/synthesizer.py`

LLM merge of multi-tool results into one coherent answer.

- `synthesize_answer()` — takes tool results + chunks, returns (answer, verdict, confidence, citations)
- `_determine_verdict()` — aggregates verdicts from all tools (worst-case wins)
- `_estimate_confidence()` — averages tool confidences, penalized by failures
- `_extract_citations()` — parses [Doc: X, Clause: Y] from merged answer
- `_fallback_merge()` — concatenates answers if synthesis LLM fails

## Files to Modify

### 3. `services/agents/tools/__init__.py`

Add imports and exports for `ToolSelection`, `select_tools`, `TOOL_NAMES`.

### 4. `services/agents/orchestrator.py`

- Add imports for `select_tools`, `ToolSelection`, `synthesize_answer`
- Add `_run_tool(selection, ctx)` — creates isolated QueryContext per tool, sets intent/conflict_type, runs agent chain
- Add `_copy_tool_fields(ctx, data)` — copies tool-specific fields to context
- Rewrite `run_pipeline()` — tool selection → shared steps → batched parallel execution → synthesis

## Critical Design Decisions

### Isolated Context (Race Condition Fix)

All tools share the same `ctx` object. Parallel execution causes race conditions on `ctx.answer`, `ctx.verdict`, `ctx.confidence`, `ctx.citations`, `ctx.chunks`.

**Solution:** Create a fresh `QueryContext` for each tool. Copy only shared read-only data (chunks, resolved_doc_ids). Each tool runs in isolation. After completion, extract results and discard the copy.

```python
def _run_tool(selection: ToolSelection, ctx: QueryContext) -> ToolResult:
    tool_ctx = QueryContext(
        question=selection.phrase,
        user_id=ctx.user_id,
        access_level=ctx.access_level,
        ...
    )
    tool_ctx.chunks = list(ctx.chunks)  # Shallow copy
    tool_ctx.resolved_doc_ids = list(ctx.resolved_doc_ids)
    tool_ctx.state = QueryState.RETRIEVED
    # Run tool on isolated context
    # Return results (tool_ctx discarded after this)
```

### Intent Setting

`agent_reason()` has special handling per intent:
- `ctx.intent == APPROVAL` → injects approval result into query
- `ctx.intent == CONFLICT` → uses `build_conflict_answer()`
- `ctx.intent == COMPLIANCE` → uses `build_risk_answer()`
- `ctx.intent == PROCEDURE` → enriches query with procedure data

`agent_procedure_reason()` guards: `if ctx.intent != PROCEDURE: return`

`agent_verify()` applies confidence floors for APPROVAL and COMPLIANCE.

**Solution:** Set `tool_ctx.intent` before calling agent functions in each tool.

### Conflict Types

| Type | Name | Trigger | Handler |
|------|------|---------|---------|
| `type_1` | Clause vs Corpus | No specific docs | `analyze_clause_vs_corpus()` |
| `type_2` | Doc vs Doc | 2+ documents | `compare_document_chunks()` |
| `type_2b` | Single Doc vs Corpus | 1 document | `detect_conflicting_documents()` |

The LLM determines conflict type when selecting the conflict tool.

### Original Chunks for Synthesis

Conflict tools replace `ctx.chunks` with citation chunks. Store a copy of original chunks before tools run:

```python
original_chunks = list(ctx.chunks)
# ... run tools ...
# Use original_chunks for synthesis
```

### Same Tool Multiple Times

The same tool can be selected multiple times with different phrases:

```json
{
  "tools": [
    {"tool": "approval", "phrase": "Who can approve $10K?"},
    {"tool": "approval", "phrase": "Who can approve $50K?"}
  ]
}
```

Each runs with its own isolated context. Results collected by index, not tool name.

## Edge Cases

| Scenario | Handling |
|----------|----------|
| LLM returns invalid JSON | Fallback to `[ToolSelection("general", question)]` |
| LLM returns empty list | Default to `[ToolSelection("general", question)]` |
| Tool fails during execution | Continue with other tools, synthesis skips failed tool |
| Same tool selected twice | Both run with different phrases (isolated ctx each) |
| `conflict` needs doc selection | `agent_resolve_docs()` handles it |
| `conversational` + others | LLM prompt forbids this; if it happens, run conversational only |
| Retrieval fails | `ctx.fail()` called, synthesis returns error |

## Execution Order

| Step | File | Action |
|------|------|--------|
| 1 | `doc/tool-based-pipeline-plan.md` | Save this plan |
| 2 | `services/agents/tools/catalog.py` | Create new file |
| 3 | `services/agents/synthesizer.py` | Create new file |
| 4 | `services/agents/tools/__init__.py` | Add imports + exports |
| 5 | `services/agents/orchestrator.py` | Add imports, helpers, rewrite run_pipeline() |
| 6 | Test | Run with sample queries |
