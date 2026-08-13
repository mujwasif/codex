"""
Answer formatting for the human-facing response.

The LLM emits inline citation markers ([Doc: X, Clause: Y]) that the
verifier and confidence scorer parse for the cite-or-abstain guardrail.
Those markers are stripped here, at the API response boundary, so the
user sees clean natural prose; the structured citations array drives the
"Citations:" section rendered by the CLI, Slack, and Streamlit.

A normalizer also splits run-on paragraph lists ("1. A: x. 2. B: y.")
into proper Markdown lines so each item renders on its own line.
"""

import re
from typing import Optional

# [Doc: X, Clause: Y] / [Doc: X] / [Clause: Y] markers (non-greedy).
INLINE_CITATION_RE = re.compile(r"\[(?:Doc|Clause):[^\]]*\]")

# A numbered or bulleted list lead-in anchored inside a sentence, e.g.
#   "1. **Maximum Password Lifetime**: ..."   "2. Automatic Password Rotation: ..."
#   "- **Maximum Password Lifetime**: ..."     "• Automatic Password Rotation: ..."
# Groups: 1 = the list marker ("1. ", "- ", "• "), 2 = optional leading "**",
# 3 = the label run (word / digits / spaces &/' ' ; can split on . , ; - up to a
# colon), 4 = optional trailing "**". The label must end in a colon, which
# conserves so sentence-internal numbers and clause refs like "Clause 5.2" or
# "1.5 meters" do not false-trigger.
LEAD_IN_RE = re.compile(
    r"(?:^|\s)((?:\d+\.\s+)|[-+•]\s*)"
    r"(\*\*?)?"
    r"([A-Za-z0-9][A-Za-z0-9 &'\"()_,.;/-]{0,60}?)"
    r"(\*\*?)?\s*:"
)


def strip_inline_citations(answer: Optional[str]) -> Optional[str]:
    """Remove inline citation markers and tidy the leftover whitespace."""
    if not answer:
        return answer

    text = INLINE_CITATION_RE.sub("", answer)

    # Fix artifacts left by removal: "point. [Doc:..]" -> "point."
    text = re.sub(r"\s+([,.;:])\s*", r"\1 ", text)
    text = re.sub(r"[\s,;:.]{2,}$", "", text.strip())
    text = re.sub(r" {2,}", " ", text)

    return text.strip()


def _marker_for(lead: str) -> str:
    """Return the Markdown marker for a lead-in group ('1. ', '- ')."""
    if "-" in lead or "\u2022" in lead or "+" in lead:
        return "- "
    number = lead.rstrip().rstrip(".")
    return f"{number}. "


def normalize_numbered_lists(text: Optional[str]) -> Optional[str]:
    """
    Turn a run-on paragraph list into proper Markdown lines.

    "Intro. 1. **A**: x. 2. B: y. Outro." becomes:

        Intro.

        1. **A**: x.
        2. **B**: y.

        Outro.

    Every lead-in is placed on its own line and its label is force-wrapped
    in bold for consistency. Non-list prose is left untouched.
    """
    if not text:
        return text

    text = text.strip()
    if not text:
        return text

    def _repl(m):
        lead = m.group(1).strip()
        label = m.group(3).strip()
        marker = _marker_for(lead)
        # Always bold the label, regardless of what the model emitted.
        return f"\n{marker}**{label}**:"

    new_text = LEAD_IN_RE.sub(_repl, text)
    if new_text == text:
        return text

    # Collapse multiple spaces, then tidy the newlines we injected so each
    # item sits cleanly on its own line.
    new_text = re.sub(r"[ \t]+", " ", new_text)
    new_text = re.sub(r"\n ", "\n", new_text)
    new_text = re.sub(r" \n", "\n", new_text)
    new_text = re.sub(r"\n{3,}", "\n\n", new_text)
    new_text = re.sub(r" +\n", "\n", new_text)

    # Blank-line-separate a leading intro paragraph from the list, and the
    # list from a trailing outro paragraph, for cleaner Markdown rendering.
    new_text = re.sub(r"(:\s*)\n(?=[-•])", r":\n\n", new_text)
    new_text = re.sub(r"(:\s*)\n(?=\d+\. )", r":\n\n", new_text)

    return new_text.strip()


def format_answer(answer: Optional[str]) -> Optional[str]:
    """Full human-facing cleanup: strip citations, then normalize list layout."""
    return normalize_numbered_lists(strip_inline_citations(answer))