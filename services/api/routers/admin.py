"""
Admin endpoints: audit logs, BM25 index refresh, document ingestion, departments.
"""

import os
import shutil
import subprocess
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File

from codex.packages.shared.schemas import AuditLogResponse, DepartmentResponse
from codex.packages.shared.db import get_db_session
from codex.packages.shared.models import AuditLog, Answer, Document, Query
from codex.services.api.dependencies import get_current_active_user, log_audit_action, require_admin
from codex.services.agents.tools.connections import ConnectionPool

router = APIRouter(tags=["admin"])


@router.get("/audit", response_model=list[AuditLogResponse])
async def get_audit_logs(
    action: str = None,
    actor: str = None,
    limit: int = 100,
    current_user: dict = Depends(require_admin),
):
    """Get audit logs (admin only)."""
    try:
        with get_db_session() as session:
            query = session.query(AuditLog)

            if action:
                query = query.filter(AuditLog.action == action)
            if actor:
                query = query.filter(AuditLog.actor == actor)

            logs = query.order_by(AuditLog.ts.desc()).limit(limit).all()

            return [
                AuditLogResponse(
                    id=str(log.id),
                    actor=log.actor,
                    action=log.action,
                    payload=log.payload,
                    ts=log.ts.isoformat() if log.ts else ""
                )
                for log in logs
            ]
    except Exception as e:
        print(f"⚠️ Failed to fetch audit logs: {e}")
        return []


@router.get("/departments", response_model=list[DepartmentResponse])
async def list_departments(
    current_user: dict = Depends(get_current_active_user)
):
    """Return each Department node from the Neo4j knowledge graph with its access level."""
    try:
        driver = ConnectionPool.get_neo4j()
        records, _, _ = driver.execute_query(
            "MATCH (d:Department) RETURN d.name AS name, d.access_level AS access_level "
            "ORDER BY d.access_level"
        )
        return [
            DepartmentResponse(
                name=r["name"],
                access_level=int(r["access_level"]) if r["access_level"] is not None else None,
            )
            for r in records
        ]
    except Exception as e:
        print(f"⚠️ Failed to fetch departments: {e}")
        return []


@router.post("/admin/refresh-index")
async def refresh_index(
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """Rebuild BM25 index after new ingestion. Admin only."""
    bm25_index = request.app.state.bm25_index
    if bm25_index is None:
        raise HTTPException(status_code=500, detail="BM25 index not initialized")
    try:
        bm25_index.refresh()
        return {"status": "ok", "chunk_count": bm25_index.chunk_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to refresh index: {e}")


@router.post("/v1/ingest")
async def ingest_document(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_admin),
):
    """Upload a document for background ingestion. Admin only."""
    from sqlalchemy import text as sa_text

    if not file.filename.lower().endswith((".docx", ".pdf")):
        raise HTTPException(status_code=400, detail="Only .docx and .pdf files are supported")

    filename = file.filename

    with get_db_session() as session:
        row = session.execute(
            sa_text("SELECT id, ingestion_status FROM documents WHERE title = :title"),
            {"title": filename},
        ).fetchone()

        if row:
            status = row.ingestion_status or "unknown"
            if status != "failed":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"A document named '{filename}' already exists "
                        f"(ingestion_status: {status}). Remove it before re-uploading."
                    ),
                )
            # Failed document: allow re-upload by removing the stale record so
            # the worker's source_uri check no longer matches this path.
            failed_id = str(row.id)
            try:
                session.execute(sa_text(
                    "DELETE FROM citations WHERE document_id = :id "
                    "OR chunk_id IN (SELECT id FROM chunks WHERE document_id = :id)"
                ), {"id": failed_id})
                session.execute(sa_text("DELETE FROM entities WHERE document_id = :id"), {"id": failed_id})
                session.execute(sa_text("DELETE FROM documents WHERE id = :id"), {"id": failed_id})
                session.commit()
            except Exception as e:
                session.rollback()
                raise HTTPException(status_code=500, detail=f"Failed to clear stale failed document: {e}")
            log_audit_action(
                "admin",
                "ingestion_reupload",
                {"filename": filename, "replaced_failed_document_id": failed_id},
            )

    from packages.shared.config import ARCHIVE_DIR
    archive_dir = ARCHIVE_DIR
    os.makedirs(archive_dir, exist_ok=True)

    dest_path = os.path.join(archive_dir, filename)

    try:
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")
    finally:
        file.file.close()

    # Register the upload so the ingestion worker picks it up. This row is the
    # ONLY way ingestion work gets created — the worker never scans the folder.
    doc = Document(
        id=str(uuid.uuid4()),
        title=filename,
        type="policy",
        status="pending",
        ingestion_status="pending",
        source_uri=dest_path,
        access_tags=["internal"],
        access_level=1,
    )
    try:
        with get_db_session() as session:
            session.add(doc)
            session.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to register document: {e}")

    return {
        "status": "queued",
        "filename": filename,
        "message": "File saved to archive. Ingestion worker will process it shortly.",
    }


@router.get("/v1/metrics")
async def get_metrics(
    current_user: dict = Depends(require_admin),
):
    """Return system telemetry: query volume, latency, verdict distribution, confidence stats."""
    metrics = {
        "queries_total": 0,
        "answers_total": 0,
        "recent_queries_24h": 0,
        "avg_latency_ms": None,
        "abstention_rate": None,
        "verdict_distribution": {},
        "intent_distribution": {},
        "avg_confidence": None,
    }
    try:
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(hours=24)

        with get_db_session() as session:
            metrics["queries_total"] = session.query(Query).count()
            metrics["recent_queries_24h"] = session.query(Query).filter(
                Query.created_at >= cutoff
            ).count()

            answer_rows = session.query(Answer).all()
            metrics["answers_total"] = len(answer_rows)

            if answer_rows:
                latencies = [a.latency_ms for a in answer_rows if a.latency_ms is not None]
                confidences = [a.confidence for a in answer_rows if a.confidence is not None]
                verdicts = [a.verdict for a in answer_rows if a.verdict]

                metrics["avg_latency_ms"] = round(sum(latencies) / len(latencies), 1) if latencies else None
                metrics["avg_confidence"] = round(sum(confidences) / len(confidences), 1) if confidences else None

                abstained = sum(1 for v in verdicts if v in ("abstained", "abstain"))
                metrics["abstention_rate"] = round(abstained / len(verdicts), 4) if verdicts else None

                dist = {}
                for v in verdicts:
                    dist[v] = dist.get(v, 0) + 1
                metrics["verdict_distribution"] = dist

            intent_rows = session.query(Query.intent).all()
            intent_dist = {}
            for row in intent_rows:
                intent = row[0] or "unknown"
                intent_dist[intent] = intent_dist.get(intent, 0) + 1
            metrics["intent_distribution"] = intent_dist

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compute metrics: {e}")

    return metrics


@router.get("/v1/ingestion/status")
def ingestion_status(
    current_user: dict = Depends(require_admin),
):
    """Read-only ingestion lifecycle status for the admin workspace."""
    from sqlalchemy import text as sa_text

    try:
        worker = False
        try:
            result = subprocess.run(
                ["pgrep", "-f", "services.ingestion.ingestion_agent"],
                capture_output=True,
                text=True,
            )
            worker = result.returncode == 0
        except Exception:
            worker = False

        with get_db_session() as session:
            counts = {}
            for row in session.execute(
                sa_text("SELECT ingestion_status, COUNT(*) FROM documents GROUP BY ingestion_status")
            ):
                counts[row[0] or "unknown"] = row[1]

            documents = session.execute(sa_text("""
                SELECT
                    d.id,
                    d.title,
                    d.ingestion_status,
                    d.graph_ready_at,
                    d.updated_at,
                    d.last_error,
                    (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id) AS chunk_count
                FROM documents d
                ORDER BY d.updated_at DESC
                LIMIT 100
            """)).fetchall()

        log_tail = ""
        try:
            from packages.shared.config import LOG_DIR
            log_path = os.path.join(LOG_DIR, "ingestion_agent.log")
            with open(log_path, "r", errors="ignore") as f:
                lines = f.readlines()
                log_tail = "".join(lines[-40:])
        except Exception:
            log_tail = ""

        return {
            "worker_alive": worker,
            "counts": counts,
            "documents": [
                {
                    "id": str(r.id),
                    "title": r.title,
                    "ingestion_status": r.ingestion_status,
                    "graph_ready_at": r.graph_ready_at.isoformat() if r.graph_ready_at else None,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                    "chunk_count": r.chunk_count,
                    "last_error": r.last_error,
                }
                for r in documents
            ],
            "log_tail": log_tail,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch ingestion status: {e}")


def _delete_document_from_neo4j(doc_id: str, chunk_ids) -> dict:
    """Remove the document's Policy/Clause nodes + edges, then orphaned entities."""
    from codex.services.agents.tools.connections import ConnectionPool

    graph_summary = {"policy_deleted": 0, "clauses_deleted": 0, "orphans_deleted": 0}
    try:
        driver = ConnectionPool.get_neo4j()
        with driver.session() as session:
            with session.begin_transaction() as tx:
                result = tx.run("MATCH (p:Policy {id: $doc_id}) DETACH DELETE p", doc_id=str(doc_id))
                graph_summary["policy_deleted"] = result.consume().counters.nodes_deleted
            if chunk_ids:
                with session.begin_transaction() as tx:
                    result = tx.run(
                        "MATCH (c:Clause) WHERE c.id IN $chunk_ids DETACH DELETE c",
                        chunk_ids=[str(cid) for cid in chunk_ids],
                    )
                    graph_summary["clauses_deleted"] = result.consume().counters.nodes_deleted
            with session.begin_transaction() as tx:
                result = tx.run(
                    "MATCH (n) "
                    "WHERE NOT n:Policy AND NOT n:Clause AND NOT n:Department "
                    "AND NOT (n)--() DELETE n"
                )
                graph_summary["orphans_deleted"] = result.consume().counters.nodes_deleted
    except Exception as e:
        print(f"⚠️ Neo4j cleanup failed for {doc_id}: {e}")
    return graph_summary


@router.delete("/v1/documents/{document_id}")
async def delete_document(
    document_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """Permanently remove a document and ALL related content (chunks, citations, entities, Neo4j graph)."""
    from sqlalchemy import text as sa_text

    with get_db_session() as session:
        row = session.execute(
            sa_text("""
                SELECT d.id, d.title, d.source_uri, d.ingestion_status,
                       (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id) AS chunk_count
                FROM documents d
                WHERE d.id = :id
            """),
            {"id": document_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")

        chunk_ids = [r[0] for r in session.execute(
            sa_text("SELECT id FROM chunks WHERE document_id = :id"),
            {"id": document_id},
        ).fetchall()]

        try:
            # Citations FKs are NO ACTION — delete first (mirrors ingest.py force path)
            session.execute(sa_text(
                "DELETE FROM citations WHERE document_id = :id "
                "OR chunk_id IN (SELECT id FROM chunks WHERE document_id = :id)"
            ), {"id": document_id})
            session.execute(sa_text("DELETE FROM entities WHERE document_id = :id"), {"id": document_id})
            session.execute(sa_text("DELETE FROM documents WHERE id = :id"), {"id": document_id})
            session.commit()
        except Exception as e:
            session.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to delete document: {e}")

    summary = _delete_document_from_neo4j(document_id, chunk_ids)

    archive_file = None
    if row.source_uri and os.path.isfile(row.source_uri):
        archive_file = row.source_uri
        try:
            os.remove(row.source_uri)
        except Exception as e:
            print(f"⚠️ Could not remove source file {row.source_uri}: {e}")

    bm25 = request.app.state.bm25_index
    if bm25 is not None:
        try:
            bm25.refresh()
        except Exception as e:
            print(f"⚠️ BM25 refresh failed after delete: {e}")

    log_audit_action(
        "admin",
        "document_deleted",
        {
            "document_id": document_id,
            "title": row.title,
            "chunks_removed": len(chunk_ids),
            "archive_file_removed": bool(archive_file),
            "graph": summary,
        },
    )

    return {
        "status": "deleted",
        "document_id": document_id,
        "title": row.title,
        "chunks_removed": len(chunk_ids),
        "ingestion_status": row.ingestion_status,
        "archive_file_removed": archive_file is not None,
        "graph": summary,
    }
