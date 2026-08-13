"""
Low-information / garbage clause detection for retrieval hygiene.

During ingestion the clause-detection LLM occasionally leaks its own
system prompt or chain-of-thought reasoning into extracted clauses, and
section scanners pick up document table headers/footers and bare
cross-reference lines (e.g. a "Related Documents" entry that only names
another policy). Such chunks pollute the retrieval index and can outrank
real policy requirements.

is_low_info() is the single source of truth used by:
  - BM25Index build (skip rows)
  - vector_search (Python filter after SQL overfetch)

Heuristic categories:
  1. Prompt / CoT leakage  -> the LLM's own instructions copied verbatim
  2. Table / header junk   -> "Issued Reviewed Approved", dates, "<date>"
  3. Stubs                 -> too short to carry a requirement:
       - short chunks WITHOUT a requirement verb (must/shall/will/should)
         are bare reference lines / fragments (e.g. "<Company> Password
         Management Policy") and are dropped;
       - short chunks WITH a requirement verb are real one-liners
         ("Each password must be at least 12 characters long.") and kept.

Note: the phrase "chain-of-thought workflow" is legitimate policy content
in this corpus (documents genuinely mandate CoT workflows). Only the full
prompt fragment ("... (reason step-by-step first, then emit the JSON array)")
is treated as leakage.
"""

import re

# Prompt / chain-of-thought leakage markers (lowercased substrings).
PROMPT_LEAK_MARKERS = (
    "each clause must be a single requirement",
    "keep all original wording",
    "reason step-by-step first, then emit the json",
    "then emit the json array",
    "do not add any text after the closing fence",
    "output only the final json array",
    "list of layers",
    "chain-of-thought workflow (reason step-by-step",
)

# Pure table/header junk: a first line that STARTS with one of these table
# keywords and carries no requirement verb (e.g. version-control rows like
# "Date of Next Revision | <date>" or "Issued Reviewed Approved").
TABLE_HEADER_RE = re.compile(
    r"^(issued|reviewed|approved|granted|final|signature|name|date|status|"
    r"document owner|date of next revision|version|change date|author|"
    r"unique market reference)\b",
    re.IGNORECASE,
)

# A requirement verb signals a genuine clause even when very short.
REQUIREMENT_RE = re.compile(
    r"\b(must|shall|will|should|required|prohibited|mandatory|may not|"
    r"must not|shall not|ensure|permitted|allowed)\b",
    re.IGNORECASE,
)

# Minimum tokens for a clause that HAS a requirement verb.
MIN_VERB_CLAUSE_TOKENS = 3
# Minimum tokens for a clause WITHOUT a requirement verb. Set low enough to
# keep imperative action clauses ("Identify the authorized party...",
# "Set up security configurations...") while still dropping bare references
# ("<Company> Password Management Policy") and single-word table debris.
MIN_PLAIN_CLAUSE_TOKENS = 6


def is_low_info(clause_ref: str, text: str) -> bool:
    """
    True if a chunk should be excluded from retrieval / the BM25 index.

    Args:
        clause_ref: section path / clause reference (e.g. "5.1 Policy > ...").
        text: chunk body.

    Returns:
        bool
    """
    body = (text or "").strip()
    if not body:
        return True

    lowered = body.lower()
    words = body.split()

    # 1. Prompt / CoT leakage
    for marker in PROMPT_LEAK_MARKERS:
        if marker in lowered:
            return True

    # 2. Table / header junk: a line that starts with a table keyword and
    #    carries no requirement verb is a version-control row / footer, not
    #    a clause (e.g. "Date of Next Revision | <date>").
    first_line = body.splitlines()[0].strip()
    if TABLE_HEADER_RE.match(first_line):
        has_verb = REQUIREMENT_RE.search(lowered) is not None
        if not has_verb and ("<" in body or len(words) <= 8):
            return True

    # 3. Stub check: real short clauses carry a requirement verb; short
    #    fragments without one (bare references, list debris) are dropped.
    has_verb = REQUIREMENT_RE.search(lowered) is not None
    if has_verb:
        return len(words) < MIN_VERB_CLAUSE_TOKENS
    return len(words) < MIN_PLAIN_CLAUSE_TOKENS
