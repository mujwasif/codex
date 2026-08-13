"""
Multi-signal confidence scoring for grounded answers.

confidence = (
    sigmoid(top_reranker_score)   × 0.40  (reranker strength)
  + citation_coverage             × 0.35  (valid citations / total citations)
  + chunk_availability            × 0.25  (min(len(chunks)/5, 1))
) × 100  → percentage (0.0 - 100.0)
"""

import math
import re


def compute_confidence(chunks, is_valid, answer_text):
    """
    Compute confidence score as a percentage (0-100).

    Weights:
    - Reranker score normalized by sigmoid (40%)
    - Citation coverage (35%)
    - Chunk availability (25%)
    """
    # 1. Reranker score → sigmoid normalization (40%)
    if chunks:
        top_score = chunks[0].get("score", 0.0)
        reranker_norm = 1.0 / (1.0 + math.exp(-top_score))
    else:
        reranker_norm = 0.0

    # 2. Citation coverage (35%)
    citations_found = re.findall(r"\[Doc: (.*?), Clause: (.*?)\]", answer_text)
    if citations_found:
        valid_pairs = {(c['title'], c.get('clause_ref', 'N/A')) for c in chunks}
        valid_count = sum(1 for doc, clause in citations_found if (doc, clause) in valid_pairs)
        citation_coverage = valid_count / len(citations_found)
    elif "Insufficient" in answer_text:
        citation_coverage = 1.0  # Correct abstention
    else:
        citation_coverage = 0.0  # No citations, not abstaining

    # 3. Chunk availability (25%)
    chunk_avail = min(len(chunks) / 5.0, 1.0)

    # Weighted combination
    confidence = (reranker_norm * 0.40 + citation_coverage * 0.35 + chunk_avail * 0.25)

    # Convert to percentage, round to 1 decimal
    return round(confidence * 100, 1)
