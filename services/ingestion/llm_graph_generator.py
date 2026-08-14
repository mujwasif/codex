"""
LLM-based Knowledge Graph Generator.

For each clause, calls Qwen2.5-3B-Instruct with a Chain-of-Thought prompt to discover:
  - Node types from: Policy, Clause, Role, Department, Process, Threshold, Regulation
  - Relationship types: PART_OF, CAN_APPROVE, REQUIRES_THRESHOLD, GOVERNS, MAPS_TO, CONFLICTS_WITH, SUPERSEDES

Department names are resolved to the 3 canonical departments via the prompt rule,
a deterministic alias map, and (as a last resort) a focused LLM retry.

Usage:
    from services.ingestion.llm_graph_generator import generate_graph_for_chunk
"""

import json
import logging
import re
import time
from typing import List, Dict, Optional
from services.agents.tools.llm_tools import llm_generate, QWEN3_4B_MODEL

from packages.shared.config import LLAMA_4B_URL, QWEN3_4B_MODEL

LLAMA_URL = f"{LLAMA_4B_URL}/v1/chat/completions"
MODEL_NAME = QWEN3_4B_MODEL

ALLOWED_LABELS = {"Policy", "Clause", "Role", "Department", "Process", "Threshold", "Regulation"}
ALLOWED_REL_TYPES = {"PART_OF", "CAN_APPROVE", "REQUIRES_THRESHOLD", "GOVERNS", "MAPS_TO", "BELONGS_TO", "CONFLICTS_WITH", "SUPERSEDES"}

# Department name normalization: lowercase variant -> canonical name
DEPT_ALIASES = {
    "it": "IT & InfoSec",
    "it department": "IT & InfoSec",
    "infosec network": "IT & InfoSec",
    "isms": "IT & InfoSec",
    "isms department": "IT & InfoSec",
    "information security management system": "IT & InfoSec",
    "information security management system (isms)": "IT & InfoSec",
    "information security management": "IT & InfoSec",
    "information systems management": "IT & InfoSec",
    "hr": "HR",
    "hr department": "HR",
    "employees": "HR",
    "company": "Company & Ops",
    "courier company": "Company & Ops",
    "emergency services": "Company & Ops",
    "it & infosec": "IT & InfoSec",
    "company & ops": "Company & Ops",
}

# Access level per canonical department (3=all, 2=managerial, 1=employee)
DEPT_LEVEL_MAP = {
    "IT & InfoSec": 2,
    "HR": 1,
    "Company & Ops": 3,
}

# Nodes that the LLM mislabels as Department must be reclassified
DEPT_LABEL_OVERRIDES = {
    "isms manager": "Role",
    "supplier management": "Process",
}

# Values that carry no real information and must not be written to the graph
PLACEHOLDER_VALUES = {
    "", "n/a", "na", "n.a.", "not specified", "none", "null", "tbd", "unknown",
    "nil", "-", "—", "not applicable", "to be determined",
}

# Units that mark a threshold as time-based
TIME_UNITS = {
    "second", "seconds", "minute", "minutes", "hour", "hours", "day", "days",
    "week", "weeks", "month", "months", "year", "years",
}

_BASE_SYSTEM_PROMPT = """You are a strict knowledge graph generator for enterprise policies.
Your job is to read policy text and produce Neo4j nodes and edges in pure JSON format.

CRITICAL CONTEXT RULE:
You are provided with PREVIOUS CHUNK, CURRENT CHUNK, and NEXT CHUNK.
- You must extract nodes and relationships ONLY for actions, rules, and facts contained within the CURRENT CHUNK.
- Use PREVIOUS CHUNK and NEXT CHUNK strictly as reference context to resolve ambiguous pronouns, missing role titles, or sentence continuations.
- Do NOT create nodes or edges for processes, thresholds, or rules that exist entirely inside PREVIOUS CHUNK or NEXT CHUNK.

EXACTLY 7 node types are allowed — never invent new ones:

| Label | When to create | Merge Key Schema | Key Properties |
|-------|----------------|------------------|----------------|
| Policy | Always — one per document. | {"id": "<doc_id>"} | title, version, status |
| Clause | Always — one per chunk. | {"id": "<chunk_id>"} | clause_ref, section_path, text_ref |
| Role | Person, title, or authority mentioned in CURRENT CHUNK. | {"name": "<role_name>"} | level |
| Department | Team, unit, or org group mentioned in CURRENT CHUNK. | {"name": "<dept_name>"} | access_level (see DEPARTMENT NORMALIZATION RULE) |
| Process | Action, procedure, or governable activity mentioned in CURRENT CHUNK. | {"name": "<process_name>"} | category |
| Threshold | Numeric amount, time period, or count cap explicitly stated in CURRENT CHUNK. | {"amount": "<number>", "basis": "<unit>"} | type: "monetary"/"time"/"count"; currency only when type="monetary" |
| Regulation | Law, standard, or regulatory body referenced in CURRENT CHUNK. | {"name": "<reg_name>"} | ref |

EXACTLY 8 relationship types are allowed — never invent new ones:

| Type | When to create |
|------|----------------|
| PART_OF | Always — Clause belongs to Policy. |
| CAN_APPROVE | When a Role or Department has authority over a Process. |
| REQUIRES_THRESHOLD | When a Process is gated by a Threshold. |
| GOVERNS | When a Clause regulates or sets rules for a Process. |
| MAPS_TO | When a Policy satisfies or references a Regulation. |
| BELONGS_TO | When a Role belongs to a Department. |
| CONFLICTS_WITH | When two Clauses contradict each other. |
| SUPERSEDES | When a Policy replaces an older version. |

GRAPH INTEGRITY RULES:
1. Link Processes to Clause: Every extracted Process MUST be connected to the CURRENT Clause using a GOVERNS relationship. Use the Clause `chunk_id` as the `from_key` for the Clause.
2. Strict JSON Output: Reason step-by-step following the CHAIN-OF-THOUGHT WORKFLOW, then output the final graph as a valid JSON object matching the EXACT schema below, wrapped in a ```json fenced code block. Do not add any text after the closing fence.
3. Completeness: Every Threshold, Role, Department, Process, and Regulation you identified in your reasoning MUST appear as a node in the final JSON. Never omit an entity you reasoned about.

OUTPUT FORMAT SCHEMA:
{
  "nodes": [
    {
      "label": "Role",
      "merge_key": {"name": "IT Manager"},
      "properties": {"level": "Manager"}
    }
  ],
  "edges": [
    {
      "type": "GOVERNS",
      "from_label": "Clause",
      "from_key": {"id": "<chunk_id>"},
      "to_label": "Process",
      "to_key": {"name": "Software Installation"}
    }
  ]
}"""


_LEVEL_MEANINGS = {3: "all-access (admin)", 2: "managerial", 1: "employee"}
_DEPT_RULE_ROWS = "\n".join(
    f"| {name} | {level} |" for name, level in sorted(DEPT_LEVEL_MAP.items(), key=lambda x: x[1])
)
_DEPT_RULE = (
    "\n\nDEPARTMENT NORMALIZATION RULE:\n"
    "Department nodes must use EXACTLY one of these canonical names and include its access_level property:\n"
    "| Canonical Name | access_level |\n"
    "|----------------|-------------|\n"
    f"{_DEPT_RULE_ROWS}\n"
    "access_level meanings: "
    + ", ".join(f"{level} = {_LEVEL_MEANINGS[level]}" for level in sorted(_LEVEL_MEANINGS))
    + ".\n"
    "Map every team, unit, or org group to the closest canonical name. Never invent a new Department name.\n"
    "\"ISMS Manager\" is a Role (a person's title), not a Department.\n"
    "\"Supplier Management\" is a Process, not a Department.\n"
)

_THRESHOLD_RULE = (
    "\n\nTHRESHOLD RULE:\n"
    "- Create a Threshold node ONLY when CURRENT CHUNK states an explicit numeric amount, time period, or count cap (e.g. \"$10,000\", \"90 days\", \"12 characters\").\n"
    "- A Threshold is a SEPARATE node type. NEVER encode a threshold as a Process and NEVER fold the amount/unit into a Process name.\n"
    "- Threshold node format: {\"label\": \"Threshold\", \"merge_key\": {\"amount\": \"90\", \"basis\": \"days\"}, \"properties\": {\"type\": \"time\"}}.\n"
    "- amount must be a plain number (\"90\", \"12\"); basis is the unit (\"days\", \"characters\", \"$\").\n"
    "- type is \"monetary\" for money, \"time\" for durations or frequencies (days/months/years/hours), \"count\" for counts (characters, attempts).\n"
    "- Set currency ONLY when type=\"monetary\".\n"
    "- Connect the gated Process to its Threshold with REQUIRES_THRESHOLD: Process -[:REQUIRES_THRESHOLD]-> Threshold.\n"
    "- Worked example: text \"passwords must be at least 12 characters\" -> Process {\"name\": \"Password Length\"}, Threshold {\"amount\": \"12\", \"basis\": \"characters\", \"type\": \"count\"}, edge REQUIRES_THRESHOLD Process -> Threshold.\n"
    "- Never emit placeholder values like \"N/A\", \"Not specified\", \"none\", or \"—\". If no explicit threshold exists, do NOT create a Threshold node.\n"
)

_COT_RULE = (
    "\n\nCHAIN-OF-THOUGHT WORKFLOW (reason step-by-step first, then emit the final JSON):\n"
    "Step 1 — Read the CURRENT CHUNK and list the governable actions, procedures, or rules it mandates; these become Process nodes.\n"
    "Step 2 — Identify roles/titles, the department the action is assigned to, any explicit numeric/time/count thresholds, and referenced regulations.\n"
    "Step 3 — State which of the 3 canonical departments the identified team/unit maps to and its consequent access level (3 = all-access, 2 = managerial, 1 = employee).\n"
    "Step 4 — Decide relationships: which Clause GOVERNS each Process, whether a Role CAN_APPROVE, whether the Policy MAPS_TO a Regulation.\n"
    "Step 5 — For every explicit amount, time period, or count cap in the CURRENT CHUNK, create a dedicated Threshold node and connect its gated Process via REQUIRES_THRESHOLD. Never encode a threshold as a Process.\n"
    "Step 6 — Apply the DEPARTMENT NORMALIZATION RULE and the THRESHOLD RULE from above.\n"
    "Step 7 — Output ONLY the final graph JSON wrapped in a ```json fenced code block. Never repeat the reasoning after the block.\n"
)

_CONCISE_RULE = (
    "\n\nCONCISE REASONING RULE:\n"
    "Follow the CHAIN-OF-THOUGHT steps in order, but keep your reasoning terse.\n"
    "- Use short bullet lines: one line per entity or relationship; no prose restating clause text.\n"
    "- Do NOT repeat the clause/chunk text in your reasoning.\n"
    "- Aim for roughly 1-2 concise bullets per chunk in the batch.\n"
    "- The final ```json block is the ONLY verbose part of your output.\n"
)

SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT + _DEPT_RULE + _THRESHOLD_RULE + _COT_RULE + _CONCISE_RULE


USER_PROMPT_TEMPLATE = """Document Metadata: title="{title}", id="{doc_id}", version="{version}", status="{status}", access_level={access_level}
Current Clause Metadata: id="{chunk_id}", ref="{clause_ref}", section="{section_path}"

=== PREVIOUS CHUNK (Reference Context Only) ===
{prev_text}

=== CURRENT CHUNK (Extract Nodes & Edges From Here) ===
{chunk_text}

=== NEXT CHUNK (Reference Context Only) ===
{next_text}

Reason step-by-step following the CHAIN-OF-THOUGHT WORKFLOW, then output the strict JSON graph for CURRENT CHUNK inside a ```json fenced code block:"""


_LLM_RETRIES = 3
_LLM_BACKOFF = [5.0, 10.0, 20.0]


def _call_llm(payload: dict) -> str:
    """Call the LLM server via the pooled llm_generate tool with retry/backoff.

    Returns the raw assistant content on success. Raises RuntimeError if the
    server remains unreachable after all retries (instead of silently returning
    an "ERROR:" string that would degrade the graph to backbone-only).
    """
    system_prompt = ""
    user_message = ""
    for msg in payload.get("messages", []):
        if msg.get("role") == "system":
            system_prompt = msg.get("content", "")
        elif msg.get("role") == "user":
            user_message = msg.get("content", "")

    last_err = None
    for attempt in range(_LLM_RETRIES):
        result = llm_generate(
            model=payload.get("model", MODEL_NAME),
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=payload.get("temperature", 0.0),
            max_tokens=payload.get("max_tokens", 2048),
            timeout=900,
        )
        if result.success:
            return result.data.strip()
        last_err = result.error
        logging.getLogger("llm_graph").warning(
            "LLM call failed (attempt %d/%d): %s",
            attempt + 1,
            _LLM_RETRIES,
            result.error,
        )
        if attempt < _LLM_RETRIES - 1:
            time.sleep(_LLM_BACKOFF[min(attempt, len(_LLM_BACKOFF) - 1)])
    raise RuntimeError(f"LLM server unreachable after {_LLM_RETRIES} attempts: {last_err}")


def _is_valid_json(cand: str) -> bool:
    try:
        json.loads(cand)
        return True
    except json.JSONDecodeError:
        return False


def _repair_truncated(cand: str) -> str:
    """Recover output truncated by max_tokens: cut at the last complete '}'."""
    for i in range(len(cand) - 1, -1, -1):
        if cand[i] == "}":
            if _is_valid_json(cand[: i + 1]):
                return cand[: i + 1]
    return ""


def _last_balanced_object(text: str) -> str:
    """Return the last complete JSON object in text, ignoring prose with braces."""
    n = len(text)
    in_str = False
    esc = False
    for i in range(n - 1, -1, -1):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "}":
                depth = 0
                j_in_str = False
                j_esc = False
                for j in range(i, -1, -1):
                    c = text[j]
                    if j_in_str:
                        if j_esc:
                            j_esc = False
                        elif c == "\\":
                            j_esc = True
                        elif c == '"':
                            j_in_str = False
                    else:
                        if c == '"':
                            j_in_str = True
                        elif c == "}":
                            depth += 1
                        elif c == "{":
                            depth -= 1
                            if depth == 0:
                                return text[j:i + 1]
    return ""


def _extract_json(text: str) -> str:
    """
    Extract the graph JSON from an LLM response that may contain CoT reasoning.

    Strategy (in order):
    1. The LAST ```json fenced block (preferred; CoT prose precedes it).
    2. The last balanced JSON object in the raw text.
    3. Truncation repair for output cut off by max_tokens.
    4. Legacy regex fallback.
    """
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    fenced = [c.strip() for c in fenced if c.strip()]

    for cand in reversed(fenced):
        if _is_valid_json(cand):
            return cand
        repaired = _repair_truncated(cand)
        if repaired:
            return repaired

    balanced = _last_balanced_object(text)
    if balanced:
        if _is_valid_json(balanced):
            return balanced
        repaired = _repair_truncated(balanced)
        if repaired:
            return repaired

    cleaned = re.sub(r'```(?:json)?\s*', '', text)
    cleaned = re.sub(r'```\s*$', '', cleaned)
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        return match.group()
    return cleaned


def _is_placeholder(value) -> bool:
    return value is None or str(value).strip().lower() in PLACEHOLDER_VALUES


def _normalize_amount(value):
    """Strip currency symbols/commas/spaces and parse as a number, or None."""
    cleaned = re.sub(r'[^\d.\-]', '', str(value).strip())
    try:
        float(cleaned)
    except ValueError:
        return None
    return cleaned


# Per-process cache for LLM-based department resolution (name -> canonical or None)
_dept_resolution_cache: Dict[str, Optional[str]] = {}

_DEPT_RESOLUTION_SYSTEM_PROMPT = (
    'You map team, unit, and organization group names to EXACTLY one of three canonical '
    'departments: "IT & InfoSec", "HR", or "Company & Ops". '
    'Output ONLY a JSON object: {"canonical": "<one of the three>"}.'
)

_DEPT_RESOLUTION_USER_TEMPLATE = (
    'Map this department/team name to one of the 3 canonical departments '
    '("IT & InfoSec", "HR", "Company & Ops"). '
    'Return only JSON: {{"canonical": "<name>"}}.\n\nDepartment name: "{name}"'
)


def _resolve_department(name: str) -> Optional[str]:
    """
    Resolve a department name to a canonical name.

    Fast path uses the deterministic alias/override maps. Unknown names trigger a
    single focused LLM retry (cached per-process so each name costs one call).
    """
    lookup = str(name).strip().lower()
    if not lookup or _is_placeholder(lookup):
        return None

    canonical = DEPT_ALIASES.get(lookup)
    if canonical:
        return canonical
    if lookup in DEPT_LEVEL_MAP:
        return lookup

    if lookup in _dept_resolution_cache:
        return _dept_resolution_cache[lookup]

    result = None
    try:
        dept_result = llm_generate(
            model=MODEL_NAME,
            system_prompt=_DEPT_RESOLUTION_SYSTEM_PROMPT,
            user_message=_DEPT_RESOLUTION_USER_TEMPLATE.format(name=name),
            temperature=0.0,
            max_tokens=50,
            timeout=30,
        )
        if dept_result.success:
            content = dept_result.data.strip()
            parsed = json.loads(_extract_json(content))
            cand = str(parsed.get("canonical", "")).strip()
            for key in DEPT_LEVEL_MAP:
                if key.lower() == cand.lower():
                    result = key
                    break
    except Exception:
        result = None

    _dept_resolution_cache[lookup] = result
    return result


def _resolve_unmapped_departments(output: dict) -> dict:
    """Retry unknown Department nodes through the LLM to reach a canonical name."""
    for node in output.get("nodes", []):
        if not isinstance(node, dict) or node.get("label") != "Department":
            continue
        merge_key = node.get("merge_key")
        if not isinstance(merge_key, dict) or not merge_key:
            continue
        orig_name = str(list(merge_key.values())[0])
        lookup = orig_name.strip().lower()
        if not lookup or _is_placeholder(lookup):
            continue
        if lookup in DEPT_ALIASES or lookup in DEPT_LABEL_OVERRIDES or lookup in {c.lower() for c in DEPT_LEVEL_MAP}:
            continue
        canonical = _resolve_department(orig_name)
        if canonical:
            new_key = dict(merge_key)
            new_key[list(new_key.keys())[0]] = canonical
            node["merge_key"] = new_key
    return output


_THRESHOLD_MONETARY_RE = re.compile(
    r"\$\s?([\d,]+(?:\.\d+)?)|\b([\d,]+(?:\.\d+)?)\s*(?:usd|dollars)\b", re.IGNORECASE
)
_THRESHOLD_TIME_RE = re.compile(
    r"\b(\d+)[-\s]*(seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b", re.IGNORECASE
)
_THRESHOLD_COUNT_RE = re.compile(
    r"\b(\d+)[-\s]*(characters?|attempts?|users?|devices?|passwords?)\b", re.IGNORECASE
)


def _context_around(text: str, start: int, end: int) -> str:
    """Return the sentence containing a match, for keyword matching."""
    seps = (".", "!", "?", "\n")
    lo = 0
    for i in range(start - 1, -1, -1):
        if text[i] in seps:
            lo = i + 1
            break
    hi = len(text)
    for i in range(end, len(text)):
        if text[i] in seps:
            hi = i + 1
            break
    return text[lo:hi]


def _extract_thresholds_from_text(text: str) -> List[dict]:
    """Deterministically extract explicit thresholds from raw policy text."""
    thresholds = []

    def _add(amount: str, basis: str, ttype: str, currency, context: str):
        thresholds.append({
            "amount": amount,
            "basis": basis,
            "type": ttype,
            "currency": currency,
            "context": context,
        })

    for m in _THRESHOLD_MONETARY_RE.finditer(text):
        amount = (m.group(1) or m.group(2)).replace(",", "")
        _add(amount, "$", "monetary", "USD", _context_around(text, m.start(), m.end()))

    for m in _THRESHOLD_TIME_RE.finditer(text):
        unit = m.group(2).lower()
        if unit.endswith("s"):
            unit = unit[:-1]
        _add(m.group(1), unit, "time", None, _context_around(text, m.start(), m.end()))

    for m in _THRESHOLD_COUNT_RE.finditer(text):
        unit = m.group(2).lower()
        if unit.endswith("s"):
            unit = unit[:-1]
        _add(m.group(1), unit, "count", None, _context_around(text, m.start(), m.end()))

    return thresholds


def _token_overlap(proc_name: str, context: str) -> int:
    """Score how strongly a Process name overlaps with the threshold context."""
    proc_words = {w for w in re.findall(r"[a-z]+", proc_name.lower()) if len(w) >= 3}
    ctx_words = {w for w in re.findall(r"[a-z]+", context.lower()) if len(w) >= 3}
    score = 0
    for pw in proc_words:
        for cw in ctx_words:
            if pw in cw or cw in pw:
                score += 1
    return score


def _attach_thresholds(graph: dict, text: str) -> dict:
    """
    Deterministic Threshold backstop — authoritative.

    The LLM's CoT often drops or mistypes Threshold nodes/edges. This pass rebuilds
    them from regex extraction over the raw text: every explicit amount/time/count
    becomes a correctly-typed Threshold node linked to its best-matching Process.

    Model Threshold nodes whose amount matches a regex extraction are replaced with
    the canonical form; Threshold nodes for amounts the regex did not find (e.g.
    word-form numbers) are preserved.
    """
    thresholds = _extract_thresholds_from_text(text or "")
    by_amount = {}
    for t in thresholds:
        by_amount.setdefault(t["amount"], t)

    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])

    # Drop all REQUIRES_THRESHOLD edges; they are rebuilt below.
    edges[:] = [e for e in edges if e.get("type") != "REQUIRES_THRESHOLD"]

    # Replace model Threshold nodes whose amount is covered by a regex extraction;
    # keep Threshold nodes for amounts the regex did not find.
    rebuilt_nodes = []
    for n in nodes:
        if n.get("label") != "Threshold":
            rebuilt_nodes.append(n)
            continue
        mk = n.get("merge_key") or {}
        amount = str(mk.get("amount", "") or (list(mk.values())[0] if mk else ""))
        if _normalize_amount(amount) in by_amount:
            continue  # canonical form added below
        rebuilt_nodes.append(n)
    nodes[:] = rebuilt_nodes

    procs = [n for n in nodes if n.get("label") == "Process"]
    seen_edges = set(
        (e.get("type"), str(e.get("from_key", {})), str(e.get("to_key", {}))) for e in edges
    )
    seen_amounts = set()
    for n in nodes:
        if n.get("label") == "Threshold":
            mk = n.get("merge_key") or {}
            seen_amounts.add(str(mk.get("amount", list(mk.values())[0] if mk else "")))

    for t in thresholds:
        if t["amount"] in seen_amounts:
            continue
        seen_amounts.add(t["amount"])

        node = {
            "label": "Threshold",
            "merge_key": {"amount": t["amount"], "basis": t["basis"]},
            "properties": {"type": t["type"]},
        }
        if t["currency"]:
            node["properties"]["currency"] = t["currency"]
        nodes.append(node)

        best, best_score = None, 0
        for p in procs:
            pk = str(list(p.get("merge_key", {}).values())[0])
            score = _token_overlap(pk, t["context"])
            if score > best_score:
                best, best_score = p, score

        # Link only when the best Process is unique and shares at least one keyword
        if best is not None and best_score >= 1:
            unique = all(
                _token_overlap(str(list(q.get("merge_key", {}).values())[0]), t["context"]) < best_score
                for q in procs if q is not best
            )
            if unique:
                edge = {
                    "type": "REQUIRES_THRESHOLD",
                    "from_label": "Process",
                    "from_key": best["merge_key"],
                    "to_label": "Threshold",
                    "to_key": node["merge_key"],
                }
                ekey = ("REQUIRES_THRESHOLD", str(best["merge_key"]), str(node["merge_key"]))
                if ekey not in seen_edges:
                    seen_edges.add(ekey)
                    edges.append(edge)

    return graph


def _sanitize_graph(output: dict) -> dict:
    """Filters out invalid labels, relationship types, and unformed objects."""

    def _valid_prop_key(k: str) -> bool:
        return bool(re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', str(k)))

    clean_nodes = []
    node_rewrite = {}
    dropped_identities = set()

    for node in output.get("nodes", []):
        if not isinstance(node, dict):
            continue
        label = node.get("label")
        merge_key = node.get("merge_key")
        props = node.get("properties", {})

        if not (label in ALLOWED_LABELS and isinstance(merge_key, dict) and merge_key):
            continue

        orig_name = str(list(merge_key.values())[0])
        new_label = label
        new_name = orig_name

        # Drop placeholder-named nodes across ALL labels (e.g. Regulation "N/A")
        if _is_placeholder(orig_name):
            dropped_identities.add((label, orig_name))
            continue

        if label == "Department":
            lookup = orig_name.strip().lower()
            override = DEPT_LABEL_OVERRIDES.get(lookup)
            if override:
                new_label = override
            else:
                canonical = DEPT_ALIASES.get(lookup)
                if canonical:
                    new_name = canonical
                    props = dict(props)
                    props["access_level"] = DEPT_LEVEL_MAP[canonical]
                else:
                    dropped_identities.add((label, orig_name))
                    continue

        elif label == "Threshold":
            amount = props.get("amount")
            if amount is None:
                amount = merge_key.get("amount", orig_name)
            basis = props.get("basis")
            if basis is None:
                basis = merge_key.get("basis")

            if _is_placeholder(amount):
                dropped_identities.add((label, orig_name))
                continue
            if basis is not None and str(basis).strip() != "" and _is_placeholder(basis):
                dropped_identities.add((label, orig_name))
                continue
            norm_amount = _normalize_amount(amount)
            if norm_amount is None:
                dropped_identities.add((label, orig_name))
                continue

            props = dict(props)
            props["amount"] = norm_amount
            if norm_amount != str(amount).strip():
                new_name = norm_amount
                merge_key = dict(merge_key)
                if "amount" in merge_key:
                    merge_key["amount"] = norm_amount
                else:
                    merge_key[list(merge_key.keys())[0]] = norm_amount
                node["merge_key"] = merge_key
                node_rewrite[(label, orig_name)] = (label, new_name)

            t = str(props.get("type") or "").strip().lower()
            currency = str(props.get("currency") or "").strip().lower()
            basis_l = basis.strip().lower() if isinstance(basis, str) else ""
            if t == "time":
                pass
            elif currency == "time" or basis_l in TIME_UNITS:
                t = "time"
            elif currency:
                t = "monetary"
            else:
                t = "count"
            props["type"] = t
            if t != "monetary":
                props.pop("currency", None)

        if new_label != label:
            node["label"] = new_label
        if new_name != orig_name:
            merge_key = dict(merge_key)
            merge_key[list(merge_key.keys())[0]] = new_name
            node["merge_key"] = merge_key

        node["properties"] = {k: v for k, v in props.items() if _valid_prop_key(k)}
        clean_nodes.append(node)
        node_rewrite[(label, orig_name)] = (new_label, new_name)

    def _rewrite_endpoint(endpoint_label, endpoint_key):
        if not isinstance(endpoint_key, dict) or not endpoint_key:
            return endpoint_label, endpoint_key
        ep_name = str(list(endpoint_key.values())[0])
        rewrite = node_rewrite.get((endpoint_label, ep_name))
        if rewrite:
            new_label, new_name = rewrite
            new_key = dict(endpoint_key)
            new_key[list(endpoint_key.keys())[0]] = new_name
            return new_label, new_key
        return endpoint_label, endpoint_key

    # Only edges to surviving nodes are kept. Policy/Clause are exempt because the
    # auto-backbone always supplies them (and may live in a different batch).
    surviving = {(n["label"], str(list(n["merge_key"].values())[0])) for n in clean_nodes}
    backbone_labels = {"Policy", "Clause"}

    clean_edges = []
    for edge in output.get("edges", []):
        if not isinstance(edge, dict):
            continue
        rel_type = edge.get("type")
        from_label = edge.get("from_label")
        from_key = edge.get("from_key")
        to_label = edge.get("to_label")
        to_key = edge.get("to_key")

        if not (
            rel_type in ALLOWED_REL_TYPES
            and from_label in ALLOWED_LABELS
            and to_label in ALLOWED_LABELS
            and isinstance(from_key, dict) and from_key
            and isinstance(to_key, dict) and to_key
        ):
            continue

        from_label, from_key = _rewrite_endpoint(from_label, from_key)
        to_label, to_key = _rewrite_endpoint(to_label, to_key)

        from_name = str(list(from_key.values())[0])
        to_name = str(list(to_key.values())[0])
        if (from_label, from_name) in dropped_identities or (to_label, to_name) in dropped_identities:
            continue
        # Drop dangling edges whose endpoint node does not survive in this output
        if from_label not in backbone_labels and (from_label, from_name) not in surviving:
            continue
        if to_label not in backbone_labels and (to_label, to_name) not in surviving:
            continue

        edge["from_label"] = from_label
        edge["from_key"] = from_key
        edge["to_label"] = to_label
        edge["to_key"] = to_key
        clean_edges.append(edge)

    return {"nodes": clean_nodes, "edges": clean_edges}


def _build_auto_backbone(
    chunk_id: str,
    doc_id: str,
    title: str,
    version: str,
    status: str,
    clause_ref: str,
    section_path: str,
    text_ref: str = "",
    access_level: int = 1,
) -> dict:
    """Auto-inject Policy and Clause backbone that must always exist."""
    return {
        "nodes": [
            {
                "label": "Policy",
                "merge_key": {"id": doc_id},
                "properties": {
                    "title": title,
                    "version": version,
                    "status": status,
                    "access_level": access_level,
                },
            },
            {
                "label": "Clause",
                "merge_key": {"id": chunk_id},
                "properties": {
                    "clause_ref": clause_ref,
                    "section_path": section_path,
                    "text_ref": text_ref[:300] if text_ref else chunk_id,
                    "access_level": access_level,
                },
            },
        ],
        "edges": [
            {
                "type": "PART_OF",
                "from_label": "Clause",
                "from_key": {"id": chunk_id},
                "to_label": "Policy",
                "to_key": {"id": doc_id},
            }
        ],
    }


def generate_graph_for_chunk(
    chunk_id: str,
    chunk_text: str,
    doc_id: str,
    title: str,
    section_path: str,
    clause_ref: str = "",
    version: str = "v1",
    status: str = "active",
    prev_text: str = "",
    next_text: str = "",
    access_level: int = 1,
) -> dict:
    """
    Call Qwen2.5-3B to generate Neo4j nodes and edges for a single chunk using context windowing.
    """
    backbone = _build_auto_backbone(
        chunk_id, doc_id, title, version, status, clause_ref, section_path, text_ref=chunk_text,
        access_level=access_level,
    )

    if not chunk_text or len(chunk_text.strip()) < 20:
        return backbone

    user_message = USER_PROMPT_TEMPLATE.format(
        title=title,
        doc_id=doc_id,
        version=version,
        status=status,
        access_level=access_level,
        chunk_id=chunk_id,
        clause_ref=clause_ref or "",
        section_path=section_path or "",
        prev_text=prev_text or "(None)",
        chunk_text=chunk_text,
        next_text=next_text or "(None)",
    )

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.0,
        "max_tokens": 2048,
        "stream": False,
    }

    raw = _call_llm(payload)

    json_str = _extract_json(raw)
    try:
        raw_output = json.loads(json_str)
    except json.JSONDecodeError:
        return backbone

    # Retry unmapped department names through the LLM before deterministic sanitize
    raw_output = _resolve_unmapped_departments(raw_output)

    # Sanitize node labels and edge types defensively
    output = _sanitize_graph(raw_output)

    # Deterministic threshold backstop: materialize thresholds the LLM missed
    output = _attach_thresholds(output, chunk_text)

    # Merge backbone with extracted graph
    merged = {"nodes": [], "edges": []}

    seen_keys = set()
    for node in backbone["nodes"] + output.get("nodes", []):
        label = node.get("label")
        merge_key = node.get("merge_key", {})
        if not label or not merge_key:
            continue
        key = (label, tuple(sorted(merge_key.items())))
        if key not in seen_keys:
            seen_keys.add(key)
            merged["nodes"].append(node)

    seen_edges = set()
    for edge in backbone["edges"] + output.get("edges", []):
        rel_type = edge.get("type")
        from_label = edge.get("from_label")
        from_key = str(edge.get("from_key", {}))
        to_label = edge.get("to_label")
        to_key = str(edge.get("to_key", {}))
        
        ekey = (rel_type, from_label, from_key, to_label, to_key)
        if ekey not in seen_edges:
            seen_edges.add(ekey)
            merged["edges"].append(edge)

    return merged


DOCUMENT_USER_PROMPT_TEMPLATE = """Document: "{title}" (id: {doc_id})
Document access_level: {access_level}
Total clauses in this document: {total}

All clauses in this document:
{chunk_list}

Ignore the "CURRENT CHUNK" rule in the system prompt. Extract entities from ALL clauses above.
Create each Role, Department, Process, Threshold, and Regulation node only ONCE across the entire document.
Do NOT create Policy or Clause nodes — they are auto-generated.
When creating GOVERNS relationships, use the Clause's chunk_id as from_key to link a Process to its governing clause.

Reason step-by-step following the CHAIN-OF-THOUGHT WORKFLOW, then output the strict JSON graph for the entire document inside a ```json fenced code block:"""


def _backbone_for_document(
    doc_id: str,
    title: str,
    version: str,
    status: str,
    chunks: List[Dict],
    access_level: int = 1,
) -> dict:
    """Generate backbone nodes and edges for all chunks in a document."""
    nodes = []
    edges = []

    # Single Policy node
    nodes.append({
        "label": "Policy",
        "merge_key": {"id": doc_id},
        "properties": {
            "title": title,
            "version": version,
            "status": status,
            "access_level": access_level,
        },
    })

    # Clause node + PART_OF edge per chunk
    for c in chunks:
        nodes.append({
            "label": "Clause",
            "merge_key": {"id": c["chunk_id"]},
            "properties": {
                "clause_ref": c.get("clause_ref", ""),
                "section_path": c.get("section_path", ""),
                "text_ref": (c.get("text") or "")[:300],
                "access_level": access_level,
            },
        })
        edges.append({
            "type": "PART_OF",
            "from_label": "Clause",
            "from_key": {"id": c["chunk_id"]},
            "to_label": "Policy",
            "to_key": {"id": doc_id},
        })

    # Canonical org departments — always present regardless of LLM extraction
    for name, level in sorted(DEPT_LEVEL_MAP.items(), key=lambda x: x[1]):
        nodes.append({
            "label": "Department",
            "merge_key": {"name": name},
            "properties": {"access_level": level},
        })

    return {"nodes": nodes, "edges": edges}


def _merge_graphs(graphs: List[dict]) -> dict:
    """Merge multiple graph dicts, deduplicating nodes and edges."""
    merged = {"nodes": [], "edges": []}
    seen_nodes = set()
    seen_edges = set()

    for g in graphs:
        for node in g.get("nodes", []):
            label = node.get("label")
            merge_key = node.get("merge_key", {})
            if not label or not merge_key:
                continue
            key = (label, tuple(sorted(merge_key.items())))
            if key not in seen_nodes:
                seen_nodes.add(key)
                merged["nodes"].append(node)

        for edge in g.get("edges", []):
            rel_type = edge.get("type")
            from_label = edge.get("from_label")
            from_key = str(edge.get("from_key", {}))
            to_label = edge.get("to_label")
            to_key = str(edge.get("to_key", {}))
            ekey = (rel_type, from_label, from_key, to_label, to_key)
            if ekey not in seen_edges:
                seen_edges.add(ekey)
                merged["edges"].append(edge)

    return merged


def _ensure_belongs_to(graph: dict) -> dict:
    """Deterministic fallback: link Roles to Departments when unambiguous.

    The LLM prompt emits BELONGS_TO edges directly. For roles the LLM missed,
    if the document references EXACTLY ONE canonical Department, link every
    Role without a BELONGS_TO edge to it. Multi-department documents are
    skipped (ambiguous) so we never invent a wrong membership.
    """
    departments = {
        str(list(n["merge_key"].values())[0])
        for n in graph.get("nodes", [])
        if n.get("label") == "Department" and n.get("merge_key")
    }
    if len(departments) != 1:
        return graph

    dept = next(iter(departments))
    roles = {
        str(list(n["merge_key"].values())[0])
        for n in graph.get("nodes", [])
        if n.get("label") == "Role" and n.get("merge_key")
    }
    if not roles:
        return graph

    linked = set()
    for e in graph.get("edges", []):
        if e.get("type") == "BELONGS_TO" and e.get("from_label") == "Role" and e.get("to_label") == "Department":
            linked.add(str(list(e["from_key"].values())[0]))

    missing = roles - linked
    if not missing:
        return graph

    for role in missing:
        graph.setdefault("edges", []).append({
            "type": "BELONGS_TO",
            "from_label": "Role",
            "from_key": {"name": role},
            "to_label": "Department",
            "to_key": {"name": dept},
        })
    return graph


def execute_graph_in_neo4j(graph: dict, driver) -> None:
    """Execute a merged graph dict against Neo4j in a single transaction per entity type batch."""
    if not graph:
        return

    try:
        with driver.session() as session:
            with session.begin_transaction() as tx:
                for node in graph.get("nodes", []):
                    label = node.get("label")
                    merge_key = node.get("merge_key", {})
                    props = node.get("properties", {})
                    if not label or not merge_key:
                        continue
                    merge_keys_list = list(merge_key.keys())
                    if not merge_keys_list:
                        continue
                    merge_prop = merge_keys_list[0]
                    all_props = {**merge_key, **props}
                    set_clause = ", ".join([f"n.{k} = ${k}" for k in all_props.keys()])
                    tx.run(
                        f"MERGE (n:{label} {{{merge_prop}: ${merge_prop}}}) SET {set_clause}",
                        **all_props,
                    )
                tx.commit()
    except Exception as e:
        import logging
        logging.getLogger("neo4j_exec").warning(f"Neo4j node batch failed: {e}")
        return

    try:
        with driver.session() as session:
            with session.begin_transaction() as tx:
                for edge in graph.get("edges", []):
                    rel_type = edge.get("type")
                    from_label = edge.get("from_label")
                    from_key = edge.get("from_key", {})
                    to_label = edge.get("to_label")
                    to_key = edge.get("to_key", {})
                    if not rel_type or not from_key or not to_key:
                        continue
                    from_keys_list = list(from_key.keys())
                    to_keys_list = list(to_key.keys())
                    if not from_keys_list or not to_keys_list:
                        continue
                    from_prop = from_keys_list[0]
                    to_prop = to_keys_list[0]
                    tx.run(
                        f"MATCH (a:{from_label} {{{from_prop}: $from_val}}) "
                        f"MATCH (b:{to_label} {{{to_prop}: $to_val}}) "
                        f"MERGE (a)-[:{rel_type}]->(b)",
                        from_val=from_key[from_prop],
                        to_val=to_key[to_prop],
                    )
                tx.commit()
    except Exception as e:
        import logging
        logging.getLogger("neo4j_exec").warning(f"Neo4j edge batch failed: {e}")


def generate_graph_for_document(
    doc_id: str,
    title: str,
    chunks: List[Dict],
    version: str = "v1",
    status: str = "active",
    max_chunks_per_call: int = 60,
    access_level: int = 1,
) -> dict:
    """
    Generate Neo4j graph for ALL chunks in a document using batched LLM calls.

    Sends chunks to Qwen2.5-3B in batches of max_chunks_per_call.
    Handles deduplication across batches and auto-generates backbone.

    Args:
        chunks: List of dicts with keys: chunk_id, text, clause_ref, section_path
        max_chunks_per_call: 40 for 16K ctx (batch), 30 for 16K ctx (agent)
        access_level: Document access level propagated to Policy/Clause nodes
    """
    backbone = _backbone_for_document(doc_id, title, version, status, chunks, access_level)

    valid_chunks = [c for c in chunks if c.get("text") and len(c["text"].strip()) >= 20]
    if not valid_chunks:
        return backbone

    # Build chunk list text
    def _format_chunks(chunk_batch):
        lines = []
        for c in chunk_batch:
            text_snippet = c["text"].strip().replace("\n", " ")[:250]
            lines.append(
                f'[{c.get("idx", 1)}] Clause "{c.get("clause_ref", "")}" '
                f'(id: {c.get("chunk_id", "")}) — "{text_snippet}"'
            )
        return "\n".join(lines)

    # Add index to each chunk
    for i, c in enumerate(valid_chunks):
        c["idx"] = i + 1

    def _generate_batch(batch, label):
        chunk_list = _format_chunks(batch)
        user_message = DOCUMENT_USER_PROMPT_TEMPLATE.format(
            title=title,
            doc_id=doc_id,
            access_level=access_level,
            total=len(batch),
            chunk_list=chunk_list,
        )
        if label:
            user_message += f"\n({label})"

        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.0,
            "max_tokens": 28000,
            "stream": False,
        }

        raw = _call_llm(payload)
        json_str = _extract_json(raw)
        try:
            raw_output = json.loads(json_str)
        except json.JSONDecodeError:
            return None

        raw_output = _resolve_unmapped_departments(raw_output)
        output = _sanitize_graph(raw_output)
        return _attach_thresholds(output, chunk_list)

    def _process_range(lo, hi, label):
        """Generate a graph for chunks[lo:hi]; on LLM/parse failure, split and retry."""
        output = _generate_batch(valid_chunks[lo:hi], label)
        if output is not None:
            return [output]
        if hi - lo > 1:
            mid = (lo + hi) // 2
            return (
                _process_range(lo, mid, f"{label} part 1/2" if label else "part 1/2")
                + _process_range(mid, hi, f"{label} part 2/2" if label else "part 2/2")
            )
        logging.getLogger("llm_graph").warning(
            "Graph generation failed for single clause chunk %s (doc %s); dropping it.",
            valid_chunks[lo].get("chunk_id"),
            doc_id,
        )
        return []

    total_batches = (len(valid_chunks) + max_chunks_per_call - 1) // max_chunks_per_call
    results = []
    for start in range(0, len(valid_chunks), max_chunks_per_call):
        batch_num = start // max_chunks_per_call + 1
        label = f"Batch {batch_num}/{total_batches}" if total_batches > 1 else ""
        results.extend(
            _process_range(start, min(start + max_chunks_per_call, len(valid_chunks)), label)
        )

    if not results:
        raise RuntimeError(
            f"LLM graph generation failed entirely for document {doc_id} ({title}); "
            "no batch produced a graph (server down?)."
        )

    results.append(backbone)
    return _ensure_belongs_to(_merge_graphs(results))