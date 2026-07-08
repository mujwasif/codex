import re
from typing import List, Dict, Optional
from rank_bm25 import BM25Okapi
from packages.shared.db import get_db_session
from sqlalchemy import text


class BM25Index:
    """
    In-memory BM25 index built from PostgreSQL chunks.
    
    Loads all active chunks at startup, builds BM25 index,
    and provides keyword-based search with BM25 scoring.
    """

    def __init__(self):
        self.corpus_tokens: List[List[str]] = []
        self.chunk_ids: List[str] = []
        self.chunk_meta: Dict[str, Dict] = {}
        self.bm25: Optional[BM25Okapi] = None
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
        """Load chunks from PostgreSQL and build BM25 index."""
        try:
            with get_db_session() as session:
                sql = text("""
                    SELECT 
                        c.id,
                        c.text,
                        c.clause_ref,
                        c.section_path,
                        c.document_id,
                        d.title
                    FROM chunks c
                    JOIN documents d ON c.document_id = d.id
                    WHERE d.status = 'active'
                    ORDER BY c.created_at
                """)
                result = session.execute(sql)

                corpus_tokens = []
                chunk_ids = []
                chunk_meta = {}

                for row in result:
                    chunk_id = str(row.id)
                    text_content = row.text or ""

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
                    }

            if corpus_tokens:
                self.bm25 = BM25Okapi(corpus_tokens)
                self.corpus_tokens = corpus_tokens
                self.chunk_ids = chunk_ids
                self.chunk_meta = chunk_meta
                self.chunk_count = len(chunk_ids)
            else:
                self.bm25 = None
                self.chunk_count = 0

        except Exception as e:
            print(f"⚠️ BM25 index build failed: {e}")
            self.bm25 = None
            self.chunk_count = 0

    def search(self, query: str, top_k: int = 20) -> List[Dict]:
        """
        Search chunks using BM25 scoring.
        
        Args:
            query: User's search query
            top_k: Number of results to return
            
        Returns:
            List of chunk dicts with BM25 scores, sorted by relevance
        """
        if not self.bm25 or not self.chunk_ids:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores = self.bm25.get_scores(query_tokens)

        scored_indices = list(enumerate(scores))
        scored_indices.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scored_indices[:top_k]:
            chunk_id = self.chunk_ids[idx]
            meta = self.chunk_meta[chunk_id].copy()
            meta["score"] = float(score)
            results.append(meta)

        return results

    def refresh(self):
        """Rebuild index from database (call after new ingestion)."""
        self._build_index()
