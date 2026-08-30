#!/usr/bin/env python3
"""
One-time backfill: embed all existing document titles using bge-large-en-v1.5.

Idempotent — safe to re-run. Skips documents that already have a title_embedding.

Usage:
    cd /home/mujtaba/new_folder/codex
    PYTHONPATH=/home/mujtaba/new_folder/codex python3 infra/migrate_title_embeddings.py
"""

import sys
from sentence_transformers import SentenceTransformer
from packages.shared.db import get_db_session, init_db
from packages.shared.models import Document
from packages.shared.config import RETRIEVER_MODEL, EMBEDDING_DEVICE


def main():
    init_db()
    print(f"Loading embedding model ({RETRIEVER_MODEL})...")
    model = SentenceTransformer(RETRIEVER_MODEL, device=EMBEDDING_DEVICE)

    with get_db_session() as session:
        docs = (
            session.query(Document)
            .filter(Document.title_embedding.is_(None))
            .all()
        )
        if not docs:
            print("All documents already have title embeddings. Nothing to do.")
            return

        print(f"Backfilling title embeddings for {len(docs)} document(s)...")
        for doc in docs:
            emb = model.encode(doc.title).tolist()
            doc.title_embedding = emb
            print(f"  {doc.title} -> {len(emb)}-dim vector")

        session.commit()
        print(f"Done. Updated {len(docs)} document(s).")


if __name__ == "__main__":
    main()
