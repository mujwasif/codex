"""
Synthesis Agent — merges multiple tool results into one coherent answer.

No longer needs raw chunks — each tool already processes chunks and includes
citations. The synthesis LLM merges tool answers and preserves their citations.

Supports batching: if combined tool results exceed 10k tokens, splits into
batches, synthesizes each separately, then merges.
"""

import re
import json
import logging
from typing import Dict, Any, List, Tuple
from services.agents.tools.llm_tools import llm_generate, AGENT_MODEL
from services.agents.tools.base import ToolResult

logger = logging.getLogger(__name__)

TOKEN_BUDGET = 16000
MAX_TOKENS_PER_BATCH = 12000


def synthesize_answer(
    question: str,
    tool_results: Dict[str, ToolResult],
    access_level: int = 1,
) -> Tuple[str, str, float, List[Dict[str, Any]]]:
    """
    Merge multiple tool results into one coherent, detailed, human-like answer.
    If tool results exceed 10k tokens, processes in batches and merges.
    Returns (answer, verdict, confidence, citations).
    """
    tool_text = _format_tool_results(tool_results)
    estimated = _estimate_tokens(tool_text)

    logger.info(
        "Synthesis: %d tools, ~%d estimated tokens",
        len(tool_results), estimated,
    )

    if estimated <= TOKEN_BUDGET:
        # Single batch — fits in one LLM call
        raw_answer = _call_synthesis_llm(question, tool_text)
    else:
        # Split into batches and merge
        logger.info("Synthesis: input exceeds %d tokens, using batched synthesis", TOKEN_BUDGET)
        raw_answer = _batched_synthesize(question, tool_results)

    if raw_answer is None:
        return _fallback_merge(tool_results)

    answer = raw_answer.strip()
    verdict = _determine_verdict(tool_results)
    confidence = _estimate_confidence(tool_results)

    # Collect per-tool citations
    all_citations = []
    for tool_result in tool_results.values():
        if tool_result.success and tool_result.data:
            tool_citations = tool_result.data.get("citations", [])
            all_citations.extend(tool_citations)

    # Merge with synthesis citations
    synthesis_citations = _extract_citations(answer)
    all_citations.extend(synthesis_citations)

    # Deduplicate by chunk_id
    seen_ids = set()
    merged_citations = []
    for citation in all_citations:
        chunk_id = citation.get("chunk_id")
        if chunk_id and chunk_id not in seen_ids:
            seen_ids.add(chunk_id)
            merged_citations.append(citation)
        elif not chunk_id:
            merged_citations.append(citation)

    return answer, verdict, confidence, merged_citations


# ═══════════════════════════════════════
#  Token Estimation
# ═══════════════════════════════════════

def _estimate_tokens(text: str) -> int:
    """Estimate token count (~4 chars per token for English)."""
    return len(text) // 4


# ═══════════════════════════════════════
#  Single-Batch Synthesis
# ═══════════════════════════════════════

def _get_system_prompt() -> str:
    """Shared system prompt for synthesis."""
    return (
        "You are a senior policy advisor producing a comprehensive, detailed policy analysis report.\n\n"

        "## LENGTH\n"
        "- Each tool section: 2-4 paragraphs with full detail\n"
        "- Overall: minimum 500 words for multi-tool queries\n"
        "- Do NOT abbreviate or skip details — include ALL findings from each tool\n"
        "- Include every specific detail: names, dates, amounts, thresholds, process steps, regulatory references, clause numbers\n\n"

        "## STRUCTURE\n"
        "1. **Section per tool** — one ## section for each analysis area, with full detail\n"
        "2. **Cross-references** — link related findings across tools when relevant\n"
        "3. **Recommendations** — ONLY include if there are actionable issues to address (conflicts, compliance gaps, governance risks). Skip this section if everything is clear.\n\n"

        "## SECTIONS BY TOOL TYPE\n"
        "| Tool | Format |\n"
        "|------|--------|\n"
        "| PROCEDURE | `1. **Step Name**: detailed explanation with context [Doc: X, Clause: Y]` |\n"
        "| APPROVAL | **Role** in bold → approval chain → thresholds → conditions → who can/cannot approve → citations |\n"
        "| CONFLICT | Quote both sides VERBATIM → explain WHY they contradict → impact analysis → cite both sources |\n"
        "| COMPLIANCE | **VERDICT** in bold → obligations table (mandatory/optional) → specific regulations → risk level → detailed recommendations |\n"
        "| SUMMARY | Introduction → Key Points (3-5 bullets) → Obligations/Requirements → Implications |\n\n"

        "## CRITICAL RULES\n"
        "- Do NOT skip any tool's findings — every tool's output must appear in the answer\n"
        "- Preserve ALL specific details: names, amounts, timelines, process steps\n"
        "- Cross-reference related findings between tools\n"
        "- Use concrete language, not vague summaries\n"
        "- If a tool returned multiple findings, include ALL of them — do not summarize to one highlight\n\n"

        "## CITATIONS\n"
        "- Preserve ALL [Doc: {title}, Clause: {ref}] from tool answers\n"
        "- Do NOT add new citations not in the tool answers\n"
        "- Place citations after the sentence, before the period\n\n"

        "## STYLE\n"
        "- C-suite briefing: authoritative, precise, actionable\n"
        "- **Bold** risk levels, roles, amounts, deadlines\n"
        "- Use tables for structured data (obligations, thresholds)\n"
        "- No filler phrases ('Based on the analysis', 'It is important to note')\n"
        "- No emojis"
    )


def _call_synthesis_llm(question: str, tool_text: str) -> str | None:
    """Send one synthesis request to the LLM. Returns answer or None."""
    system_prompt = _get_system_prompt()

    user_message = (
        f"Question: {question}\n\n"
        f"Tool Results:\n{tool_text}\n\n"
        f"Write the final answer:"
    )

    result = llm_generate(
        model=AGENT_MODEL,
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=0.0,
        max_tokens=8192,
        timeout=60.0,
    )

    if result.success and result.data:
        return result.data
    logger.warning("Synthesis LLM call failed: %s", result.error)
    return None


# ═══════════════════════════════════════
#  Batched Synthesis
# ═══════════════════════════════════════

def _batched_synthesize(question: str, tool_results: Dict[str, ToolResult]) -> str | None:
    """
    Split tool results into token-bounded batches, synthesize each,
    then merge into one answer.
    """
    batches = _split_into_batches(tool_results, MAX_TOKENS_PER_BATCH)
    logger.info("Synthesis: split into %d batches", len(batches))

    batch_answers = []
    for i, batch in enumerate(batches):
        batch_text = _format_tool_results(batch)
        logger.info(
            "Synthesis batch %d/%d: %d tools, ~%d tokens",
            i + 1, len(batches), len(batch), _estimate_tokens(batch_text),
        )
        answer = _call_synthesis_llm(question, batch_text)
        if answer:
            batch_answers.append(answer.strip())
        else:
            logger.warning("Synthesis batch %d failed, skipping", i + 1)

    if not batch_answers:
        return None

    if len(batch_answers) == 1:
        return batch_answers[0]

    # Merge multiple batch answers into one
    return _merge_batch_answers(question, batch_answers)


def _split_into_batches(
    tool_results: Dict[str, ToolResult],
    max_tokens_per_batch: int = MAX_TOKENS_PER_BATCH,
) -> List[Dict[str, ToolResult]]:
    """Split tool results into batches that fit within token budget."""
    batches = []
    current_batch = {}
    current_tokens = 0

    for tool_name, result in tool_results.items():
        tool_text = _format_single_tool(tool_name, result)
        tool_tokens = _estimate_tokens(tool_text)

        # If adding this tool exceeds budget, start a new batch
        if current_tokens + tool_tokens > max_tokens_per_batch and current_batch:
            batches.append(current_batch)
            current_batch = {}
            current_tokens = 0

        current_batch[tool_name] = result
        current_tokens += tool_tokens

    if current_batch:
        batches.append(current_batch)

    return batches


def _merge_batch_answers(question: str, batch_answers: List[str]) -> str | None:
    """Merge multiple batch answers into one coherent answer."""
    if len(batch_answers) == 1:
        return batch_answers[0]

    numbered = "\n\n".join(
        f"--- Section {i + 1} ---\n{ans}" for i, ans in enumerate(batch_answers)
    )

    system_prompt = (
        "Merge these batch analysis sections into one cohesive answer.\n\n"
        "RULES:\n"
        "- Deduplicate: if the same fact appears in multiple sections, keep it once\n"
        "- Combine related topics into unified sections\n"
        "- Preserve ALL [Doc: X, Clause: Y] citations\n"
        "- Keep ## header structure\n"
        "- Start with a 1-sentence executive summary\n"
        "- End with recommendations\n"
        "- Follow the same style as the main synthesis: C-suite briefing, bold key terms, tables for data"
    )

    user_message = (
        f"User Question: {question}\n\n"
        f"Sections to merge:\n\n{numbered}\n\n"
        f"Merge these sections into one cohesive, well-structured answer."
    )

    result = llm_generate(
        model=AGENT_MODEL,
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=0.0,
        max_tokens=8192,
        timeout=60.0,
    )

    if result.success and result.data:
        return result.data
    logger.warning("Batch merge LLM call failed: %s", result.error)
    # Fallback: concatenate sections
    return "\n\n---\n\n".join(batch_answers)


# ═══════════════════════════════════════
#  Formatting
# ═══════════════════════════════════════

def _format_tool_results(tool_results: Dict[str, ToolResult]) -> str:
    """Format all tool results as human-readable text."""
    sections = []
    for idx, (tool_name, result) in enumerate(tool_results.items()):
        sections.append(f"--- Result {idx + 1}: {tool_name.upper()} ---\n"
                        + _format_single_tool(tool_name, result))
    return "\n\n".join(sections)


def _format_single_tool(tool_name: str, result: ToolResult) -> str:
    """Format a single tool result as human-readable text."""
    if not result.success or not result.data:
        error = result.error or "Tool failed"
        return f"Error: {error}\nThis tool could not complete. Focus on the other successful results."

    data = result.data
    parts = []

    if data.get("answer"):
        parts.append(f"Answer: {data['answer']}")

    if data.get("approval"):
        approval = data["approval"]
        roles = ", ".join(approval.get("matching_roles", []))
        process = approval.get("process", "Unknown")
        amount = approval.get("amount")
        user_can = approval.get("user_can_approve", False)
        parts.append(
            f"Approval Authority: Process '{process}' requires approval from: "
            f"{roles or 'Not defined in knowledge graph'}. "
            f"User can approve: {'Yes' if user_can else 'No'}."
            + (f" Amount threshold: ${amount:,.2f}." if amount else "")
        )

    if data.get("conflicts"):
        conflicts = data["conflicts"]
        if isinstance(conflicts, list):
            for i, conflict in enumerate(conflicts, 1):
                ref_a = conflict.get("clause_a_id", conflict.get("target_id", "?"))
                ref_b = conflict.get("clause_b_id", conflict.get("candidate_id", "?"))
                reason = conflict.get("reason", "No reason provided")
                confidence = conflict.get("confidence", "")
                conf_str = f" (confidence: {confidence})" if confidence else ""
                parts.append(f"Conflict {i}: Between {ref_a} and {ref_b}{conf_str} — {reason}")

    if data.get("conflict_analysis"):
        analysis = data["conflict_analysis"]
        if analysis.get("inconclusive"):
            parts.append("Conflict Analysis: Analysis was inconclusive.")
        elif analysis.get("coverage_note"):
            parts.append(f"Conflict Analysis: {analysis['coverage_note']}")

    if data.get("risk"):
        risk = data["risk"]
        parts.append(
            f"Risk Verdict: {risk.get('verdict', 'unknown').upper()}. "
            f"Regulations: {', '.join(risk.get('regulations', [])) or 'None identified'}. "
            f"Recommendations: {'; '.join(risk.get('recommendations', []))}"
        )

    if data.get("verdict") and not data.get("risk"):
        parts.append(f"Verdict: {data['verdict']}")

    return "\n".join(parts) if parts else "No data returned."


# ═══════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════

def _determine_verdict(tool_results: Dict[str, ToolResult]) -> str:
    verdicts = []
    for result in tool_results.values():
        if result.success and result.data:
            v = result.data.get("verdict", "")
            if v:
                verdicts.append(v)
    if "violation" in verdicts:
        return "violation"
    if "conditional" in verdicts:
        return "conditional"
    if "conflict" in verdicts:
        return "conflict"
    if "clear" in verdicts:
        return "clear"
    return "abstained"


def _estimate_confidence(tool_results: Dict[str, ToolResult]) -> float:
    if not tool_results:
        return 0.0
    confidences = []
    for result in tool_results.values():
        if result.success and result.data:
            confidences.append(result.data.get("confidence", 0.0))
        else:
            confidences.append(0.0)
    if not confidences:
        return 0.0
    avg = sum(confidences) / len(confidences)
    failure_penalty = sum(1 for r in tool_results.values() if not r.success) * 0.1
    return max(0.0, avg - failure_penalty)


def _extract_citations(answer: str) -> List[Dict]:
    """Extract [Doc: {title}, Clause: {ref}] citations from answer text."""
    citations = []
    pattern = r"\[Doc:\s*([^,\]]+),\s*Clause:\s*([^\]]+)\]"
    for match in re.finditer(pattern, answer):
        title = match.group(1).strip()
        clause_ref = match.group(2).strip()
        citations.append({
            "chunk_id": None,
            "clause_ref": clause_ref,
            "title": title,
            "score": 0.0,
        })
    return citations


def _fallback_merge(
    tool_results: Dict[str, ToolResult],
) -> Tuple[str, str, float, List]:
    parts = []
    for tool_name, result in tool_results.items():
        if result.success and result.data and result.data.get("answer"):
            parts.append(f"**{tool_name.title()}:** {result.data['answer']}")
    if parts:
        return "\n\n".join(parts), "abstained", 0.5, []
    return "Unable to generate an answer.", "abstained", 0.0, []
