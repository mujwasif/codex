import json
import re
from typing import List, Dict
from packages.shared.chunk_filter import is_low_info

from packages.shared.config import MIN_CLAUSE_TOKENS


def _group_table_sections(sections: List[Dict]) -> List[Dict]:
    """Group consecutive table sections into single sections."""
    if not sections:
        return sections

    result = []
    table_buffer = None

    for sec in sections:
        if sec.get('is_table', False) and not sec.get('is_heading', False):
            if table_buffer is None:
                table_buffer = dict(sec)
            else:
                table_buffer['content'] = table_buffer['content'] + " | " + sec.get('content', '')
        else:
            if table_buffer is not None:
                table_buffer['token_count'] = len(table_buffer['content'].split())
                result.append(table_buffer)
                table_buffer = None
            result.append(sec)

    if table_buffer is not None:
        table_buffer['token_count'] = len(table_buffer['content'].split())
        result.append(table_buffer)

    return result


def chunk_document_clauses(
    sections: List[Dict],
    max_clause_tokens: int = 70,
    overlap_tokens: int = 3,
    use_llm: bool = True
) -> List[Dict]:
    """
    Convert sections into clause-level chunks.

    The LLM (doc_parser) already split content into 20-70 word clauses.
    This function adds metadata (clause_ref, heading_hierarchy) and applies
    a safety-net split for anything still exceeding max_clause_tokens.

    Args:
        sections: List of sections from doc_parser (already clause-sized)
        max_clause_tokens: Safety net max tokens (default 70)
        overlap_tokens: Not used (LLM handles sizing)
        use_llm: Not used (LLM already ran in doc_parser)

    Returns:
        List of clause-level chunks with metadata
    """
    chunks = []
    clause_counter = {}

    sections = _group_table_sections(sections)

    for section in sections:
        if section.get('is_heading', False):
            continue

        section_path = section.get('section_path', '')
        heading_hierarchy = section.get('heading_hierarchy', [])
        content = section.get('content', '')
        page = section.get('page')
        is_table = section.get('is_table', False)

        if not content.strip():
            continue
        if len(content.strip().split()) < 3:
            continue

        if section_path not in clause_counter:
            clause_counter[section_path] = 0
        clause_counter[section_path] += 1
        clause_num = clause_counter[section_path]

        hierarchy_str = ' > '.join(heading_hierarchy) if heading_hierarchy else 'Content'
        clause_ref = f"{section_path} {hierarchy_str} > Rule {clause_num}"

        token_count = len(content.split())

        # Safety net: split if still over max
        if token_count > max_clause_tokens:
            sub_clauses = _split_long_clause(content, max_clause_tokens)
            for sub_text in sub_clauses:
                clause_counter[section_path] += 1
                sub_num = clause_counter[section_path]
                chunks.append({
                    'section_path': section_path,
                    'clause_ref': f"{section_path} {hierarchy_str} > Rule {sub_num}",
                    'heading_hierarchy': heading_hierarchy,
                    'text': sub_text,
                    'token_count': len(sub_text.split()),
                    'page': page,
                    'clause_number': sub_num,
                    'is_table': is_table,
                })
        else:
            chunks.append({
                'section_path': section_path,
                'clause_ref': clause_ref,
                'heading_hierarchy': heading_hierarchy,
                'text': content,
                'token_count': token_count,
                'page': page,
                'clause_number': clause_num,
                'is_table': is_table,
            })

    raw_chunks = [c for c in chunks if not is_low_info(c.get('clause_ref', ''), c.get('text', ''))]
    return _merge_small_chunks(raw_chunks, max_clause_tokens)


def _merge_small_chunks(chunks: List[Dict], max_tokens: int) -> List[Dict]:
    """Merge chunks below MIN_CLAUSE_TOKENS into neighbors (max 70 total)."""
    if not chunks:
        return chunks

    result = []
    buffer = None

    for chunk in chunks:
        wc = chunk['token_count']
        if wc < MIN_CLAUSE_TOKENS:
            if buffer is None:
                buffer = dict(chunk)
            elif buffer['token_count'] + wc <= max_tokens:
                buffer['text'] = buffer['text'] + " " + chunk['text']
                buffer['token_count'] = len(buffer['text'].split())
            else:
                result.append(buffer)
                buffer = dict(chunk)
        else:
            if buffer is not None:
                if buffer['token_count'] + wc <= max_tokens:
                    chunk['text'] = buffer['text'] + " " + chunk['text']
                    chunk['token_count'] = len(chunk['text'].split())
                elif result and result[-1]['token_count'] + buffer['token_count'] <= max_tokens:
                    result[-1]['text'] = result[-1]['text'] + " " + buffer['text']
                    result[-1]['token_count'] = len(result[-1]['text'].split())
                else:
                    result.append(buffer)
                buffer = None
            result.append(chunk)

    if buffer is not None:
        if result and result[-1]['token_count'] + buffer['token_count'] <= max_tokens:
            result[-1]['text'] += " " + buffer['text']
            result[-1]['token_count'] = len(result[-1]['text'].split())
        else:
            result.append(buffer)

    return result


def _split_long_clause(text: str, max_tokens: int) -> List[str]:
    """Split a clause at sentence boundaries, then word boundaries as fallback."""
    if not text or not text.strip():
        return []

    # Try sentence splitting first
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    result = []
    current = []
    current_tokens = 0

    for sentence in sentences:
        stokens = len(sentence.split())
        if current_tokens + stokens > max_tokens and current:
            result.append(' '.join(current))
            current = [sentence]
            current_tokens = stokens
        else:
            current.append(sentence)
            current_tokens += stokens

    if current:
        result.append(' '.join(current))

    # Hard cap: split at word boundaries if anything still too long
    final = []
    for clause in result:
        if len(clause.split()) <= max_tokens:
            final.append(clause)
        else:
            words = clause.split()
            i = 0
            while i < len(words):
                final.append(' '.join(words[i:i + max_tokens]))
                i += max_tokens

    return final


def get_chunk_stats(chunks: List[Dict]) -> Dict:
    """Get statistics about the chunks."""
    if not chunks:
        return {
            'total_chunks': 0,
            'avg_chunk_size': 0,
            'min_chunk_size': 0,
            'max_chunk_size': 0,
            'total_tokens': 0,
            'unique_sections': 0,
            'avg_tokens': 0,
            'min_tokens': 0,
            'max_tokens': 0
        }

    sizes = [len(c['text']) for c in chunks]
    tokens = [c.get('token_count', 0) for c in chunks]
    sections = set(c['section_path'] for c in chunks)

    return {
        'total_chunks': len(chunks),
        'avg_chunk_size': sum(sizes) / len(sizes),
        'min_chunk_size': min(sizes),
        'max_chunk_size': max(sizes),
        'total_tokens': sum(tokens),
        'unique_sections': len(sections),
        'avg_tokens': sum(tokens) / len(tokens),
        'min_tokens': min(tokens),
        'max_tokens': max(tokens)
    }
