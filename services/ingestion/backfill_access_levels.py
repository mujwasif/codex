"""
Codex RBAC Backfill — assign access_level to pre-existing documents.

Re-runs access-level inference over every document already in PostgreSQL
(title + access_tags + chunk text), then propagates the level to its chunks.
Idempotent: only updates rows whose level changes.

Usage:
    source /home/mujtaba/new_folder/fastmcp/venv/bin/activate
    cd /home/mujtaba/new_folder/codex
    PYTHONPATH=/home/mujtaba/new_folder/codex python3 services/ingestion/backfill_access_levels.py
    PYTHONPATH=/home/mujtaba/new_folder/codex python3 services/ingestion/backfill_access_levels.py --dry-run
"""

import sys
from sqlalchemy import text
from packages.shared.db import get_db_session
from packages.shared.access_control import infer_access_level, clamp_access_level


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("Dry-run mode: no writes will be applied.")

    with get_db_session() as session:
        rows = session.execute(text("""
            SELECT d.id, d.title, d.access_tags, d.access_level,
                   COALESCE(
                       (SELECT string_agg(c.text, '\n')
                        FROM chunks c WHERE c.document_id = d.id),
                       ''
                   ) AS body
            FROM documents d
        """)).fetchall()

    print(f"Backfilling access levels for {len(rows)} document(s)...")

    changed = 0
    with get_db_session() as session:
        for row in rows:
            r = row._mapping
            doc_id = str(r["id"])
            title = r["title"] or ""
            access_tags = r["access_tags"] or []
            body = r["body"] or ""

            inferred = infer_access_level(title, access_tags, body)
            current = clamp_access_level(r["access_level"]) if r["access_level"] is not None else 1

            if inferred == current:
                continue

            changed += 1
            if dry_run:
                print(f"  [dry-run] {doc_id} ({title}): {current} -> {inferred}")
                continue

            session.execute(
                text("UPDATE documents SET access_level = :level WHERE id = :id"),
                {"level": inferred, "id": doc_id},
            )
            session.execute(
                text("UPDATE chunks SET access_level = :level WHERE document_id = :id"),
                {"level": inferred, "id": doc_id},
            )
            print(f"  {doc_id} ({title}): {current} -> {inferred}")

    print(f"Done: {changed} document(s) would be{' (dry-run)' if dry_run else ''} updated.")
    print("Note: run 'sync_rbac.py' afterwards to reconcile with Neo4j, and "
          "POST /admin/refresh-index to rebuild the per-level BM25 indexes.")


if __name__ == "__main__":
    main()
