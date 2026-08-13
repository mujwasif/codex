import re

def verify_citations(answer, context_chunks):
    """
    Simple NLI/Regex verifier to ensure every bracketed citation [Doc: X, Clause: Y]
    actually exists in the retrieved chunks.

    Matching is lenient about clause references: the LLM often bundles several
    rules into one bracket (e.g. "9 Reference > Rule 10 and Rule 9"), so a citation
    is accepted if its clause_ref equals, contains, or is contained by a retrieved
    chunk's clause_ref within the same source document.
    """
    citations_found = re.findall(r"\[Doc: (.*?), Clause: (.*?)\]", answer)
    if not citations_found:
        return True, "No citations to verify."

    # Valid (title, clause_ref) pairs from the retrieval stage.
    valid_pairs = { (c.get('title'), c.get('clause_ref', 'N/A')) for c in context_chunks }

    def _clause_matches(cited: str) -> bool:
        for (title, clause) in valid_pairs:
            if title != cited_doc:
                continue
            if clause == cited:
                return True
            # The LLM bundles several rules into one bracket
            # ("9 Reference > Rule 10 and Rule 9") — accept if the cited
            # clause contains a retrieved clause at a token boundary so a
            # shorter ref ("Rule 9") cannot false-positive inside "Rule 99".
            if re.search(rf"(?:^|[^a-zA-Z0-9]){re.escape(clause)}(?:[^a-zA-Z0-9]|$)", cited):
                return True
        return False

    for cited_doc, cited_clause in citations_found:
        matched = _clause_matches(cited_clause)
        if not matched:
            return False, f"Invalid citation found: {cited_doc} {cited_clause}"

    return True, "All citations verified."
