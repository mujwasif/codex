"""
Unified document parser.
Extracts text + minimal formatting metadata, then uses LLM to identify
document structure (headings, content, tables).
Works for any .docx or .pdf regardless of internal format.
LLM is always the primary parser — font-size heuristic is the fallback.
"""

import os
import re
import json
import logging
from collections import defaultdict
from typing import List, Dict

logger = logging.getLogger(__name__)

BATCH_CHAR_LIMIT = 11000


# ── Helpers ────────────────────────────────────────────────────────

def _strip_markers(text: str) -> str:
    """Remove all formatting markers from text."""
    text = re.sub(r'\[Page \d+(?:,\s*(?:Bold,\s*)?\d+pt)?\]\s*', '', text)
    text = re.sub(r'\[Bold(?:,\s*\d+pt)?\]\s*', '', text)
    text = re.sub(r'\[Heading \d+\]\s*', '', text)
    text = re.sub(r'\[Table\]\s*', '', text)
    text = re.sub(r'\[\d+pt\]\s*', '', text)
    return text.strip()


# ── Extraction: DOCX ──────────────────────────────────────────────

def _extract_docx_formatted(file_path: str) -> str:
    """Extract DOCX text with minimal formatting markers.

    Interleaves paragraphs and tables in document order so the LLM
    sees content in its natural position with full metadata.
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
                lines.append(f"[{style}] {text}")
            elif is_bold:
                lines.append(f"[Bold] {text}")
            else:
                lines.append(text)
        elif child.tag == qn('w:tbl'):
            for tbl in doc.tables:
                if tbl._tbl is child:
                    lines.extend(table_map.get(id(tbl._tbl), []))
                    break

    return "\n".join(lines)


# ── Extraction: PDF ───────────────────────────────────────────────

def _extract_pdf_formatted(file_path: str) -> str:
    """Extract PDF text with minimal formatting markers.

    Includes [Page N] markers with font metadata for heading detection.
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
                    lines.append(f"[Page {page_num}, Bold, {size}pt] {text}")
                else:
                    lines.append(f"[Page {page_num}, {size}pt] {text}")

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

_CLAUSE_SUFFIX = """\
CLAUSE RULES:
1. Each clause must be a SINGLE requirement, prohibition, or permission.
2. Each clause MUST be between 20 and 70 words. Split longer paragraphs into multiple clauses.
3. CRITICAL: Copy text EXACTLY as written. Do NOT reword, rephrase, rephrase, or change any words. The "text" field must be a verbatim copy from the source document.
4. Do NOT merge multiple lines or bullet points into one clause.
5. Do NOT add introductory phrases like "The purpose of this policy is to" if the original text doesn't start with that.
6. If a paragraph is already a single rule within 20-70 words, return it as one clause.

BAD EXAMPLES (paraphrased — DO NOT DO THIS):
- Original: "Ensuring the effectiveness of controls"
  WRONG output: "The purpose of this policy is to ensure the effectiveness of controls" ← ADDED TEXT
- Original: "Protecting the confidentiality of assets"
  WRONG output: "The policy provides a framework for protecting the confidentiality of assets" ← CHANGED WORDS

GOOD EXAMPLES (verbatim — DO THIS):
- Original: "Ensuring the effectiveness of controls"
  CORRECT output: "Ensuring the effectiveness of controls" ← EXACT COPY, WORD FOR WORD

OUTPUT FORMAT:
Return a JSON array of objects. Each object has:
- "type": "heading" or "clause"
- "level": heading level (1-6) — only for headings
- "text": the text content — must be VERBATIM from the source, strip all formatting markers but do NOT change the words
- "hierarchy": array of ancestor heading texts — only for clauses

Example:
[
  {"type": "heading", "level": 1, "text": "Password Policy"},
  {"type": "clause", "text": "All passwords must be at least 12 characters long.", "hierarchy": ["Password Policy"]},
  {"type": "heading", "level": 2, "text": "Scope"},
  {"type": "clause", "text": "This policy applies to all employees.", "hierarchy": ["Password Policy", "Scope"]}
]

RULES:
1. Return ONLY the JSON array — no explanation text.
2. Preserve ALL heading text — no summarizing.
3. Every non-heading line must become a clause — do not skip content.
4. NEVER paraphrase. NEVER reword. NEVER add words that aren't in the source."""


PDF_PROMPT = """You are a PDF policy document parser. You have TWO jobs:
1. Identify headings and their levels from font size metadata.
2. Split content into individual clauses (rules/requirements).

METADATA MARKERS (for your analysis only — do NOT pass them through):
- [Page N, Bold, Npt] = bold text at N points on page N
- [Page N, Npt] = regular text at N points on page N
- [Table] = table row (not a heading)

HEADING DETECTION:
1. Find the MOST COMMON font size — this is body text size.
2. Text with font size LARGER than body = heading.
   Largest → level 1. Second largest → level 2. Third → level 3.
3. BOLD text at larger size = likely heading.
4. ALL CAPS short lines = likely heading.
5. Numbered patterns (Article, Section, Rule, Clause, Chapter, Part) = likely heading.
6. IGNORE page numbers, footers, watermarks.

TABLE RULES:
- [Table] rows are NOT headings.
- The first [Table] row is the column header. For each subsequent row,
  merge the header labels with the row values.
  Example: Header "Standard | Fee | Risk" + Row "High | 100000 | Yes"
  → clause text: "Standard: High | Fee: 100000 | Risk: Yes"
- If a table has no clear header row, join cells with " | ".

""" + _CLAUSE_SUFFIX


DOCX_PROMPT = """You are a DOCX policy document parser. You have TWO jobs:
1. Identify headings and their levels from Word style markers.
2. Split content into individual clauses (rules/requirements).

METADATA MARKERS (for your analysis only — do NOT pass them through):
- [Heading N] = heading at level N (from Word styles — use N directly)
- [Bold] = bold text (possibly a heading)
- [Table] = table row (not a heading)

HEADING DETECTION:
1. [Heading N]: use N directly as the level.
2. [Bold]: heading if it starts a new topic or uses a numbered pattern.
3. [Table] rows are NOT headings.

TABLE RULES:
- [Table] rows are NOT headings.
- The first [Table] row is the column header. For each subsequent row,
  merge the header labels with the row values.
  Example: Header "Name | Role | Department" + Row "John | Manager | Finance"
  → clause text: "Name: John | Role: Manager | Department: Finance"
- If a table has no clear header row, join cells with " | ".

""" + _CLAUSE_SUFFIX


def _llm_analyze_structure(formatted_text: str, ext: str = ".pdf") -> List[Dict]:
    """Send formatted text to LLM for structure analysis.

    Processes in page-sized batches for large documents.
    Returns both headings and clauses with hierarchy.
    """
    from services.agents.tools.llm_tools import llm_generate, INGESTION_MODEL

    all_items = []

    batches = []
    if len(formatted_text) <= BATCH_CHAR_LIMIT:
        batches = [formatted_text]
    else:
        page_splits = re.split(r'\n(?=\[Page \d+)', formatted_text)
        if len(page_splits) <= 1:
            batches = [
                formatted_text[i:i+BATCH_CHAR_LIMIT]
                for i in range(0, len(formatted_text), BATCH_CHAR_LIMIT)
            ]
        else:
            for page_text in page_splits:
                if batches and len(batches[-1]) + len(page_text) < BATCH_CHAR_LIMIT:
                    batches[-1] += "\n" + page_text
                else:
                    batches.append(page_text)

    for batch_num, batch in enumerate(batches, 1):
        label = f"batch {batch_num}/{len(batches)}" if len(batches) > 1 else ""
        system_prompt = PDF_PROMPT if ext == ".pdf" else DOCX_PROMPT
        result = llm_generate(
            model=INGESTION_MODEL,
            system_prompt=system_prompt,
            user_message=f"Document text with formatting:\n\n{batch}",
            temperature=0.0,
            max_tokens=7000,
            timeout=180.0,
        )

        if not result.success:
            logger.warning(f"LLM structure analysis failed ({label}): {result.error}")
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

def _build_sections_hybrid(items: List[Dict], raw_lines: List[str]) -> List[Dict]:
    """Build sections by matching LLM-detected headings to raw extracted lines.

    The LLM returns ONLY heading items. Every non-heading line becomes a
    separate content item. This guarantees 1:1 line-to-section mapping
    with zero content loss.
    """
    heading_map = {}
    for item in items:
        if item.get("type") != "heading":
            continue
        text = _strip_markers(item.get("text", "")).strip()
        if not text:
            continue
        heading_map[text] = int(item.get("level", 1))

    sections = []
    heading_stack = []
    section_counter = [0, 0, 0, 0, 0]
    matched_headings = set()

    for line in raw_lines:
        clean = _strip_markers(line).strip()
        if not clean or len(clean.split()) < 3:
            continue

        page = 0
        page_match = re.match(r"\[Page (\d+)", line)
        if page_match:
            page = int(page_match.group(1))

        is_heading = False
        heading_level = 1
        for h_text, h_level in heading_map.items():
            if clean.startswith(h_text) or h_text.startswith(clean):
                is_heading = True
                heading_level = h_level
                break

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
                "page": page,
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
                "is_table": '[Table]' in line,
                "page": page,
            })

    return sections


# ── Fallback: Font-Size Heading Detection ─────────────────────────

def _build_sections_from_pdf(formatted: str) -> List[Dict]:
    """PDF fallback: detect headings from font sizes and bold markers.

    Parses [Page N, Bold, Npt] markers to determine heading levels
    based on font size relative to the most common (body) size.
    """
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


def _build_sections_from_markers(formatted: str) -> List[Dict]:
    """DOCX fallback: detect headings from [Heading N] / [Bold] markers."""
    paragraphs = [p.strip() for p in formatted.split('\n') if p.strip()]
    sections = []
    heading_stack = []
    section_counter = [0, 0, 0, 0, 0]

    for para in paragraphs:
        clean = _strip_markers(para)
        if not clean or len(clean.split()) < 3:
            continue

        is_heading = bool(re.match(r'\[(?:Heading \d+|Bold)', para))
        heading_level = 1
        if is_heading:
            m = re.match(r'\[Heading (\d+)', para)
            if m:
                heading_level = int(m.group(1))
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


def _build_sections_from_llm_output(items: List[Dict], raw_lines: List[Dict]) -> List[Dict]:
    """Build sections from combined LLM output (headings + clauses).

    The LLM returns both heading items and clause items. Each clause is
    already sized to 20-70 words. We convert directly to section dicts.
    """
    sections = []
    heading_stack = []
    section_counter = [0, 0, 0, 0, 0]

    # Build a lookup from raw line text to its metadata (page, is_table)
    raw_lookup = {}
    for rl in raw_lines:
        clean = _strip_markers(rl.get("text", "")).strip()
        if clean:
            raw_lookup[clean[:80]] = rl

    for item in items:
        item_type = item.get("type", "clause")
        text = _strip_markers(item.get("text", "")).strip()
        if not text:
            continue

        if item_type == "heading":
            level = min(int(item.get("level", 1)), len(section_counter))
            for j in range(level, len(section_counter)):
                section_counter[j] = 0
            section_counter[level - 1] += 1

            section_path = ".".join(
                str(section_counter[j]) for j in range(level) if section_counter[j] > 0
            )
            heading_stack = [(l, t) for l, t in heading_stack if l < level]
            heading_stack.append((level, text))

            sections.append({
                "section_path": section_path,
                "heading_hierarchy": [t for l, t in heading_stack],
                "content": text,
                "section_level": level,
                "is_heading": True,
                "is_table": False,
                "page": 0,
            })
        else:
            # Clause item — already 20-70 words
            hierarchy = item.get("hierarchy", [])
            if not hierarchy and heading_stack:
                hierarchy = [t for l, t in heading_stack]

            current_path = ".".join(
                str(section_counter[j]) for j in range(len(section_counter)) if section_counter[j] > 0
            ) or "0"

            # Try to find page/table info from raw lines
            page = 0
            is_table = False
            lookup_key = text[:80]
            if lookup_key in raw_lookup:
                page = raw_lookup[lookup_key].get("page", 0)
                is_table = raw_lookup[lookup_key].get("is_table", False)

            sections.append({
                "section_path": current_path,
                "heading_hierarchy": hierarchy if hierarchy else ["Document"],
                "content": text,
                "section_level": len(heading_stack),
                "is_heading": False,
                "is_table": is_table,
                "page": page,
            })

    return sections


# ── Main Entry Point ──────────────────────────────────────────────

def parse_document_structure(file_path: str) -> List[Dict]:
    """Parse any .docx or .pdf document.

    Extracts text + formatting metadata, sends to LLM which detects
    headings AND splits content into 20-70 word clauses in one pass.
    Returns section dicts ready for embedding.
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

    # Build raw line metadata for page/table info
    raw_lines = []
    for line in formatted.split('\n'):
        if line.strip():
            page = 0
            page_match = re.match(r"\[Page (\d+)", line)
            if page_match:
                page = int(page_match.group(1))
            raw_lines.append({
                "text": line,
                "page": page,
                "is_table": '[Table]' in line,
            })

    items = _llm_analyze_structure(formatted, ext)

    if items:
        # Check if LLM returned clauses (new format) or just headings (old format)
        has_clauses = any(i.get("type") == "clause" for i in items)
        if has_clauses:
            sections = _build_sections_from_llm_output(items, raw_lines)
        else:
            # Old format: headings only — use hybrid builder with raw lines
            raw_texts = [rl["text"] for rl in raw_lines]
            sections = _build_sections_hybrid(items, raw_texts)
        if sections:
            return sections

    # Fallback: use format-specific heuristic
    if ext == '.pdf':
        return _build_sections_from_pdf(formatted)
    else:
        return _build_sections_from_markers(formatted)
