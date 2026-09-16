"""
Unified document parser.
Extracts text + minimal formatting metadata, then uses LLM to identify
document structure (headings, content, tables).
Works for any .docx or .pdf regardless of internal format.
"""

import os
import re
import json
from collections import defaultdict
from typing import List, Dict


# ── Extraction: DOCX ──────────────────────────────────────────────

def _extract_docx_formatted(file_path: str) -> str:
    """Extract DOCX text with minimal formatting markers.

    Only includes:
    - [Heading N] for heading-styled paragraphs
    - [Bold] for bold non-heading paragraphs
    - Plain text for everything else
    - [Table] for table rows
    """
    from docx import Document

    doc = Document(file_path)
    lines = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        style = para.style.name if para.style else 'Normal'
        is_bold = any(run.bold for run in para.runs if run.bold)

        if 'Heading' in style:
            lines.append(f"[{style}] {text}")
        elif is_bold:
            lines.append(f"[Bold] {text}")
        else:
            lines.append(text)

    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            row_text = ' | '.join(c for c in cells if c)
            if row_text:
                lines.append(f"[Table] {row_text}")

    return "\n".join(lines)


# ── Extraction: PDF ───────────────────────────────────────────────

def _extract_pdf_formatted(file_path: str) -> str:
    """Extract PDF text with minimal formatting markers.

    Only includes:
    - [Bold, Npt] for bold lines
    - [Npt] for non-bold lines (size rounded to integer)
    """
    import pdfplumber

    lines = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(extra_attrs=['size', 'fontname'])
            if not words:
                continue

            by_top = defaultdict(list)
            for w in words:
                by_top[round(w['top'], 0)].append(w)

            for top in sorted(by_top.keys()):
                ws = by_top[top]
                text = " ".join(w['text'] for w in ws).strip()
                text = re.sub(r'\(cid:\d+\)', '', text).strip()
                if not text:
                    continue

                size = round(max(w['size'] for w in ws))
                is_bold = any('Bold' in w.get('fontname', '') for w in ws)

                if is_bold:
                    lines.append(f"[Bold, {size}pt] {text}")
                else:
                    lines.append(f"[{size}pt] {text}")

    return "\n".join(lines)


# ── Extraction: Dispatcher ────────────────────────────────────────

def _extract_with_formatting(file_path: str) -> str:
    """Dispatch to format-specific extraction."""
    ext = os.path.splitext(file_path)[1].lower()

    if ext == '.docx':
        return _extract_docx_formatted(file_path)
    elif ext == '.pdf':
        return _extract_pdf_formatted(file_path)
    else:
        raise ValueError(f"Unsupported format: {ext}")


# ── LLM Analysis ──────────────────────────────────────────────────

STRUCTURE_PROMPT_DOCX = """You are a document structure analyzer for a Microsoft Word document.
Each line has a formatting marker showing its Word style.

Markers:
- [Heading N] = heading at level N (this is the EXACT heading level — use it directly)
- [Bold] = bold text (likely a table header, sub-heading, or label)
- [Table] = table row (pipe-delimited cells)
- No marker = plain body text

Return a JSON array. Each element:
  {"type": "heading", "level": N, "text": "..."}
  {"type": "content", "text": "..."}

RULES:
1. Use [Heading N] directly — the N tells you the level. [Heading 1] = level 1, [Heading 2] = level 2, etc.
2. [Bold] text that introduces a new topic is a heading — assign level based on context (usually one level deeper than the last heading).
3. Group [Table] rows together as a single content block with all rows.
4. Group related content paragraphs together (numbered sub-clauses under the same heading).
5. Preserve ALL original text — do not summarize.
6. Ignore page numbers, footers, and watermarks.
7. Return ONLY the JSON array."""

STRUCTURE_PROMPT_PDF = """You are a document structure analyzer for a PDF document.
Each line has a font size marker showing its visual prominence.

Markers:
- [Bold, Npt] = bold text at N points (likely a heading)
- [Npt] = regular text at N points
- No marker = plain body text

Return a JSON array. Each element:
  {"type": "heading", "level": N, "text": "..."}
  {"type": "content", "text": "..."}

RULES:
1. Headings have LARGER font sizes than body text. Compare sizes across the document:
   - Largest size = level 1 (e.g., document title, PART/CHAPTER markers)
   - Second largest = level 2 (e.g., Article/Section/Rule)
   - Third largest = level 3 (e.g., sub-sections)
   - Body text is the most common size — it is NOT a heading
2. [Bold, Npt] text is likely a heading, especially if it's short (< 80 chars).
3. Look for numbered patterns: "Article N", "Section N", "PART X", "Rule N", "Clause N" — these are headings.
4. ALL CAPS short lines are likely headings.
5. Group related sentences into content blocks — complete paragraphs or sets of sub-clauses.
6. Preserve ALL original text — no summarizing.
7. Ignore page numbers, footers, watermarks, and test fixture notices.
8. If there are no clear headings, return all text as content blocks.
9. Return ONLY the JSON array."""


def _llm_analyze_structure(formatted_text: str, fmt: str = "docx") -> List[Dict]:
    """Send formatted text to LLM, return parsed JSON array."""
    import logging
    from services.agents.tools.llm_tools import llm_generate, INGESTION_MODEL

    prompt = STRUCTURE_PROMPT_DOCX if fmt == "docx" else STRUCTURE_PROMPT_PDF
    sample = formatted_text[:8000]

    result = llm_generate(
        model=INGESTION_MODEL,
        system_prompt=prompt,
        user_message=f"Document text with formatting:\n\n{sample}",
        temperature=0.0,
        max_tokens=4096,
        timeout=60.0,
    )

    if not result.success:
        logging.getLogger("doc_parser").warning(f"LLM structure analysis failed: {result.error}")
        return []

    text = result.data.strip()
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    json_str = fenced[-1].strip() if fenced else text

    try:
        items = json.loads(json_str)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                items = json.loads(match.group())
            except json.JSONDecodeError:
                return []
        else:
            return []

    return items if isinstance(items, list) else []


# ── Section Builder ───────────────────────────────────────────────

def _build_sections(items: List[Dict]) -> List[Dict]:
    """Convert LLM JSON output to section dicts (compatible with structure_chunker)."""
    sections = []
    heading_stack = []
    section_counter = [0, 0, 0, 0, 0]
    current_path = "0"
    current_hierarchy = ["Document"]

    for item in items:
        text = item.get("text", "").strip()
        if not text:
            continue

        item_type = item.get("type", "content")
        level = int(item.get("level", 1))

        if item_type == "heading":
            for j in range(level, len(section_counter)):
                section_counter[j] = 0
            section_counter[level - 1] += 1

            section_path = ".".join(
                str(section_counter[j]) for j in range(level) if section_counter[j] > 0
            )

            heading_stack = [(l, t) for l, t in heading_stack if l < level]
            heading_stack.append((level, text))
            heading_hierarchy = [t for l, t in heading_stack]

            current_path = section_path
            current_hierarchy = heading_hierarchy

            sections.append({
                "section_path": section_path,
                "heading_hierarchy": heading_hierarchy,
                "content": text,
                "section_level": level,
                "is_heading": True,
                "is_table": False,
                "page": 0,
            })
        else:
            sections.append({
                "section_path": current_path or "0",
                "heading_hierarchy": current_hierarchy,
                "content": text,
                "section_level": len(heading_stack),
                "is_heading": False,
                "is_table": False,
                "page": 0,
            })

    return sections


# ── Main Entry Point ──────────────────────────────────────────────

def parse_document_structure(file_path: str) -> List[Dict]:
    """Parse any .docx or .pdf document.

    Extracts text + minimal formatting metadata, sends to LLM for
    structure analysis, returns section dicts for the chunker.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in ('.docx', '.pdf'):
        raise ValueError(f"Unsupported format: {ext}")

    try:
        formatted = _extract_with_formatting(file_path)
    except Exception as e:
        raise ValueError(f"Failed to extract text: {e}")

    if not formatted.strip():
        return []

    fmt = "docx" if ext == ".docx" else "pdf"
    items = _llm_analyze_structure(formatted, fmt=fmt)

    if items:
        sections = _build_sections(items)
        if sections:
            return sections

    return [{
        "section_path": "0",
        "heading_hierarchy": ["Document"],
        "content": formatted[:50000],
        "section_level": 0,
        "is_heading": False,
        "is_table": False,
        "page": 0,
    }]
