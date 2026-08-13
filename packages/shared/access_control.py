"""
Access-level inference and enforcement for the Codex RBAC layer.

Access levels:
    1 = employee   (sees only level-1 documents)
    2 = manager    (sees level-1 and level-2 documents)
    3 = admin      (sees all documents)

Inference priority (highest first):
    1. Explicit level tag on the document (e.g. "level:3", "access_level:2", "restricted")
    2. Sensitivity keywords in the filename/title
    3. Sensitivity keywords in the document body (first SAMPLE_CHARS)
    4. Default level (1)

Tag matching is case-insensitive and substring-based, so a tag like
"Confidential" or "confidential:true" is recognized.
"""

from typing import Iterable, List, Optional

DEFAULT_ACCESS_LEVEL = 1
MIN_ACCESS_LEVEL = 1
MAX_ACCESS_LEVEL = 3

# Words in the title/filename that push a document to the admin level.
LEVEL_3_TITLE_KEYWORDS = (
    "confidential",
    "restricted",
    "secret",
    "top-secret",
    "classified",
    "salary",
    "compensation",
    "bonus",
    "termination",
    "litigation",
    "legal",
    "audit-finding",
    "executive",
)

# Words in the title/filename that push a document to the manager level.
LEVEL_2_TITLE_KEYWORDS = (
    "manager",
    "management",
    "hr",
    "human-resource",
    "human resources",
    "finance",
    "financial",
    "payroll",
    "budget",
    "forecast",
    "strategy",
    "strategic",
    "board",
)

# Content-only keywords (same semantics as the title lists). These are checked
# against a sample of the document body, so they may include phrases that are
# unlikely to appear in a filename.
LEVEL_3_CONTENT_KEYWORDS = (
    "salary",
    "compensation",
    "bonus",
    "termination",
    "litigation",
    "confidential",
    "restricted",
    "classified",
)

LEVEL_2_CONTENT_KEYWORDS = (
    "payroll",
    "budget",
    "forecast",
    "performance review",
    "board of directors",
)

# How many characters of the document body to inspect for content keywords.
SAMPLE_CHARS = 5000

# Tags that explicitly encode a numeric level: "level:3", "access_level:2", "access:1"
_LEVEL_TAG_PATTERN = ("level", "access_level", "access", "sensitivity")

# Explicit sensitivity tags (exact, case-insensitive).
_EXPLICIT_TAG_LEVELS = {
    "public": 1,
    "internal": 1,
    "employee": 1,
    "manager": 2,
    "managerial": 2,
    "confidential": 3,
    "restricted": 3,
    "secret": 3,
    "classified": 3,
}


def clamp_access_level(level) -> int:
    """Coerce a value into a valid access level (1-3)."""
    try:
        value = int(level)
    except (TypeError, ValueError):
        return DEFAULT_ACCESS_LEVEL
    return max(MIN_ACCESS_LEVEL, min(MAX_ACCESS_LEVEL, value))


def can_access(user_level: int, document_level: int) -> bool:
    """
    A user with access level `user_level` may read a document whose access
    level is `document_level` iff user_level >= document_level.
    """
    return clamp_access_level(user_level) >= clamp_access_level(document_level)


def _title_matches_keywords(title: str, keywords: Iterable[str]) -> bool:
    title_lower = (title or "").lower()
    return any(kw in title_lower for kw in keywords)


def _content_matches_keywords(text: str, keywords: Iterable[str]) -> bool:
    sample = (text or "")[:SAMPLE_CHARS].lower()
    return any(kw in sample for kw in keywords)


def _explicit_level_from_tags(access_tags) -> Optional[int]:
    """
    Return a numeric access level encoded in an explicit tag, or None.

    Recognizes forms like:
        "level:3", "access_level=2", "access: 1", "sensitivity-3",
        "confidential", "restricted", "manager"

    A named tag that means "employee/internal" (level 1) is NOT treated as a
    binding declaration — the ingestion layer stamps `internal` on every
    document as a default, so it must not block keyword-based promotion.
    Use a numeric form ("level:1") to pin a document to level 1.
    """
    if not access_tags:
        return None
    if isinstance(access_tags, str):
        access_tags = [access_tags]

    for raw in access_tags:
        tag = str(raw).strip().lower()
        if not tag:
            continue

        # Explicit numeric forms: "level:3", "access_level=2", "sensitivity-3", "access3"
        for prefix in _LEVEL_TAG_PATTERN:
            if tag.startswith(prefix):
                remainder = tag[len(prefix):].lstrip(":=-_ \t")
                if remainder.isdigit():
                    return clamp_access_level(int(remainder))
                break

        # Bare "3" or "level 3"
        if tag.isdigit() and 1 <= int(tag) <= 3:
            return int(tag)

        # Named sensitivity tags — only levels 2 and 3 are binding
        for name, level in _EXPLICIT_TAG_LEVELS.items():
            if tag == name or tag.startswith(name + ":"):
                if level >= 2:
                    return level
                break

    return None


def infer_access_level(title: str, access_tags=None, full_text: str = "") -> int:
    """
    Infer the access level for a document.

    Priority: explicit tags > title keywords > body keywords > default(1).

    Args:
        title: Document title or filename.
        access_tags: Explicit tags attached to the document.
        full_text: Document body (only the first SAMPLE_CHARS are inspected).

    Returns:
        An integer access level in {1, 2, 3}.
    """
    explicit = _explicit_level_from_tags(access_tags)
    if explicit is not None:
        return explicit

    if _title_matches_keywords(title, LEVEL_3_TITLE_KEYWORDS):
        return 3
    if _title_matches_keywords(title, LEVEL_2_TITLE_KEYWORDS):
        return 2

    if _content_matches_keywords(full_text, LEVEL_3_CONTENT_KEYWORDS):
        return 3
    if _content_matches_keywords(full_text, LEVEL_2_CONTENT_KEYWORDS):
        return 2

    return DEFAULT_ACCESS_LEVEL
docker compose ps