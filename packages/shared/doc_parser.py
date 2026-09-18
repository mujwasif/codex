"""
Unified document parser.
Extracts text + minimal formatting metadata, then uses LLM to identify
document structure (headings, content, tables).
Works for any .docx or .pdf regardless of internal format.
LLM is always the primary parser — no fallbacks.
"""

import os
import re
import json
import logging
from collections import defaultdict
from typing import List, Dict

logger = logging.getLogger(__name__)


# ── Extraction: DOCX ──────────────────────────────────────────────

def _extract_docx_formatted(file_path: str) -> str:
    """Extract DOCX text with minimal formatting markers.

    Interleaves paragraphs and tables in document order so the LLM
    sees content in its natural position.
    """
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(file_path)
    lines = []

    table_map = {}
    for tbl in doc.tables:
        rows = []
        for row in tbl.rows:
            cells = [cell.text.strip() for cell in row.cells]
            row_text = ' | '.join(c for c in cells if c)
            if row_text:
                rows.append(f"[Table] {row_text}")
        if rows:
            table_map[id(tbl._tbl)] = rows

    for child in doc.element.body:
        if child.tag == qn('w:p'):
            para = None
            for p in doc.paragraphs:
                if p._element is child:
                    para = p
                    break
            if para is None:
                continue
            text = para.text.strip()
            if not text:
                continue
            style = para.style.name if para.style and para.style.name else 'Normal'
            is_bold = any(run.bold for run in para.runs if run.bold)
            if 'Heading' in style:
                lines.append(f"[{style}] {text[:30]}")
            elif is_bold:
                lines.append(f"[Bold] {text[:30]}")
            else:
                lines.append(text[:30])
        elif child.tag == qn('w:tbl'):
            for tbl in doc.tables:
                if tbl._tbl is child:
                    lines.extend(table_map.get(id(tbl._tbl), []))
                    break

    return "\n".join(lines)


# ── Extraction: PDF ───────────────────────────────────────────────

def _extract_pdf_formatted(file_path: str) -> str:
    """Extract PDF text with minimal formatting markers.

    Includes [Page N] markers so the LLM and downstream code know
    which page each line came from.
    """
    import pdfplumber

    lines = []
    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
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
                    lines.append(f"[Page {page_num}, Bold, {size}pt] {text[:30]}")
                else:
                    lines.append(f"[Page {page_num}, {size}pt] {text[:30]}")

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


# ── LLM Analysis (primary for both DOCX and PDF) ─────────────────

STRUCTURE_PROMPT = """You are a document structure analyzer. Given document text with
formatting markers, identify the heading hierarchy and content paragraphs.

CRITICAL: The "text" field in your output must contain ONLY clean document text.
Remove ALL formatting markers ([Page ...], [Bold, ...], [Npt], etc.) from the text.

Markers provided to you (for YOUR analysis only — do NOT pass them through):
- [Heading N] = heading at level N (from Word styles — use N directly)
- [Bold] = bold text (likely a heading, table header, or sub-heading)
- [Table] = table row (pipe-delimited cells)
- [Page N, Bold, Npt] = bold text at N points on page N — use font size to classify
- [Page N, Npt] = regular text at N points on page N — use font size to classify
- [Bold, Npt] = bold text at N points (no page marker)
- [Npt] = regular text at N points
- No marker = plain body text

HEADING DETECTION (PDF documents):
1. Find the MOST COMMON font size — this is body text size.
2. Any text with font size LARGER than body = heading.
   Largest size → level 1. Second largest → level 2. Third → level 3. Etc.
3. BOLD text shorter than 80 chars = likely heading even if same size as body.
4. ALL CAPS short lines = likely heading.
5. Lines starting with numbered patterns (Article, Section, Rule, Clause, Chapter, Part, 1., 2., etc.) = likely heading if bold or large font.

Return a JSON array. Each element:
  {"type": "heading", "level": N, "text": "CLEAN text — no markers"}
  {"type": "content", "text": "CLEAN text — no markers"}

RULES:
1. [Heading N]: use N directly as the level.
2. [Bold] / [Bold, Npt]: heading if short (< 80 chars), starts a new topic, or numbered pattern.
3. ALL CAPS short lines are headings.
4. Group [Table] rows as a single content block.
5. Group related content under the same heading together.
6. Preserve ALL original text content — no summarizing — but STRIP all formatting markers.
7. IGNORE page numbers, footers, watermarks, document headers/footers, and test fixture notices.
8. Return ONLY the JSON array — no explanation text."""


def _llm_analyze_structure(formatted_text: str) -> List[Dict]:
    """Send formatted text to LLM for structure analysis.

    Processes in page-sized batches for large documents.
    Works for both DOCX and PDF formatted text.
    """
    from services.agents.tools.llm_tools import llm_generate, INGESTION_MODEL

    BATCH_CHAR_LIMIT = 20000
    all_items = []

    batches = []
    if len(formatted_text) <= BATCH_CHAR_LIMIT:
        batches = [formatted_text]
    else:
        page_splits = re.split(r'\n(?=\[Page \d+)', formatted_text)
        if len(page_splits) <= 1:
            chunks = [formatted_text[i:i+BATCH_CHAR_LIMIT] for i in range(0, len(formatted_text), BATCH_CHAR_LIMIT)]
            batches = chunks
        else:
            for page_text in page_splits:
                if batches and len(batches[-1]) + len(page_text) < BATCH_CHAR_LIMIT:
                    batches[-1] += "\n" + page_text
                else:
                    batches.append(page_text)

    for batch in batches:
        result = llm_generate(
            model=INGESTION_MODEL,
            system_prompt=STRUCTURE_PROMPT,
            user_message=f"Document text with formatting:\n\n{batch}",
            temperature=0.0,
            max_tokens=4096,
            timeout=180.0,
        )

        if not result.success:
            logger.warning(f"LLM structure analysis failed: {result.error}")
            continue

        text = result.data.strip()
        fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        json_str = fenced[-1].strip() if fenced else text

        try:
            items = json.loads(json_str)
            if isinstance(items, list):
                all_items.extend(items)
        except json.JSONDecodeError:
            match = re.search(r"\[.*?\]\s*$", text, re.DOTALL)
            if match:
                try:
                    items = json.loads(match.group())
                    if isinstance(items, list):
                        all_items.extend(items)
                except json.JSONDecodeError:
                    pass

    return all_items


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
        level = min(int(item.get("level", 1)), len(section_counter))

        page = 0
        page_match = re.match(r"\[Page (\d+)", text)
        if page_match:
            page = int(page_match.group(1))
        # Strip ALL formatting markers from text — they are for analysis only
        text = re.sub(r'\[Page \d+(?:,\s*(?:Bold,\s*)?\d+pt)?\]\s*', '', text)
        text = re.sub(r'\[Bold(?:,\s*\d+pt)?\]\s*', '', text)
        text = re.sub(r'\[Heading \d+\]\s*', '', text)
        text = re.sub(r'\[Table\]\s*', '', text)
        text = re.sub(r'\[\d+pt\]\s*', '', text)
        text = text.strip()
        if not text:
            continue

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
                "page": page,
            })
        else:
            sections.append({
                "section_path": current_path or "0",
                "heading_hierarchy": current_hierarchy,
                "content": text,
                "section_level": len(heading_stack),
                "is_heading": False,
                "is_table": False,
                "page": page,
            })

    return sections


def _strip_markers(text: str) -> str:
    """Remove all formatting markers from text."""
    text = re.sub(r'\[Page \d+(?:,\s*(?:Bold,\s*)?\d+pt)?\]\s*', '', text)
    text = re.sub(r'\[Bold(?:,\s*\d+pt)?\]\s*', '', text)
    text = re.sub(r'\[Heading \d+\]\s*', '', text)
    text = re.sub(r'\[Table\]\s*', '', text)
    text = re.sub(r'\[\d+pt\]\s*', '', text)
    return text.strip()


# ── Main Entry Point ──────────────────────────────────────────────

def parse_document_structure(file_path: str) -> List[Dict]:
    """Parse any .docx or .pdf document.

    Extracts text + minimal formatting metadata, sends to LLM for
    structure analysis, returns section dicts for the chunker.
    LLM is always the primary parser.
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

    items = _llm_analyze_structure(formatted)

    if items:
        sections = _build_sections(items)
        if sections:
            return sections

    # ── Fallback: font-size aware heading detection ───────────────
    paragraphs = [p.strip() for p in formatted.split('\n') if p.strip()]

    size_counts = {}
    bold_sizes = set()
    for para in paragraphs:
        size_match = re.search(r'(\d+)pt\]', para)
        if size_match:
            sz = int(size_match.group(1))
            size_counts[sz] = size_counts.get(sz, 0) + 1
            if 'Bold' in para:
                bold_sizes.add(sz)

    body_size = max(size_counts, key=size_counts.get) if size_counts else 0
    sorted_sizes = sorted(size_counts.keys(), reverse=True)
    size_to_level = {}
    for idx, sz in enumerate(sorted_sizes):
        if sz > body_size:
            size_to_level[sz] = min(idx + 1, 3)

    numbered_pattern = re.compile(
        r'^(?:Article|Section|Rule|Clause|Chapter|Part|Schedule|Appendix)\s+\d+',
        re.IGNORECASE
    )
    roman_pattern = re.compile(
        r'^(?:PART|ANNEX)\s+[IVXLC]+',
        re.IGNORECASE
    )

    sections = []
    heading_stack = []
    section_counter = [0, 0, 0, 0, 0]

    for para in paragraphs:
        clean = _strip_markers(para)
        if not clean or len(clean.split()) < 3:
            continue

        is_heading = False
        heading_level = 1

        m = re.match(r'\[Heading (\d+)', para)
        if m:
            is_heading = True
            heading_level = int(m.group(1))
        elif re.match(r'\[Bold(?:,\s*\d+pt)?\]', para) and not body_size:
            is_heading = True
            heading_level = 2
        elif body_size:
            size_match = re.search(r'Bold,\s*(\d+)pt\]', para)
            if size_match:
                sz = int(size_match.group(1))
                if sz in size_to_level:
                    is_heading = True
                    heading_level = size_to_level[sz]
                elif sz > body_size:
                    is_heading = True
                    heading_level = 1
            if not is_heading and numbered_pattern.match(clean):
                is_heading = True
                heading_level = 3
            if not is_heading and roman_pattern.match(clean):
                is_heading = True
                heading_level = 1
        else:
            if numbered_pattern.match(clean) or roman_pattern.match(clean):
                is_heading = True
                heading_level = 2

        if is_heading:
            for j in range(heading_level, len(section_counter)):
                section_counter[j] = 0
            section_counter[heading_level - 1] += 1
            section_path = ".".join(
                str(section_counter[j]) for j in range(heading_level) if section_counter[j] > 0
            )
            heading_stack = [(l, t) for l, t in heading_stack if l < heading_level]
            heading_stack.append((heading_level, clean))
            sections.append({
                "section_path": section_path,
                "heading_hierarchy": [t for l, t in heading_stack],
                "content": clean,
                "section_level": heading_level,
                "is_heading": True,
                "is_table": False,
                "page": 0,
            })
        else:
            current_path = ".".join(
                str(section_counter[j]) for j in range(len(section_counter)) if section_counter[j] > 0
            ) or "0"
            hierarchy = [t for l, t in heading_stack] if heading_stack else ["Document"]
            sections.append({
                "section_path": current_path,
                "heading_hierarchy": hierarchy,
                "content": clean,
                "section_level": len(heading_stack),
                "is_heading": False,
                "is_table": '[Table]' in para,
                "page": 0,
            })
    return sections
