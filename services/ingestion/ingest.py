import os
import sys
import uuid
from datetime import datetime
from sqlalchemy import text
from sentence_transformers import SentenceTransformer

from codex.packages.shared.db import get_db_session, init_db
from codex.packages.shared.models import Document as DocumentModel, Chunk
from codex.packages.shared.access_control import infer_access_level
from codex.services.ingestion.structure_chunker import chunk_document_clauses, get_chunk_stats
from codex.packages.shared.config import (

    ARCHIVE_DIR,
    EMBEDDING_DEVICE,
    MAX_CLAUSE_TOKENS,
    OVERLAP_TOKENS,
    USE_LLM_FOR_CHUNKING,
)
MAX_CLAUSE_TOKENS = 200
OVERLAP_TOKENS = 20
USE_LLM = USE_LLM_FOR_CHUNKING

# Setup paths and model
archive_path = ARCHIVE_DIR
model = SentenceTransformer(MODEL_NAME, device=EMBEDDING_DEVICE)


def _existing_doc(session, source_uri: str):
    """Return the document row for an absolute source_uri, or None."""
    row = session.execute(
        text("SELECT id FROM documents WHERE source_uri = :uri"),
        {"uri": source_uri},
    ).fetchone()
    return row


def ingest_file(file_path: str, session, force: bool = False):
    """
    Ingest a single file into the database.

    Skips files whose absolute source_uri is already present unless force=True.

    Args:
        file_path: Path to the file (PDF or DOCX)
        session: SQLAlchemy session
        force: Re-ingest even if the document already exists
    """
    filename = os.path.basename(file_path)
    abs_path = os.path.abspath(file_path)

    existing = _existing_doc(session, abs_path)
    if existing and not force:
        print(f"  Skipping {filename}: already ingested (source_uri exists).")
        return
    if existing and force:
        print(f"  Force re-ingest: removing existing {filename} ({existing[0]})...")
        # Re-ingestion assigns new chunk IDs, so historical answer citations
        # that point at this document's old chunks must be removed first
        # (citations_chunk_id_fkey).
        session.execute(text(
            "DELETE FROM citations WHERE chunk_id IN "
            "(SELECT id FROM chunks WHERE document_id = :did)"
        ), {"did": existing[0]})
        session.execute(text("DELETE FROM chunks WHERE document_id = :did"), {"did": existing[0]})
        session.execute(text("DELETE FROM documents WHERE id = :did"), {"did": existing[0]})
        session.commit()

    try:
        # Parse document structure
        print(f"  Parsing structure of {filename}...")
        sections = parse_document_structure(file_path)
        
        if not sections:
            print(f"  Skipping {filename}: No content found.")
            return
        
        # Extract full text for entity extraction
        full_text = '\n'.join([s.get('content', '') for s in sections])
        
        if not full_text.strip():
            print(f"  Skipping {filename}: No text content found.")
            return

        # Infer access level from explicit tags > filename > content > default
        access_tags = ['internal']
        access_level = infer_access_level(filename, access_tags, full_text)
        print(f"  Inferred access_level={access_level} for {filename}")

        # Create document record
        doc_id = str(uuid.uuid4())
        document = DocumentModel(
            id=doc_id,
            title=filename,
            type='policy',
            status='active',
            source_uri=abs_path,
            access_tags=access_tags,
            access_level=access_level,
            effective_date=datetime.utcnow()
        )
        session.add(document)
        
        # Chunk with clause-level detection
        print(f"  Chunking {filename} with clause-level detection...")
        chunks = chunk_document_clauses(
            sections,
            llama_url=None,
            max_clause_tokens=MAX_CLAUSE_TOKENS,
            overlap_tokens=OVERLAP_TOKENS,
            use_llm=USE_LLM
        )
        
        # Print chunk statistics
        stats = get_chunk_stats(chunks)
        print(f"  Created {stats['total_chunks']} clause-level chunks from {stats['unique_sections']} sections")
        print(f"  Token stats: avg={stats['avg_tokens']:.1f}, min={stats['min_tokens']}, max={stats['max_tokens']}")
        
        # Insert chunks with version field
        for chunk_data in chunks:
            chunk_id = str(uuid.uuid4())
            embedding = model.encode(chunk_data['text']).tolist()
            
            chunk = Chunk(
                id=chunk_id,
                document_id=doc_id,
                section_path=chunk_data['section_path'],
                clause_ref=chunk_data['clause_ref'],
                page=chunk_data.get('page'),
                text=chunk_data['text'],
                embedding=embedding.tolist() if hasattr(embedding, "tolist") else embedding,
                token_count=chunk_data['token_count'],
                version='v1',
                access_level=access_level
            )
            session.add(chunk)
        
        session.commit()
        print(f"  Successfully ingested {filename}: {stats['total_chunks']} chunks")
        print(f"  Run 'migrate_to_neo4j.py' to generate the knowledge graph.")
        
    except Exception as e:
        print(f"  Error processing {filename}: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()


def main():
    """Main ingestion pipeline."""
    force = "--force" in sys.argv
    if force:
        print("Force mode: re-ingesting files even if already present in the database.")

    # Initialize database tables
    print("Initializing database...")
    init_db()
    
    # Check if archive folder exists
    if not os.path.exists(archive_path):
        print(f"Error: Archive folder not found at {archive_path}")
        return
    
    # Start ingestion
    print("Starting ingestion pipeline with structure-aware chunking...")
    
    with get_db_session() as session:
        for filename in os.listdir(archive_path):
            if filename.lower().endswith((".docx", ".pdf")):
                file_path = os.path.join(archive_path, filename)
                print(f"\nProcessing: {filename}")
                ingest_file(file_path, session, force=force)
    
    print("\nIngestion process complete.")


if __name__ == "__main__":
    main()
