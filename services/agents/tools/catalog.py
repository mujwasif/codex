"""
Tool Catalog — LLM selects which tools to run based on the query.
Each selection includes the tool name, a focused phrase, optional conflict type,
and extracted document names (for conflict/summary tools).
"""

import json
import logging
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any

from services.agents.tools.llm_tools import llm_generate, AGENT_MODEL

logger = logging.getLogger(__name__)


# ─── Tool Definitions ───────────────────────────────────────────────

TOOL_CATALOG: List[Dict[str, Any]] = [
    {
        "name": "approval",
        "description": (
            "Check who can approve a process or transaction. "
            "Resolves approval authority from the knowledge graph. "
            "Use for: 'who can approve', 'approval required', 'authorization limits'."
        ),
    },
    {
        "name": "conflict",
        "description": (
            "Detect contradictions or conflicts between policy clauses or documents. "
            "Use for: 'compare', 'contradictions', 'conflict between', 'differences'."
        ),
    },
    {
        "name": "compliance",
        "description": (
            "Assess compliance risks, regulatory obligations, and violation severity. "
            "Use for: 'compliance', 'risk', 'regulation', 'violation', 'obligation'."
        ),
    },
    {
        "name": "procedure",
        "description": (
            "Generate step-by-step procedures or workflows from policy documents. "
            "Use for: 'how to', 'steps', 'process', 'workflow', 'procedure'."
        ),
    },
    {
        "name": "summary",
        "description": (
            "Summarize an entire policy document. "
            "Use for: 'summarize', 'overview', 'key points', 'tell me about [doc]'."
        ),
    },
    {
        "name": "conversational",
        "description": (
            "Handle greetings, small talk, general non-policy conversation. "
            "Use for: 'hello', 'hi', 'thank you', 'how are you'."
        ),
    },
]

TOOL_NAMES = [t["name"] for t in TOOL_CATALOG]


# ─── Selection Data Structure ───────────────────────────────────────

@dataclass
class ToolSelection:
    """A selected tool with its focused phrase, extracted document names, and unique call ID."""
    tool_name: str
    phrase: str
    call_id: str = ""
    conflict_type: Optional[str] = None
    doc_names: Optional[List[str]] = None


# ─── Selection Prompt ────────────────────────────────────────────────

TOOL_SELECTION_SYSTEM = """You are a tool selector for a policy query system.
Given a user query, select ALL relevant tools AND rewrite a detailed, standalone question for each tool.

Available tools:
{tool_descriptions}

Conflict types (only for the conflict tool):
- "type_1": No specific documents mentioned. The user asks about a clause or general contradiction.
- "type_2": Two or more specific documents mentioned. The user wants to compare them.
- "type_2b": One specific document mentioned. The user wants to check it against everything else.

Document name extraction (CRITICAL for conflict and summary tools):
- For conflict and summary tools, you MUST extract the individual document/policy names mentioned in the query
- Put them in the "doc_names" array as separate strings
- Spell out abbreviations (e.g., user says "DR" → "Disaster Recovery Policy")
- Each doc_names entry should be a standalone document name that can be searched in a database
- If no specific document is mentioned, set doc_names to an empty array []
- The SAME tool can appear multiple times if the query mentions multiple documents for the same tool type

Phrase rewriting rules (CRITICAL — follow these strictly):
- Each phrase MUST be a complete, standalone question — not a fragment
- NEVER use pronouns like "it", "them", "this" — replace with the actual noun
- NEVER use abbreviations — spell everything out
- Include specifics: amounts, document names, process names
- BAD: "who approves it" → GOOD: "Who approves vendor onboarding?"
- BAD: "$10K" → GOOD: "$10,000"
- BAD: "DR Policy" → GOOD: "Disaster Recovery Policy"
- "conversational" is standalone — never combine with other tools

DECISION LOGIC (follow this for every query):

Step 1: Is this a greeting or small talk? → pick "conversational"

Step 2: Pick the appropriate tool(s) for the query and rewrite the phrase

Respond with ONLY a JSON object:
{{
  "tools": [
    {{"tool": "tool_name", "phrase": "detailed standalone question", "conflict_type": null, "doc_names": ["Document Name"]}}
  ]
}}

Examples:
Q: "Who can approve a purchase over $10K?"
→ {{"tools": [{{"tool": "approval", "phrase": "Who can approve a purchase over $10,000?", "conflict_type": null, "doc_names": []}}]}}

Q: "Summarize the Password Policy"
→ {{"tools": [{{"tool": "summary", "phrase": "Summarize the Password Management Policy", "conflict_type": null, "doc_names": ["Password Management Policy"]}}]}}

Q: "Who can approve $10K and what are the compliance risks?"
→ {{"tools": [
    {{"tool": "approval", "phrase": "Who can approve a purchase over $10,000?", "conflict_type": null, "doc_names": []}},
    {{"tool": "compliance", "phrase": "What are the compliance risks for purchase approvals?", "conflict_type": null, "doc_names": []}}
]}}

Q: "Compare the Backup Policy and DR Policy"
→ {{"tools": [{{"tool": "conflict", "phrase": "Compare the Backup Policy and Disaster Recovery Policy for conflicts", "conflict_type": "type_2", "doc_names": ["Backup Policy", "Disaster Recovery Policy"]}}]}}

Q: "Are there conflicts in the Password Policy?"
→ {{"tools": [{{"tool": "conflict", "phrase": "Are there any internal conflicts or contradictions within the Password Management Policy?", "conflict_type": "type_2b", "doc_names": ["Password Management Policy"]}}]}}

Q: "Summarize the Password Policy, compare it with the Security Policy, and check compliance"
→ {{"tools": [
    {{"tool": "summary", "phrase": "Summarize the Password Management Policy", "conflict_type": null, "doc_names": ["Password Management Policy"]}},
    {{"tool": "conflict", "phrase": "Compare the Password Management Policy and the Security Policy for conflicts", "conflict_type": "type_2", "doc_names": ["Password Management Policy", "Security Policy"]}},
    {{"tool": "compliance", "phrase": "What are the compliance risks associated with the Password Management Policy?", "conflict_type": null, "doc_names": ["Password Management Policy"]}}
]}}

Q: "What is the process for vendor onboarding, who approves it, and what are the risks?"
→ {{"tools": [
    {{"tool": "procedure", "phrase": "What is the step-by-step process for vendor onboarding?", "conflict_type": null, "doc_names": []}},
    {{"tool": "approval", "phrase": "Who has the authority to approve vendor onboarding?", "conflict_type": null, "doc_names": []}},
    {{"tool": "compliance", "phrase": "What are the compliance risks associated with vendor onboarding?", "conflict_type": null, "doc_names": []}}
]}}

Q: "Hello!"
→ {{"tools": [{{"tool": "conversational", "phrase": "Hello!", "conflict_type": null, "doc_names": []}}]}}
"""


def _build_tool_descriptions() -> str:
    lines = []
    for i, tool in enumerate(TOOL_CATALOG, 1):
        lines.append(f"{i}. {tool['name']} — {tool['description']}")
    return "\n".join(lines)


def select_tools(
    question: str,
    access_level: int = 1,
    department: str = "",
) -> Tuple[List[ToolSelection], float]:
    """
    LLM-based tool selection. Returns (list of ToolSelection, confidence).
    Falls back to [ToolSelection("general", question)] if LLM fails.
    """
    system_prompt = TOOL_SELECTION_SYSTEM.format(
        tool_descriptions=_build_tool_descriptions()
    )

    result = llm_generate(
        model=AGENT_MODEL,
        system_prompt=system_prompt,
        user_message=f"Q: \"{question}\"\n→",
        temperature=0.0,
        max_tokens=800,
        timeout=12.0,
    )

    if not result.success or not result.data:
        logger.warning("Tool selection LLM failed, falling back to general")
        fb = ToolSelection(tool_name="general", phrase=question)
        fb.call_id = "general_0"
        return [fb], 0.1

    try:
        text = result.data.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON found in response")

        parsed = json.loads(text[start:end])
        raw_tools = parsed.get("tools", [])

        selections = []
        for item in raw_tools:
            tool_name = item.get("tool", "")
            phrase = item.get("phrase", question)
            conflict_type = item.get("conflict_type")
            doc_names = item.get("doc_names")

            # Validate doc_names
            if doc_names and isinstance(doc_names, list):
                doc_names = [str(d).strip() for d in doc_names if d and str(d).strip()]
            else:
                doc_names = None

            if tool_name not in TOOL_NAMES:
                continue

            if tool_name == "conversational":
                s = ToolSelection(tool_name=tool_name, phrase=phrase)
                s.call_id = f"{tool_name}_0"
                selections = [s]
                break

            selections.append(ToolSelection(
                tool_name=tool_name,
                phrase=phrase,
                conflict_type=conflict_type,
                doc_names=doc_names if doc_names else None,
            ))

        if not selections:
            selections = [ToolSelection(tool_name="general", phrase=question)]

        # Assign unique call_ids: approval_0, approval_1, conflict_0, etc.
        call_counts = {}
        for s in selections:
            name = s.tool_name
            s.call_id = f"{name}_{call_counts.get(name, 0)}"
            call_counts[name] = call_counts.get(name, 0) + 1

        confidence = 0.9 if len(selections) == 1 else 0.85

        return selections, confidence

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"Failed to parse tool selection: {e}")
        fb = ToolSelection(tool_name="general", phrase=question)
        fb.call_id = "general_0"
        return [fb], 0.1
