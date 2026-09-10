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
    python -m services.ingestion.ingestion_agent
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
from packages.shared.config import (
    CODEX_API_URL,
    CLAIM_INTERVAL as CONFIG_CLAIM_INTERVAL,
    GRAPH_BATCH_SIZE as CONFIG_GRAPH_BATCH_SIZE,
    LOG_DIR,
    LLM_BASE_URL,
    MAX_CLAUSE_TOKENS as CONFIG_MAX_CLAUSE_TOKENS,
    OVERLAP_TOKENS as CONFIG_OVERLAP_TOKENS,
    USE_LLM_FOR_CHUNKING,
    WORKER_LOCK_PATH as CONFIG_WORKER_LOCK_PATH,
    NEO4J_PASS,
    NEO4J_URI,
    NEO4J_USER,
)

from packages.shared.config import LOG_DIR
import os

os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "ingestion_agent.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file),
    ],
)
logger = logging.getLogger("ingestion_agent")

MODEL_NAME = "BAAI/bge-large-en-v1.5"
CLAIM_INTERVAL = CONFIG_CLAIM_INTERVAL
MAX_CLAUSE_TOKENS = CONFIG_MAX_CLAUSE_TOKENS
OVERLAP_TOKENS = CONFIG_OVERLAP_TOKENS
USE_LLM = USE_LLM_FOR_CHUNKING
GRAPH_BATCH_SIZE = CONFIG_GRAPH_BATCH_SIZE

WORKER_LOCK_PATH = CONFIG_WORKER_LOCK_PATH

_run_worker = True


def _pid_is_worker(pid: int) -> bool:
    """True only if the PID belongs to a running ingestion worker process."""
    if pid == os.getpid():
        # The lockfile names THIS process. That cannot be another live worker
        # (PIDs are unique among live processes in a PID namespace); it is a
        # stale leftover from a previous incarnation — e.g. a prior container
        # where the worker ran as PID 1 and the bind-mounted state dir
        # outlived it. Report not-a-worker so the caller treats it as stale.
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode(errors="ignore")
        return "ingestion_agent" in cmdline
    except Exception:
        return False


def _acquire_singleton_lock() -> bool:
    """Claim the worker lockfile atomically. Returns False if another worker runs."""
    try:
        # Use a persistent project path instead of /tmp to ensure UI can find it
        lock_path = os.path.join(LOG_DIR, "ingestion_worker.pid")
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        stale = True
        try:
            lock_path = os.path.join(LOG_DIR, "ingestion_worker.pid")
            with open(lock_path, "r") as f:
                existing = f.read().strip()
            if existing.isdigit() and _pid_is_worker(int(existing)):
                logger.info(
                    f"Another ingestion worker is already running (pid {existing}); exiting."
                )
                return False
        except Exception:
            pass
        try:
            lock_path = os.path.join(LOG_DIR, "ingestion_worker.pid")
            os.remove(lock_path)
        except FileNotFoundError:
            pass
        try:
            lock_path = os.path.join(LOG_DIR, "ingestion_worker.pid")
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
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

    return GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASS) if NEO4J_PASS else None,
    )


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


def process_chunking_stage(doc_id: str, model: SentenceTransformer, idx: int, total: int) -> bool:
    """
    Phase 1: Parse and Chunk a document.
    """
    with get_db_session() as session:
        row = session.execute(
            text("SELECT title, source_uri FROM documents WHERE id = :id"),
            {"id": doc_id},
        ).fetchone()
        if not row:
            return False
        filename = row.title
        file_path = row.source_uri

    logger.info(f"Chunking [{idx}/{total}]: {filename}")

    sections = parse_document_structure(file_path)
    if not sections:
        logger.error(f"    -> Failed: no content found")
        _mark_failed(doc_id, f"No content found in {filename}")
        return False

    full_text = "\n".join([s.get("content", "") for s in sections])
    if not full_text.strip():
        logger.error(f"    -> Failed: no text content")
        _mark_failed(doc_id, "No text content in document")
        return False

    access_tags = ["internal"]
    access_level = infer_access_level(filename, access_tags, full_text)

    try:
        chunks = chunk_document_clauses(
            sections,
            max_clause_tokens=MAX_CLAUSE_TOKENS,
            overlap_tokens=OVERLAP_TOKENS,
            use_llm=USE_LLM,
        )

        stats = get_chunk_stats(chunks)

        with get_db_session() as session:
            doc = session.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                return False

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
                    embedding=embedding.tolist() if hasattr(embedding, "tolist") else embedding,
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

        logger.info(
            f"    -> {stats['total_chunks']} clause-level chunks "
            f"from {stats['unique_sections']} sections"
        )
        return True

    except Exception as e:
        logger.error(f"Chunking failed for {filename}: {e}")
        _mark_failed(doc_id, str(e)[:500])
        return False

def process_graph_stage(doc_id: str, idx: int, total: int, model=None) -> bool:
    """
    Phase 2: Generate Knowledge Graph for a previously chunked document.
    """
    with get_db_session() as session:
        row = session.execute(
            text("SELECT title FROM documents WHERE id = :id"),
            {"id": doc_id},
        ).fetchone()
        if not row:
            return False
        filename = row.title

    db_chunk_count = len(_get_chunks_for_document(doc_id))
    logger.info(f"  [{idx}/{total}] Processing: {filename} ({db_chunk_count} chunks)")

    try:
        from services.ingestion.llm_graph_generator import (
            generate_graph_for_document,
            execute_graph_in_neo4j,
        )

        with get_db_session() as session:
            doc = session.query(Document).filter(Document.id == doc_id).first()
            if doc:
                doc.ingestion_status = "graph_building"
                session.commit()

        formatted_chunks = [
            {
                "chunk_id": str(c["id"]),
                "text": c["text"],
                "section_path": c["section_path"],
                "clause_ref": c["clause_ref"],
            }
            for c in _get_chunks_for_document(doc_id)
        ]

        with get_db_session() as session:
            doc = session.query(Document).filter(Document.id == doc_id).first()
            access_level = int(doc.access_level) if doc and doc.access_level is not None else 1

        graph = generate_graph_for_document(
            doc_id=doc_id,
            title=filename,
            chunks=formatted_chunks,
            version="v1",
            status="active",
            max_chunks_per_call=GRAPH_BATCH_SIZE,
            access_level=access_level,
        )

        node_count = len(graph.get("nodes", [])) if graph else 0
        edge_count = len(graph.get("edges", [])) if graph else 0

        if graph and (graph.get("nodes") or graph.get("edges")):
            driver = _get_neo4j_driver()
            execute_graph_in_neo4j(graph, driver)
            driver.close()

        with get_db_session() as session:
            doc = session.query(Document).filter(Document.id == doc_id).first()
            if doc:
                doc.ingestion_status = "ready"
                doc.status = "active"
                doc.graph_ready_at = datetime.utcnow()
                if model is not None:
                    doc.title_embedding = model.encode(doc.title).tolist()
                doc.last_error = None
                session.commit()

        logger.info(f"    -> {node_count} nodes, {edge_count} edges")
        return True
    except Exception as e:
        logger.warning(f"Graph generation failed for {filename}: {e}")
        _mark_failed(doc_id, str(e)[:500])
        return False

def run_worker():
    logger.info("=" * 40)
    logger.info("Codex Staged Ingestion Worker starting")
    logger.info("=" * 40)

    if not _acquire_singleton_lock():
        logger.info("Exiting: another ingestion worker holds the singleton lock.")
        return

    init_db()
    logger.info("Loading embedding model...")
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    logger.info("Embedding model loaded.")

    while _run_worker:
        # STAGE 1: Chunking Wave
        pending_docs = []
        with get_db_session() as session:
            rows = session.execute(
                text("SELECT id FROM documents WHERE ingestion_status = 'pending' ORDER BY created_at")
            ).fetchall()
            pending_docs = [str(r[0]) for r in rows]

        if pending_docs:
            total = len(pending_docs)
            logger.info("\n" + "=" * 40)
            logger.info(f" STAGE 1: CHUNKING ({total} documents)")
            logger.info("=" * 40)
            processed = 0
            for i, doc_id in enumerate(pending_docs, 1):
                if process_chunking_stage(doc_id, model, i, total):
                    processed += 1

            # Refresh BM25 index once after the whole wave is done
            try:
                from packages.shared.auth import create_access_token
                import requests as http_req
                admin_token = create_access_token({"username": "admin", "sub": "admin", "access_level": 3})
                resp = http_req.post(
                    f"{CODEX_API_URL.rstrip('/')}/admin/refresh-index",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    timeout=10,
                )
                if resp.ok:
                    logger.info("BM25 index refreshed.")
                else:
                    logger.warning(f"Batch index refresh returned {resp.status_code}")
            except Exception as e:
                logger.warning(f"Batch index refresh failed: {e}")

            logger.info(f"\nChunking done: {processed} processed, {total - processed} failed out of {total}")

        # STAGE 2: Graph Wave
        ready_for_graph = []
        with get_db_session() as session:
            rows = session.execute(
                text("SELECT id FROM documents WHERE ingestion_status = 'chunks_ready' ORDER BY created_at")
            ).fetchall()
            ready_for_graph = [str(r[0]) for r in rows]

        if ready_for_graph:
            total = len(ready_for_graph)
            logger.info("\n" + "=" * 40)
            logger.info(" STAGE 2: KNOWLEDGE GRAPH (%d documents)" % total)
            logger.info("=" * 40)
            processed = 0
            for i, doc_id in enumerate(ready_for_graph, 1):
                if process_graph_stage(doc_id, i, total, model=model):
                    processed += 1
            logger.info(f"\nGraph done: {processed} processed, {total - processed} skipped out of {total}")

        time.sleep(CLAIM_INTERVAL)

    logger.info("Ingestion worker stopped.")
    _release_singleton_lock()


def main():
    run_worker()


if __name__ == "__main__":
    main()
