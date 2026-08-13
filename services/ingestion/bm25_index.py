import re
from typing import List, Dict, Optional
from rank_bm25 import BM25Okapi
from packages.shared.db import get_db_session
from packages.shared.chunk_filter import is_low_info
from sqlalchemy import text


class BM25Index:
    """
    In-memory BM25 index built from PostgreSQL chunks with per-level isolation.

    Three indexes are pre-built, one per access-level ceiling:
      - level 1 index: chunks from documents with access_level <= 1
      - level 2 index: chunks from documents with access_level <= 2
      - level 3 index: chunks from documents with access_level <= 3 (all)

    A search is routed to the index matching the caller's access level, so a
    level-1 user physically cannot retrieve chunks from restricted documents.
    """

    def __init__(self):
        # Per-level state, keyed by access level ceiling (1, 2, 3).
        self._indexes: Dict[int, dict] = {
            1: {"corpus_tokens": [], "chunk_ids": [], "chunk_meta": {}, "bm25": None},
            2: {"corpus_tokens": [], "chunk_ids": [], "chunk_meta": {}, "bm25": None},
            3: {"corpus_tokens": [], "chunk_ids": [], "chunk_meta": {}, "bm25": None},
        }
        # Total chunk count across all levels (for the refresh endpoint).
        self.chunk_count: int = 0
        self._build_index()

    def _tokenize(self, text: str) -> List[str]:
        """
        Tokenize text for BM25.
        
        Strategy: lowercase + split on whitespace/punctuation.
        No stopword removal — policy terms like "must", "shall" are important.
        """
        text = text.lower()
        tokens = re.findall(r'[a-z0-9]+', text)
        return tokens

    def _build_index(self):
        """Load chunks from PostgreSQL and build the three per-level indexes."""
        try:
            with get_db_session() as session:
                sql = text("""
                    SELECT 
                        c.id,
                        c.text,
                        c.clause_ref,
                        c.section_path,
                        c.document_id,
                        c.access_level,
                        d.title
                    FROM chunks c
                    JOIN documents d ON c.document_id = d.id
                    WHERE d.ingestion_status = 'ready'
                    ORDER BY c.created_at
                """)
                result = session.execute(sql)

                # Group chunks by their access level so we can build cumulative
                # (level <= N) indexes.
                rows_by_level = {1: [], 2: [], 3: []}
                for row in result:
                    level = int(row.access_level) if row.access_level is not None else 1
                    if level < 1:
                        level = 1
                    elif level > 3:
                        level = 3
                    rows_by_level[level].append(row)

            cumulative = []
            total = 0
            for ceiling in (1, 2, 3):
                cumulative.extend(rows_by_level[ceiling])
                state = self._indexes[ceiling]
                corpus_tokens = []
                chunk_ids = []
                chunk_meta = {}

                for row in cumulative:
                    chunk_id = str(row.id)
                    text_content = row.text or ""

                    # Skip low-information chunks (prompt leaks, table junk,
                    # bare cross-references) so they never enter the index.
                    if is_low_info(row.clause_ref, text_content):
                        continue

                    tokens = self._tokenize(text_content)
                    if not tokens:
                        continue

                    corpus_tokens.append(tokens)
                    chunk_ids.append(chunk_id)
                    chunk_meta[chunk_id] = {
                        "id": chunk_id,
                        "text": text_content,
                        "clause_ref": row.clause_ref,
                        "section_path": row.section_path,
                        "document_id": str(row.document_id),
                        "title": row.title,
                        "access_level": int(row.access_level) if row.access_level is not None else 1,
                    }

                if corpus_tokens:
                    state["bm25"] = BM25Okapi(corpus_tokens)
                    state["corpus_tokens"] = corpus_tokens
                    state["chunk_ids"] = chunk_ids
                    state["chunk_meta"] = chunk_meta
                else:
                    state["bm25"] = None
                    state["chunk_ids"] = []
                    state["chunk_meta"] = {}

                if ceiling == 3:
                    total = len(chunk_ids)

            self.chunk_count = total

        except Exception as e:
            print(f"⚠️ BM25 index build failed: {e}")
            for ceiling in (1, 2, 3):
                self._indexes[ceiling]["bm25"] = None
                self._indexes[ceiling]["chunk_ids"] = []
                self._indexes[ceiling]["chunk_meta"] = {}
            self.chunk_count = 0

    def _state_for_level(self, access_level: int) -> dict:
        """Pick the index whose ceiling is the caller's access level."""
        if access_level is None:
            return self._indexes[3]
        try:
            level = int(access_level)
        except (TypeError, ValueError):
            return self._indexes[3]
        if level < 1:
            return self._indexes[1]
        if level > 3:
            return self._indexes[3]
        return self._indexes[level]

    def search(self, query: str, top_k: int = 20, access_level: int = 1) -> List[Dict]:
        """
        Search chunks using BM25 scoring within the caller's access ceiling.

        Args:
            query: User's search query
            top_k: Number of results to return
            access_level: Caller's access level; results are limited to
                documents with access_level <= this value
            
        Returns:
            List of chunk dicts with BM25 scores, sorted by relevance
        """
        state = self._state_for_level(access_level)
        bm25 = state["bm25"]
        chunk_ids = state["chunk_ids"]
        chunk_meta = state["chunk_meta"]

        if not bm25 or not chunk_ids:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores = bm25.get_scores(query_tokens)

        scored_indices = list(enumerate(scores))
        scored_indices.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scored_indices[:top_k]:
            chunk_id = chunk_ids[idx]
            meta = chunk_meta[chunk_id].copy()
            meta["score"] = float(score)
            results.append(meta)

        return results

    def refresh(self):
        """Rebuild indexes from database (call after new ingestion)."""
        self._build_index()
