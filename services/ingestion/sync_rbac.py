"""
Codex RBAC Sync — Neo4j → PostgreSQL projection.

The knowledge graph is the source of truth for document access levels.
When a document's level is updated in Neo4j (or Neo4j was offline during
ingestion), this script projects the authoritative access_level back onto
the PostgreSQL documents + chunks tables.

Usage:
    source /home/mujtaba/new_folder/fastmcp/venv/bin/activate
    cd /home/mujtaba/new_folder/codex
    PYTHONPATH=/home/mujtaba/new_folder/codex python3 services/ingestion/sync_rbac.py
    PYTHONPATH=/home/mujtaba/new_folder/codex python3 services/ingestion/sync_rbac.py --dry-run
"""

import sys
import logging
from neo4j import GraphDatabase
from sqlalchemy import text
from packages.shared.db import get_db_session
from packages.shared.access_control import clamp_access_level

from packages.shared.config import NEO4J_URI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rbac_sync")


def fetch_neo4j_access_levels(driver) -> dict:
    """Return {doc_id: access_level} from Neo4j Policy nodes."""
    levels = {}
    try:
        records, _, _ = driver.execute_query(
            "MATCH (p:Policy) WHERE p.id IS NOT NULL AND p.access_level IS NOT NULL "
            "RETURN p.id AS id, p.access_level AS access_level"
        )
        for r in records:
            levels[str(r["id"])] = clamp_access_level(r["access_level"])
    except Exception as e:
        logger.warning(f"Neo4j query failed (may be offline): {e}")
    return levels


def apply_projection(levels: dict, dry_run: bool = False) -> int:
    """
    Update documents + chunks in PostgreSQL to match Neo4j levels.

    Returns the number of documents whose level was changed.
    """
    changed = 0
    with get_db_session() as session:
        for doc_id, level in levels.items():
            row = session.execute(
                text("SELECT access_level FROM documents WHERE id = :id"),
                {"id": doc_id},
            ).fetchone()
            if row is None:
                continue

            current = clamp_access_level(row[0]) if row[0] is not None else 1
            if current == level:
                continue

            changed += 1
            if dry_run:
                logger.info(f"  [dry-run] {doc_id}: access_level {current} -> {level}")
                continue

            session.execute(
                text("UPDATE documents SET access_level = :level WHERE id = :id"),
                {"level": level, "id": doc_id},
            )
            session.execute(
                text("UPDATE chunks SET access_level = :level WHERE document_id = :id"),
                {"level": level, "id": doc_id},
            )
            logger.info(f"  {doc_id}: access_level {current} -> {level}")

    return changed


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("Dry-run mode: no writes will be applied.")

    print("=" * 40)
    print(" RBAC Sync: Neo4j -> PostgreSQL")
    print("=" * 40)

    try:
        driver = GraphDatabase.driver(NEO4J_URI)
    except Exception as e:
        logger.error(f"Failed to connect to Neo4j: {e}")
        sys.exit(1)

    levels = fetch_neo4j_access_levels(driver)
    driver.close()
    print(f"Found {len(levels)} Policy nodes with an access_level in Neo4j.")

    if not levels:
        print("Nothing to sync (Neo4j offline or no levels stamped yet).")
        return

    changed = apply_projection(levels, dry_run=dry_run)
    print(f"Done: {changed} document(s) would be{' (dry-run)' if dry_run else ''} updated.")


if __name__ == "__main__":
    main()
