import re

def verify_citations(answer, context_chunks):
    """
    Simple NLI/Regex verifier to ensure every bracketed citation [Doc: X, Clause: Y]
    actually exists in the retrieved chunks.
    """
    citations_found = re.findall(r"\[Doc: (.*?), Clause: (.*?)\]", answer)
    
    # Create a set of all valid doc/clause pairs from the retrieval stage
    valid_pairs = { (c['title'], c.get('clause_ref', 'N/A')) for c in context_chunks }
    
    for doc, clause in citations_found:
        if (doc, clause) not in valid_pairs:
            return False, f"Invalid citation found: {doc} {clause}"
            
    return True, "All citations verified."
