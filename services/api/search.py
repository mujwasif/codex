from typing import List, Dict
from sentence_transformers import CrossEncoder, SentenceTransformer
from sqlalchemy import text
from packages.shared.db import get_db_session
from packages.shared.chunk_filter import is_low_info

# Configuration
RETRIEVER_MODEL = 'BAAI/bge-large-en-v1.5'  # 1024 dimensions, high quality semantic embeddings
RERANKER_MODEL = 'BAAI/bge-reranker-base'

# Load models (CPU to keep the 8GB GPU free for the LLM servers)
retriever = SentenceTransformer(RETRIEVER_MODEL, device="cpu")
reranker = CrossEncoder(RERANKER_MODEL, device="cpu")


def reciprocal_rank_fusion(result_lists: List[List[Dict]], k: int = 60) -> List[Dict]:
    """
    Merge multiple ranked lists using Reciprocal Rank Fusion (RRF).
    
    score(d) = sum(1 / (k + rank_i(d))) for each list i
    k=60 is standard (from original RRF paper, Cormack et al. 2009).
    
    Args:
        result_lists: List of ranked result lists. Each list contains dicts with 'id' key.
        k: RRF parameter (default 60)
        
    Returns:
        Merged list sorted by RRF score, deduplicated by chunk id
    """
    scores = {}

    for result_list in result_lists:
        for rank, doc in enumerate(result_list):
            doc_id = doc["id"]
            if doc_id not in scores:
                scores[doc_id] = {"doc": doc, "score": 0.0}
            scores[doc_id]["score"] += 1.0 / (k + rank + 1)

    merged = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
    return [item["doc"] for item in merged]


def vector_search(query: str, access_level: int, top_k: int = 20) -> List[Dict]:
    """
    Vector similarity search using pgvector.

    Args:
        query: The user's question
        access_level: User's access level (1=Standard, 2=Manager, 3=Admin);
            documents at or below this level are retrievable
        top_k: Number of candidates to retrieve

    Returns:
        List of chunk dictionaries sorted by vector similarity
    """
    query_vector = retriever.encode(query).tolist()

    # Overfetch then Python-filter low-information chunks so the SQL query
    # stays simple and is_low_info() is the single source of truth.
    overfetch = max(top_k * 4, 50)

    with get_db_session() as session:
        sql = text("""
            SELECT 
                c.id,
                c.text,
                c.document_id,
                c.clause_ref,
                c.section_path,
                d.title,
                1 - (c.embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE d.ingestion_status = 'ready'
              AND d.access_level <= :user_level
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """) 

        result = session.execute(sql, {
            "embedding": str(query_vector),
            "user_level": access_level,
            "limit": overfetch
        })

        candidates = []
        for row in result:
            candidates.append({
                "id": str(row.id),
                "text": row.text,
                "document_id": str(row.document_id),
                "clause_ref": row.clause_ref,
                "section_path": row.section_path,
                "title": row.title,
                "similarity": float(row.similarity),
            })

    candidates = [c for c in candidates if not is_low_info(c.get("clause_ref"), c.get("text"))]
    return candidates[:top_k]


def search_policy(
    query: str,
    access_level: int,
    top_k_retrieval: int = 20,
    top_k_final: int = 5,
    search_mode: str = "hybrid",
    bm25_index=None,
) -> List[Dict]:
    """
    Search for relevant policy chunks with configurable search mode.
    
    Args:
        query: The user's question
        access_level: User's access level (1=Standard, 2=Manager, 3=Admin)
        top_k_retrieval: Number of candidates to retrieve per search method
        top_k_final: Number of final results to return after reranking
        search_mode: "vector" | "hybrid" | "bm25"
        bm25_index: BM25Index instance (required for hybrid/bm25 modes)
        
    Returns:
        List of chunk dictionaries with text, title, clause_ref, score, etc.
    """
    try:
        # Stage 1: Retrieval
        if search_mode == "bm25":
            if bm25_index is None:
                print("⚠️ BM25 index not available, falling back to vector search")
                candidates = vector_search(query, access_level, top_k_retrieval)
            else:
                candidates = bm25_index.search(query, top_k_retrieval, access_level)

        elif search_mode == "hybrid":
            vector_results = vector_search(query, access_level, top_k_retrieval)
            bm25_results = bm25_index.search(query, top_k_retrieval, access_level) if bm25_index else []
            candidates = reciprocal_rank_fusion([vector_results, bm25_results], k=60)

        else:
            # Default: vector-only
            candidates = vector_search(query, access_level, top_k_retrieval)

        if not candidates:
            print("⚠️ No candidates found in database")
            return []

        # Stage 2: Rerank with CrossEncoder
        # Include the document title so the reranker can match on the source
        # policy name (e.g. a query about "password policies" should surface
        # the "Password management policy" document).
        pairs = [[query, f"{c.get('title', '')} | {c['text']}"] for c in candidates]
        scores = reranker.predict(pairs)

        for i, score in enumerate(scores):
            candidates[i]["score"] = float(score)

        ranked_results = sorted(candidates, key=lambda x: x["score"], reverse=True)

        return ranked_results[:top_k_final]

    except Exception as e:
        print(f"⚠️ Search failed: {e}")
        return []
