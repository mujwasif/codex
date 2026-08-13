-- 001_add_access_level.sql
-- Adds RBAC access_level columns to existing installations.
-- Safe to run repeatedly: ALTER ... IF NOT EXISTS-style guards via checks.

-- documents.access_level
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'documents' AND column_name = 'access_level'
    ) THEN
        ALTER TABLE documents ADD COLUMN access_level INTEGER DEFAULT 1;
    END IF;
END $$;

-- chunks.access_level
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'chunks' AND column_name = 'access_level'
    ) THEN
        ALTER TABLE chunks ADD COLUMN access_level INTEGER DEFAULT 1;
    END IF;
END $$;

-- Backfill chunks.access_level from their document, then default the rest.
UPDATE chunks c
SET access_level = d.access_level
FROM documents d
WHERE c.document_id = d.id
  AND (c.access_level IS NULL OR c.access_level <> d.access_level);

UPDATE chunks SET access_level = 1 WHERE access_level IS NULL;

-- Indexes for the retrieval fast path.
CREATE INDEX IF NOT EXISTS idx_documents_access_level ON documents(access_level);
CREATE INDEX IF NOT EXISTS idx_chunks_access_level ON chunks(access_level);
