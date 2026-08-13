"""
Codex Ingestion Worker

Processes documents that were explicitly uploaded through the admin UI.
The UI's POST /v1/ingest saves the file and creates a Document row with
ingestion_status='pending'. This worker claims pending rows from the database
and runs the full pipeline (parse -> chunk -> embed -> graph) for each one.

This is deliberately NOT a filesystem watcher: files dropped directly into
archive/ are never auto-ingested. Only rows created by an explicit upload
(ingestion_status='pending') are processed.

Usage:
    PYTHONPATH=/home/mujtaba/new_folder/codex python3 -m services.ingestion.ingestion_agent
"""

import os
import time
import uuid
import logging
import signal
import sys
from datetime import datetime
from typing import List, Optional

from sentence_transformers import SentenceTransformer
from sqlalchemy import text

from packages.shared.db import get_db_session, init_db
from packages.shared.models import Document, Chunk
from packages.shared.access_control import infer_access_level
from packages.shared.doc_parser import parse_document_structure
from services.ingestion.structure_chunker import chunk_document_clauses, get_chunk_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/tmp/ingestion_agent.log"),
    ],
)
logger = logging.getLogger("ingestion_agent")

from packages.shared.config import NEO4J_URI, LLAMA_4B_URL
LLAMA_URL = f"{LLAMA_4B_URL}/v1/chat/completions"
MODEL_NAME = "BAAI/bge-large-en-v1.5"
CLAIM_INTERVAL = 3
MAX_CLAUSE_TOKENS = 200
OVERLAP_TOKENS = 20
USE_LLM = True
GRAPH_BATCH_SIZE = 30

WORKER_LOCK_PATH = "/tmp/ingestion_worker.pid"

_run_worker = True


def _pid_is_worker(pid: int) -> bool:
    """True only if the PID belongs to a running ingestion worker process."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode(errors="ignore")
        return "ingestion_agent" in cmdline
    except Exception:
        return False


def _acquire_singleton_lock() -> bool:
    """Claim the worker lockfile atomically. Returns False if another worker runs."""
    try:
        fd = os.open(WORKER_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        stale = True
        try:
            with open(WORKER_LOCK_PATH, "r") as f:
                existing = f.read().strip()
            if existing.isdigit() and _pid_is_worker(int(existing)):
                logger.info(f"Another ingestion worker is already running (pid {existing}); exiting.")
                return False
        except Exception:
            pass
        try:
            os.remove(WORKER_LOCK_PATH)
        except FileNotFoundError:
            pass
        try:
            fd = os.open(WORKER_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            logger.info("Another ingestion worker is already running; exiting.")
            return False
    try:
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    return True


def _release_singleton_lock():
    try:
        with open(WORKER_LOCK_PATH, "r") as f:
            pid = f.read().strip()
        if pid == str(os.getpid()):
            os.remove(WORKER_LOCK_PATH)
    except Exception:
        pass


def signal_handler(sig, frame):
    global _run_worker
    logger.info("Shutdown signal received, finishing current document...")
    _run_worker = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def _get_neo4j_driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(NEO4J_URI)


def _doc_exists(doc_id: str) -> bool:
    with get_db_session() as session:
        result = session.execute(
            text("SELECT id FROM documents WHERE id = :id"),
            {"id": doc_id},
        ).fetchone()
        return result is not None


def _get_chunks_for_document(doc_id: str) -> List[dict]:
    with get_db_session() as session:
        rows = session.execute(
            text("""
                SELECT id, text, section_path, clause_ref, version
                FROM chunks
                WHERE document_id = :doc_id
                ORDER BY section_path, clause_ref
            """),
            {"doc_id": doc_id},
        ).fetchall()
        return [dict(r._mapping) for r in rows]


def _mark_failed(doc_id: str, error: str):
    with get_db_session() as session:
        doc = session.query(Document).filter(Document.id == doc_id).first()
        if doc:
            doc.ingestion_status = "failed"
            doc.last_error = str(error)[:500]
            session.commit()


def process_document(doc_id: str, model: SentenceTransformer):
    with get_db_session() as session:
        row = session.execute(
            text("SELECT title, source_uri FROM documents WHERE id = :id"),
            {"id": doc_id},
        ).fetchone()
        if not row:
            logger.warning(f"Document {doc_id} not found (likely cancelled before processing)")
            return
        filename = row.title
        file_path = row.source_uri

    logger.info(f"Processing: {filename}")

    sections = parse_document_structure(file_path)
    if not sections:
        logger.warning(f"No content found in {filename}, skipping.")
        _mark_failed(doc_id, f"No content found in {filename}")
        return

    full_text = "\n".join([s.get("content", "") for s in sections])
    if not full_text.strip():
        logger.warning(f"No text content in {filename}, skipping.")
        _mark_failed(doc_id, "No text content in document")
        return

    access_tags = ["internal"]
    access_level = infer_access_level(filename, access_tags, full_text)
    logger.info(f"  Inferred access_level={access_level} for {filename}")

    try:
        chunks = chunk_document_clauses(
            sections,
            llama_url=LLAMA_URL,
            max_clause_tokens=MAX_CLAUSE_TOKENS,
            overlap_tokens=OVERLAP_TOKENS,
            use_llm=USE_LLM,
        )

        stats = get_chunk_stats(chunks)
        logger.info(f"  Created {stats['total_chunks']} chunks from {stats['unique_sections']} sections")

        with get_db_session() as session:
            doc = session.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise RuntimeError(f"Document {doc_id} not found in DB (cancelled mid-flight)")

            for chunk_data in chunks:
                chunk_id = str(uuid.uuid4())
                embedding = model.encode(chunk_data["text"]).tolist()

                chunk = Chunk(
                    id=chunk_id,
                    document_id=doc_id,
                    section_path=chunk_data["section_path"],
                    clause_ref=chunk_data["clause_ref"],
                    page=chunk_data.get("page"),
                    text=chunk_data["text"],
                    embedding=str(embedding),
                    token_count=chunk_data["token_count"],
                    version="v1",
                    access_level=access_level,
                )
                session.add(chunk)

            doc.status = "processing"
            doc.ingestion_status = "chunks_ready"
            doc.access_level = access_level
            doc.last_error = None
            session.commit()

        logger.info(f"  Stored {stats['total_chunks']} chunks in PostgreSQL")

    except Exception as e:
        logger.error(f"  Ingestion failed for {filename}: {e}")
        _mark_failed(doc_id, str(e)[:500])
        return

    try:
        from packages.shared.auth import create_access_token
        import requests as http_req
        admin_token = create_access_token({"username": "admin", "sub": "admin", "access_level": 3})
        resp = http_req.post(
            "http://localhost:8000/admin/refresh-index",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        if resp.ok:
            logger.info("  BM25 index refreshed")
        else:
            logger.warning(f"  BM25 refresh returned {resp.status_code}")
    except Exception as e:
        logger.warning(f"  BM25 refresh failed (FastAPI may be offline): {e}")

    try:
        from services.ingestion.llm_graph_generator import generate_graph_for_document, execute_graph_in_neo4j

        with get_db_session() as session:
            doc = session.query(Document).filter(Document.id == doc_id).first()
            if doc:
                doc.ingestion_status = "graph_building"
                session.commit()

        db_chunks = _get_chunks_for_document(doc_id)
        logger.info(f"  Generating graph for {len(db_chunks)} chunks (batch size {GRAPH_BATCH_SIZE})...")

        formatted_chunks = [
            {
                "chunk_id": str(c["id"]),
                "text": c["text"],
                "section_path": c["section_path"],
                "clause_ref": c["clause_ref"],
            }
            for c in db_chunks
        ]

        graph = generate_graph_for_document(
            doc_id=doc_id,
            title=filename,
            chunks=formatted_chunks,
            version="v1",
            status="active",
            max_chunks_per_call=GRAPH_BATCH_SIZE,
            access_level=access_level,
        )

        if graph:
            if not _doc_exists(doc_id):
                logger.info(f"  {filename} cancelled mid-flight — graph skipped")
                return
            driver = _get_neo4j_driver()
            execute_graph_in_neo4j(graph, driver)
            driver.close()
            logger.info(f"  Graph complete: {len(graph.get('nodes', []))} nodes, {len(graph.get('edges', []))} edges")

        with get_db_session() as session:
            doc = session.query(Document).filter(Document.id == doc_id).first()
            if doc:
                doc.ingestion_status = "ready"
                doc.status = "active"
                doc.graph_ready_at = datetime.utcnow()
                doc.last_error = None
                session.commit()
        logger.info(f"  {filename} registered as READY (chunks + graph)")
    except Exception as e:
        logger.warning(f"  Graph generation failed (Neo4j may be offline): {e}")
        try:
            with get_db_session() as session:
                doc = session.query(Document).filter(Document.id == doc_id).first()
                if doc:
                    doc.ingestion_status = "failed"
                    doc.last_error = str(e)[:500]
                    session.commit()
        except Exception:
            pass
        # Defensive cleanup: remove any partially-written graph nodes for this doc.
        try:
            driver = _get_neo4j_driver()
            with driver.session() as session:
                with session.begin_transaction() as tx:
                    tx.run("MATCH (p:Policy {id: $doc_id}) DETACH DELETE p", doc_id=doc_id)
                    chunk_ids = [str(c["id"]) for c in _get_chunks_for_document(doc_id)]
                    if chunk_ids:
                        tx.run(
                            "MATCH (c:Clause) WHERE c.id IN $chunk_ids DETACH DELETE c",
                            chunk_ids=chunk_ids,
                        )
            driver.close()
            logger.info("  Cleaned partial Neo4j graph for failed document")
        except Exception:
            pass

    logger.info(f"Done: {filename}")


def _claim_pending() -> Optional[str]:
    """Atomically claim the oldest pending document, or None if none exists."""
    with get_db_session() as session:
        row = session.execute(
            text("SELECT id FROM documents WHERE ingestion_status = 'pending' ORDER BY created_at LIMIT 1")
        ).fetchone()
        if not row:
            return None
        doc_id = str(row.id)
        result = session.execute(
            text("""
                UPDATE documents
                SET ingestion_status = 'processing', status = 'processing', last_error = NULL
                WHERE id = :id AND ingestion_status = 'pending'
            """),
            {"id": doc_id},
        )
        session.commit()
        if result.rowcount != 1:
            return None
        return doc_id


def run_worker():
    logger.info("=" * 50)
    logger.info("Codex Ingestion Worker starting")
    logger.info(f"  LLM: {LLAMA_URL}")
    logger.info(f"  Neo4j: {NEO4J_URI}")
    logger.info("  Mode: explicit queue — processes ONLY documents uploaded through the admin UI")
    logger.info("=" * 50)

    if not _acquire_singleton_lock():
        logger.info("Exiting: another ingestion worker holds the singleton lock.")
        return

    init_db()

    logger.info("Loading embedding model...")
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    logger.info("Embedding model loaded.")

    while _run_worker:
        doc_id = _claim_pending()
        if doc_id is None:
            time.sleep(CLAIM_INTERVAL)
            continue
        try:
            process_document(doc_id, model)
        except Exception as e:
            logger.error(f"Unexpected error processing {doc_id}: {e}")
            _mark_failed(doc_id, str(e)[:500])

    logger.info("Ingestion worker stopped.")
    _release_singleton_lock()


def main():
    run_worker()


if __name__ == "__main__":
    main()