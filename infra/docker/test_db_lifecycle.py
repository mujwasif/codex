#!/usr/bin/env python3
"""
Isolated test-database lifecycle for the compose `tests` profile (T4 / ISC-4).

Usage:
    python infra/docker/test_db_lifecycle.py bootstrap   # drop+create+seed
    python infra/docker/test_db_lifecycle.py teardown    # drop

bootstrap:
    1. DROP DATABASE IF EXISTS <TEST_DB_NAME> WITH (FORCE)  (PG13+, idempotent)
    2. CREATE DATABASE <TEST_DB_NAME>
    3. CREATE EXTENSION vector          (pgvector is installed PER-DATABASE; the
       image init script only provisions POSTGRES_DB, so a fresh test DB needs
       its own extension before init_db() can create the embedding column)
    4. init_db()                        (SQLAlchemy schema)
    5. deterministic seed corpus        (see _seed)

teardown:
    DROP DATABASE IF EXISTS <TEST_DB_NAME> WITH (FORCE)

Isolation invariant: the dev database named by POSTGRES_DB (codex_db) is NEVER
touched. Admin DDL connects to the 'postgres' maintenance database; all data
operations go through DATABASE_URL, which the tests service points at
<TEST_DB_NAME>. Exit code is nonzero on any failure so the shell orchestrator
(run_tests.sh) can abort before running suites.
"""

import os
import sys
from datetime import datetime, timedelta

import psycopg2

TEST_DB_NAME = os.getenv("TEST_DB_NAME", "codex_test_db")


def _admin_dsn() -> str:
    """DATABASE_URL with the database segment swapped to 'postgres'."""
    url = os.environ["DATABASE_URL"]
    head, sep, tail = url.rpartition("/")
    if not sep or not tail:
        raise ValueError(f"cannot derive admin DSN from DATABASE_URL: {url!r}")
    return f"{head}/postgres"


def _connect(dsn: str):
    conn = psycopg2.connect(dsn)
    conn.autocommit = True  # CREATE/DROP DATABASE cannot run inside a txn block
    return conn


def _exec_admin(dsn: str, statement: str) -> None:
    """Run one admin DDL statement on its own autocommit connection.

    Deliberately avoids `with conn:` — psycopg2-binary 2.9.10 forces
    transactional semantics inside the connection context manager even with
    autocommit=True (statements inside the block do not auto-commit; exit
    commits), so CREATE/DROP DATABASE die with ActiveSqlTransaction. Proven
    empirically 2026-08-22 against postgres:16-alpine.
    """
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
    finally:
        conn.close()


def drop_test_db() -> None:
    _exec_admin(_admin_dsn(), f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
    print(f"[lifecycle] dropped {TEST_DB_NAME} (if present)")


def bootstrap() -> None:
    drop_test_db()

    _exec_admin(_admin_dsn(), f'CREATE DATABASE "{TEST_DB_NAME}"')
    print(f"[lifecycle] created {TEST_DB_NAME}")

    # pgvector is per-database — provision it before init_db() creates chunks.
    with _connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    print("[lifecycle] pgvector extension ready")

    from packages.shared.db import init_db

    init_db()
    _seed()
    print("[lifecycle] bootstrap complete")


# ── Deterministic seed corpus ────────────────────────────────────────────────
# Sized for the retrieval-quality assertions in tests/test_retrieval_quality.py:
#   - broad password query must surface PASSWORD_DOC in the top results
#   - "How often should passwords change?" must surface a "90 days" rule
#     ("90 days" is seeded in TWO password clauses for ranking robustness)
#   - BM25 index must be non-empty and free of prompt-leak text
#   - rerank pairs must carry document titles
# Two off-topic documents act as distractors so retrieval has real work to do.
# All rows are access_level=1 / ingestion_status='ready' (level-3 queries see all).

PASSWORD_DOC = "UnderDefense MAXI - Password management policy.docx"

SEED_CORPUS = [
    (
        "t4doc-password-0001",
        PASSWORD_DOC,
        [
            (
                "5.1.3",
                "5.1 Password Rotation",
                "All passwords must be changed every 90 days. This applies to all "
                "user accounts and service accounts without exception.",
            ),
            (
                "5.1.4",
                "5.1 Password Rotation",
                "Each password must be at least 12 characters long and must not be "
                "reused for at least five password generations.",
            ),
            (
                "5.2.1",
                "5.2 Privileged Accounts",
                "Administrative passwords must be rotated every 90 days and must be "
                "stored in the approved enterprise password manager.",
            ),
            (
                "6.1.2",
                "6.1 Handling",
                "Users must not share passwords with any other person, and default "
                "vendor passwords must be changed before deployment.",
            ),
            (
                "7.3.1",
                "7.3 Remote Access",
                "Multi-factor authentication must be enabled for all remote access, "
                "and users must not reuse passwords across separate systems.",
            ),
        ],
    ),
    (
        "t4doc-travel-0002",
        "Travel and expense policy.docx",
        [
            (
                "2.1",
                "2 Expense Reporting",
                "Expense reports must be submitted within 30 days of travel "
                "completion using the corporate finance portal.",
            ),
            (
                "3.4",
                "3 Bookings",
                "Hotel bookings must be arranged through the corporate travel agency "
                "to qualify for reimbursement.",
            ),
            (
                "4.2",
                "4 Allowances",
                "Meal allowances shall not exceed the published per-diem rates for "
                "each destination country.",
            ),
        ],
    ),
    (
        "t4doc-retention-0003",
        "Data retention policy.docx",
        [
            (
                "6.2.3",
                "6 Vendor Records",
                "Vendor data including contracts and access logs must be retained "
                "for 5 years minimum after contract end.",
            ),
            (
                "8.1.2",
                "8 Employee Records",
                "All employee data including personal records and access logs must "
                "be retained for 7 years after collection.",
            ),
            (
                "9.1",
                "9 Disposal",
                "Backup tapes must be destroyed securely once the retention schedule "
                "expires for the underlying records.",
            ),
        ],
    ),
]


def _seed() -> None:
    from sqlalchemy.orm import Session

    from packages.shared.db import engine
    from packages.shared.models import Chunk, Document
    from services.api.search import retriever  # same embedder the query path uses

    base_ts = datetime(2026, 1, 1, 0, 0, 0)
    n_chunks = 0
    with Session(engine) as session:
        seq = 0
        for doc_id, title, clauses in SEED_CORPUS:
            session.add(
                Document(
                    id=doc_id,
                    title=title,
                    type="docx",
                    owner="t4-seed",
                    version="v1",
                    status="active",
                    ingestion_status="ready",
                    source_uri=f"test://{doc_id}",
                    access_level=1,
                    created_at=base_ts,
                    updated_at=base_ts,
                )
            )
            for clause_ref, section_path, text in clauses:
                # Identical encode call to vector_search(): no prefixes either side.
                embedding = retriever.encode(text).tolist()
                seq += 1
                session.add(
                    Chunk(
                        id=f"t4chunk-{doc_id.split('-')[-1]}-{n_chunks + 1:04d}",
                        document_id=doc_id,
                        section_path=section_path,
                        clause_ref=clause_ref,
                        text=text,
                        embedding=embedding,
                        token_count=len(text.split()),
                        version="v1",
                        access_level=1,
                        created_at=base_ts + timedelta(seconds=seq),
                    )
                )
                n_chunks += 1
        session.commit()

    print(f"[lifecycle] seeded {len(SEED_CORPUS)} documents / {n_chunks} chunks")


def teardown() -> None:
    drop_test_db()


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if action == "bootstrap":
            bootstrap()
        elif action == "teardown":
            teardown()
        else:
            print(f"usage: {sys.argv[0]} bootstrap|teardown", file=sys.stderr)
            return 2
    except Exception as exc:  # noqa: BLE001 — report and fail loudly
        print(f"[lifecycle] {action or '?'} FAILED: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
