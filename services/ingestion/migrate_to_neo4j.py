"""
Codex PostgreSQL -> Neo4j Knowledge Graph Migration

Pure LLM-based graph generation using Qwen2.5-3B-Instruct with document-level batching.
Groups chunks by document and sends them in batches of 40 (~50 LLM calls for 1,976 chunks).

Usage:
    source /home/mujtaba/new_folder/fastmcp/venv/bin/activate
    PYTHONPATH=/home/mujtaba/new_folder/codex python3 services/ingestion/migrate_to_neo4j.py
    PYTHONPATH=/home/mujtaba/new_folder/codex python3 services/ingestion/migrate_to_neo4j.py --incremental
"""

import sys
import logging
from collections import defaultdict
from neo4j import GraphDatabase
from sqlalchemy import text
from packages.shared.db import get_db_session
from services.ingestion.llm_graph_generator import generate_graph_for_document, execute_graph_in_neo4j

from packages.shared.config import NEO4J_URI
neo = GraphDatabase.driver(NEO4J_URI)

PROCESSED_FILE = "processed_docs.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("neo4j_migration")

MAX_CHUNKS_PER_CALL = 40


def _load_processed() -> set:
    try:
        with open(PROCESSED_FILE) as f:
            return set(line.strip() for line in f)
    except FileNotFoundError:
        return set()


def _save_processed(doc_id: str):
    with open(PROCESSED_FILE, "a") as f:
        f.write(doc_id + "\n")


def summary():
    print("\n" + "=" * 50)
    print(" NODES")
    records, _, _ = neo.execute_query(
        "MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c ORDER BY c DESC"
    )
    for r in records:
        print(f"  {r['l']:15s} {r['c']}")
    print("\n RELATIONSHIPS")
    records, _, _ = neo.execute_query(
        "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"
    )
    for r in records:
        print(f"  {r['t']:30s} {r['c']}")
    nodes_rec, _, _ = neo.execute_query("MATCH (n) RETURN count(n) AS n")
    rels_rec, _, _ = neo.execute_query("MATCH ()-[r]->() RETURN count(r) AS r")
    print(f"\n  Total: {nodes_rec[0]['n']} nodes, {rels_rec[0]['r']} relationships")
    print("=" * 50)


def main():
    incremental = "--incremental" in sys.argv
    processed = _load_processed() if incremental else set()

    print("=" * 40)
    print(" Codex -> Neo4j (Document-Level Batch Graph Generation)")
    print("=" * 40)

    if incremental:
        print(f"Incremental mode: {len(processed)} documents already processed")

    with get_db_session() as session:
        rows = session.execute(text("""
            SELECT
                c.id AS chunk_id, c.text, c.section_path, c.clause_ref, c.version,
                d.id AS doc_id, d.title AS doc_title, d.status AS doc_status,
                d.access_level AS doc_access_level
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            ORDER BY d.id, c.section_path
        """)).fetchall()

    docs = defaultdict(list)
    doc_meta = {}
    for row in rows:
        r = row._mapping
        doc_id = str(r["doc_id"])
        docs[doc_id].append({
            "chunk_id": str(r["chunk_id"]),
            "text": (r["text"] or "").strip(),
            "section_path": (r["section_path"] or ""),
            "clause_ref": (r["clause_ref"] or ""),
        })
        if doc_id not in doc_meta:
            doc_meta[doc_id] = {
                "title": r["doc_title"] or "Untitled",
                "status": r["doc_status"] or "active",
                "access_level": r["doc_access_level"] if r["doc_access_level"] is not None else 1,
            }

    total_docs = len(docs)
    processed_count = 0
    skipped_count = 0

    for doc_index, (doc_id, chunks) in enumerate(docs.items()):
        meta = doc_meta[doc_id]

        if incremental and doc_id in processed:
            skipped_count += 1
            print(f"  [{doc_index + 1}/{total_docs}] Skipped (already processed): {meta['title']}")
            continue

        valid_chunks = [c for c in chunks if len(c["text"]) >= 20]
        if not valid_chunks:
            skipped_count += 1
            print(f"  [{doc_index + 1}/{total_docs}] Skipped (no valid chunks): {meta['title']}")
            if incremental:
                _save_processed(doc_id)
            continue

        print(f"  [{doc_index + 1}/{total_docs}] Processing: {meta['title']} ({len(valid_chunks)} chunks)")

        try:
            graph = generate_graph_for_document(
                doc_id=doc_id,
                title=meta["title"],
                chunks=valid_chunks,
                version="v1",
                status=meta["status"],
                max_chunks_per_call=MAX_CHUNKS_PER_CALL,
                access_level=meta["access_level"],
            )

            if graph and (graph.get("nodes") or graph.get("edges")):
                execute_graph_in_neo4j(graph, neo)
                processed_count += 1
                if incremental:
                    _save_processed(doc_id)
                print(f"    -> {len(graph.get('nodes', []))} nodes, {len(graph.get('edges', []))} edges")
            else:
                skipped_count += 1
                print(f"    -> No graph generated")

        except Exception as e:
            logger.error(f"Error processing document {doc_id} ({meta['title']}): {e}")

    print(f"\nDone: {processed_count} documents processed, {skipped_count} skipped out of {total_docs}")
    summary()

    neo.close()
    print("\nDone! http://localhost:7474")


if __name__ == "__main__":
    main()
