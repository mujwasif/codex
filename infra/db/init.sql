-- Codex Database Schema
-- PostgreSQL + pgvector

CREATE EXTENSION IF NOT EXISTS vector;

-- documents: Policy corpus registry
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    type VARCHAR(50),
    owner VARCHAR(100),
    version VARCHAR(20),
    effective_date DATE,
    status VARCHAR(20) DEFAULT 'active',
    source_uri TEXT,
    access_tags JSONB DEFAULT '[]',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- chunks: Clause-level units + vectors
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    section_path TEXT,
    clause_ref VARCHAR(100),
    page INTEGER,
    text TEXT NOT NULL,
    embedding vector(1024),
    token_count INTEGER,
    version VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW()
);

-- entities: Extracted rules/entities mirror
CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR(50),
    name VARCHAR(200),
    document_id UUID REFERENCES documents(id),
    attrs JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);

-- queries: Inbound questions
CREATE TABLE IF NOT EXISTS queries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(100),
    role VARCHAR(50),
    dept VARCHAR(100),
    question TEXT NOT NULL,
    intent VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

-- answers: Responses + outcome
CREATE TABLE IF NOT EXISTS answers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_id UUID REFERENCES queries(id),
    answer TEXT,
    verdict VARCHAR(20),
    confidence FLOAT,
    abstained BOOLEAN DEFAULT FALSE,
    latency_ms INTEGER,
    model_version VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

-- citations: Answer-to-clause links
CREATE TABLE IF NOT EXISTS citations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    answer_id UUID REFERENCES answers(id),
    chunk_id UUID REFERENCES chunks(id),
    document_id UUID REFERENCES documents(id),
    clause_ref VARCHAR(50),
    score FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- feedback: Human review signal
CREATE TABLE IF NOT EXISTS feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    answer_id UUID REFERENCES answers(id),
    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
    note TEXT,
    reviewer VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

-- audit_log: Immutable audit trail
CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor VARCHAR(100),
    action VARCHAR(50),
    payload JSONB DEFAULT '{}',
    ts TIMESTAMP DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_clause_ref ON chunks(clause_ref);
CREATE INDEX IF NOT EXISTS idx_entities_document_id ON entities(document_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_queries_user_id ON queries(user_id);
CREATE INDEX IF NOT EXISTS idx_queries_created_at ON queries(created_at);
CREATE INDEX IF NOT EXISTS idx_answers_query_id ON answers(query_id);
CREATE INDEX IF NOT EXISTS idx_answers_verdict ON answers(verdict);
CREATE INDEX IF NOT EXISTS idx_citations_answer_id ON citations(answer_id);
CREATE INDEX IF NOT EXISTS idx_citations_chunk_id ON citations(chunk_id);
CREATE INDEX IF NOT EXISTS idx_feedback_answer_id ON feedback(answer_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts);

-- Vector index for similarity search (IVFFlat)
-- Note: This index requires data to be present. Run after ingestion.
-- CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
