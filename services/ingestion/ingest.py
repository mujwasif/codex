import os
import re
import uuid
from datetime import datetime
from sentence_transformers import SentenceTransformer

from packages.shared.db import get_db_session, init_db
from packages.shared.models import Document as DocumentModel, Chunk, Entity
from packages.shared.doc_parser import parse_document_structure
from services.ingestion.structure_chunker import chunk_document_clauses, get_chunk_stats

# Configuration
MODEL_NAME = 'BAAI/bge-large-en-v1.5'  # 1024 dimensions, high quality semantic embeddings
LLAMA_URL = "http://localhost:8080/v1/chat/completions"
MAX_CLAUSE_TOKENS = 50
OVERLAP_TOKENS = 3
USE_LLM = True

# Setup paths and model
script_dir = os.path.dirname(os.path.abspath(__file__))
archive_path = os.path.join(script_dir, '..', '..', 'archive')
model = SentenceTransformer(MODEL_NAME)


def extract_entities(text: str, document_id: str):
    """
    Extract policy entities (thresholds, approvals, deadlines) from text.
    Returns list of Entity objects.
    """
    entities = []
    
    # Pattern 1: Monetary thresholds (e.g., "NGN 1,000,000", "USD 50,000")
    amount_pattern = r'(?:NGN|USD|EUR|GBP|₦|\$|€|£)\s*[\d,]+(?:\.\d{2})?'
    amounts = re.findall(amount_pattern, text)
    
    for amount in amounts:
        # Clean up the amount
        clean_amount = re.sub(r'[^\d.]', '', amount)
        if clean_amount:
            currency = 'NGN' if 'NGN' in amount or '₦' in amount else 'USD'
            entities.append(Entity(
                type='threshold',
                name=f'Monetary Threshold: {amount}',
                document_id=document_id,
                attrs={
                    'amount': float(clean_amount),
                    'currency': currency,
                    'original_text': amount
                }
            ))
    
    # Pattern 2: Approval authorities (e.g., "Manager approval", "CFO approval")
    approval_pattern = r'(?:Manager|Director|CFO|CEO|VP|Head of|Lead)\s+(?:approval|authorization|consent)'
    approvals = re.findall(approval_pattern, text, re.IGNORECASE)
    
    for approval in approvals:
        entities.append(Entity(
            type='approval',
            name=f'Approval Authority: {approval}',
            document_id=document_id,
            attrs={
                'authority': approval.split()[0],
                'original_text': approval
            }
        ))
    
    # Pattern 3: Time deadlines (e.g., "every 90 days", "within 30 days")
    time_pattern = r'(?:every|within|after|before)\s+(\d+)\s+(?:days|weeks|months|hours|minutes)'
    times = re.findall(time_pattern, text, re.IGNORECASE)
    
    for time_val in times:
        entities.append(Entity(
            type='deadline',
            name=f'Time Deadline: {time_val} days',
            document_id=document_id,
            attrs={
                'days': int(time_val),
                'original_text': time_val
            }
        ))
    
    # Pattern 4: Requirements (e.g., "must be", "required", "mandatory")
    requirement_pattern = r'(?:must|required|mandatory|shall)\s+(\w+(?:\s+\w+)?)'
    requirements = re.findall(requirement_pattern, text, re.IGNORECASE)
    
    for req in requirements[:5]:  # Limit to first 5 to avoid noise
        entities.append(Entity(
            type='requirement',
            name=f'Requirement: {req}',
            document_id=document_id,
            attrs={
                'requirement': req,
                'original_text': req
            }
        ))
    
    return entities


def ingest_file(file_path: str, session):
    """
    Ingest a single file into the database.
    
    Args:
        file_path: Path to the file (PDF or DOCX)
        session: SQLAlchemy session
    """
    filename = os.path.basename(file_path)
    
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
        
        # Create document record
        doc_id = str(uuid.uuid4())
        document = DocumentModel(
            id=doc_id,
            title=filename,
            type='policy',
            status='active',
            source_uri=file_path,
            access_tags=['internal'],
            effective_date=datetime.utcnow()
        )
        session.add(document)
        
        # Chunk with clause-level detection
        print(f"  Chunking {filename} with clause-level detection...")
        chunks = chunk_document_clauses(
            sections,
            llama_url=LLAMA_URL,
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
                embedding=str(embedding),
                token_count=chunk_data['token_count'],
                version='v1'
            )
            session.add(chunk)
        
        # Extract entities from the full text
        entities = extract_entities(full_text, doc_id)
        for entity in entities:
            session.add(entity)
        
        session.commit()
        print(f"  Successfully ingested {filename}: {stats['total_chunks']} chunks, {len(entities)} entities")
        
    except Exception as e:
        print(f"  Error processing {filename}: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()


def main():
    """Main ingestion pipeline."""
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
                ingest_file(file_path, session)
    
    print("\nIngestion process complete.")


if __name__ == "__main__":
    main()
