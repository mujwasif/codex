# Codex Models — SQLAlchemy ORM Relationships

## Overview

`packages/shared/models.py` defines 8 SQLAlchemy models that mirror the PostgreSQL schema. These Python classes let you interact with the database using Python objects instead of raw SQL.

---

## The 8 Models

### 1. Document — Policy Corpus Registry

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Unique document identifier |
| `title` | TEXT | Document name (e.g., "Information Security Policy") |
| `type` | VARCHAR(50) | Document category (e.g., "security", "compliance") |
| `owner` | VARCHAR(100) | Department or person responsible |
| `version` | VARCHAR(20) | Version string (e.g., "v1", "v2") |
| `effective_date` | DATE | When the policy goes into effect |
| `status` | VARCHAR(20) | "active", "archived", "draft" |
| `source_uri` | TEXT | Original file path or URL |
| `access_tags` | JSONB | Access control tags `["public", "internal"]` |
| `created_at` | TIMESTAMP | Record creation time |
| `updated_at` | TIMESTAMP | Last modification time |

**Relationships:**
- `chunks` → has many `Chunk` objects (cascade delete)
- `entities` → has many `Entity` objects

**Example:** "UnderDefense MAXI Information Security Policy v2.1"

---

### 2. Chunk — Clause-Level Text Units

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Unique chunk identifier |
| `document_id` | UUID (FK) | Links to parent `Document` |
| `section_path` | TEXT | Hierarchical location (e.g., "4.2.1 > Password Policy") |
| `clause_ref` | VARCHAR(100) | Clause reference number (e.g., "4.2.1") |
| `page` | INTEGER | Page number in original document |
| `text` | TEXT | Actual clause text content |
| `embedding` | TEXT | Vector embedding (1024 dims, stored as JSON string) |
| `token_count` | INTEGER | Number of tokens in the clause |
| `version` | VARCHAR(20) | Document version this chunk belongs to |
| `created_at` | TIMESTAMP | Record creation time |

**Relationships:**
- `document` → belongs to one `Document`
- `citations` → has many `Citation` objects (which answers cite this chunk)

**Example:** "All passwords must be at least 12 characters long and changed every 90 days."

---

### 3. Entity — Extracted Rules/Thresholds

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Unique entity identifier |
| `type` | VARCHAR(50) | Entity type: "requirement", "deadline", "threshold", "approval" |
| `name` | VARCHAR(200) | Entity value (e.g., "12 characters", "90 days") |
| `document_id` | UUID (FK) | Links to source `Document` |
| `attrs` | JSONB | Additional attributes `{}` |
| `created_at` | TIMESTAMP | Record creation time |

**Relationships:**
- `document` → belongs to one `Document`

**Example entities from a document:**
- `{type: "requirement", name: "12 character minimum password length"}`
- `{type: "deadline", name: "90 day password rotation"}`

---

### 4. Query — Inbound Questions

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Unique query identifier |
| `user_id` | VARCHAR(100) | Who asked the question |
| `role` | VARCHAR(50) | User's access level (e.g., "level_1", "level_3") |
| `dept` | VARCHAR(100) | User's department |
| `question` | TEXT | The actual question asked |
| `intent` | VARCHAR(50) | Classified intent: "policy_lookup", "approval_check", etc. |
| `created_at` | TIMESTAMP | When the question was asked |

**Relationships:**
- `answers` → has many `Answer` objects (one per response attempt)

**Example:** "Who can approve a purchase over $10,000?"

---

### 5. Answer — LLM Responses

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Unique answer identifier |
| `query_id` | UUID (FK) | Links to the `Query` being answered |
| `answer` | TEXT | The generated response text |
| `verdict` | VARCHAR(20) | "clear", "abstained", "conflict", "conditional", "violation" |
| `confidence` | FLOAT | Confidence score (0.0 - 100.0) |
| `abstained` | BOOLEAN | `True` if the system refused to answer |
| `latency_ms` | INTEGER | Response time in milliseconds |
| `model_version` | VARCHAR(50) | Which LLM generated this (e.g., "deepseek-r1-distill-llama-8b") |
| `created_at` | TIMESTAMP | When the answer was generated |

**Relationships:**
- `query` → belongs to one `Query`
- `citations` → has many `Citation` objects
- `feedback` → has many `Feedback` objects

**Example:** "According to clause 4.2, the Finance Director can approve purchases over $10,000."

---

### 6. Citation — Answer-to-Source Links

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Unique citation identifier |
| `answer_id` | UUID (FK) | Links to the `Answer` making the citation |
| `chunk_id` | UUID (FK) | Links to the `Chunk` being cited |
| `document_id` | UUID (FK) | Links to the source `Document` |
| `clause_ref` | VARCHAR(100) | Clause reference (e.g., "4.2.1") |
| `score` | FLOAT | Relevance score from retrieval (0.0 - 1.0) |
| `created_at` | TIMESTAMP | Record creation time |

**Relationships:**
- `answer` → belongs to one `Answer`
- `chunk` → references one `Chunk`

**Example:** "This answer cites clause 4.2.1 from 'Information Security Policy' with score 0.92"

---

### 7. Feedback — Human Review Signal

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Unique feedback identifier |
| `answer_id` | UUID (FK) | Links to the `Answer` being rated |
| `rating` | INTEGER | 1-5 star rating (enforced by CHECK constraint) |
| `note` | TEXT | Optional reviewer comment |
| `reviewer` | VARCHAR(100) | Who provided the feedback |
| `created_at` | TIMESTAMP | When the feedback was submitted |

**Relationships:**
- `answer` → belongs to one `Answer`

**Example:** `{rating: 5, note: "Accurate and well-cited", reviewer: "admin"}`

---

### 8. AuditLog — Immutable Audit Trail

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Unique log identifier |
| `actor` | VARCHAR(100) | Who performed the action |
| `action` | VARCHAR(50) | Action type: "login", "query", "feedback", "register" |
| `payload` | JSONB | Action-specific data `{}` |
| `ts` | TIMESTAMP | When the action occurred |

**Relationships:** None (standalone, append-only)

**Example:** `{actor: "admin", action: "query", payload: {"question": "Who can approve $10K?"}}`

---

## Relationship Map

```
Document
  ├── (1:N) Chunk        ← clause-level text units
  └── (1:N) Entity       ← extracted rules/thresholds

Query
  └── (1:N) Answer       ← LLM responses
        ├── (1:N) Citation   ← links to source chunks
        │     ├── (N:1) Chunk
        │     └── (N:1) Document
        └── (1:N) Feedback   ← human review ratings

AuditLog                   ← standalone, no FK links
```

---

## Relationship Details

### Document → Chunk (One-to-Many)

```python
# Document model
chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")

# Chunk model
document = relationship("Document", back_populates="chunks")
document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'))
```

**Meaning:** One policy document contains many clause-level chunks. Deleting a document deletes all its chunks.

**Example:** "Information Security Policy" (Document) → 45 clauses (Chunks)

---

### Document → Entity (One-to-Many)

```python
# Document model
entities = relationship("Entity", back_populates="document")

# Entity model
document = relationship("Document", back_populates="entities")
document_id = Column(String(36), ForeignKey('documents.id'))
```

**Meaning:** One document yields many extracted entities (thresholds, deadlines, etc.).

**Example:** "Information Security Policy" → entities like "Password length: 12 chars", "Review deadline: 90 days"

---

### Query → Answer (One-to-Many)

```python
# Query model
answers = relationship("Answer", back_populates="query")

# Answer model
query = relationship("Query", back_populates="answers")
query_id = Column(String(36), ForeignKey('queries.id'))
```

**Meaning:** One question can have multiple answer attempts (e.g., retries, different models).

**Example:**
- Query: "Who can approve $10K?"
- Answer 1: "The Finance Director..." (from one run)
- Answer 2: (if retried with different parameters)

---

### Answer → Citation (One-to-Many)

```python
# Answer model
citations = relationship("Citation", back_populates="answer", cascade="all, delete-orphan")

# Citation model
answer = relationship("Answer", back_populates="citations")
answer_id = Column(String(36), ForeignKey('answers.id'))
```

**Meaning:** One answer cites multiple chunks as evidence.

**Example:**
- Answer: "According to clause 4.2 and 7.1..."
- Citation 1: links to chunk "clause-4.2" (score: 0.92)
- Citation 2: links to chunk "clause-7.1" (score: 0.85)

---

### Citation → Chunk (Many-to-One)

```python
# Citation model
chunk = relationship("Chunk", back_populates="citations")
chunk_id = Column(String(36), ForeignKey('chunks.id'))
```

**Meaning:** Citations point back to the specific chunk they reference.

---

### Citation → Document (Many-to-One)

```python
# Citation model
document_id = Column(String(36), ForeignKey('documents.id'))
```

**Meaning:** Citations also track which document the chunk came from.

---

### Answer → Feedback (One-to-Many)

```python
# Answer model
feedback = relationship("Feedback", back_populates="answer", cascade="all, delete-orphan")

# Feedback model
answer = relationship("Answer", back_populates="feedback")
answer_id = Column(String(36), ForeignKey('answers.id'))
```

**Meaning:** Users can rate/feedback on answers (1-5 stars + notes).

**Example:**
- Answer: "The Finance Director can approve..."
- Feedback 1: rating=5, note="Accurate"
- Feedback 2: rating=2, note="Missing policy version"

---

### AuditLog (Standalone)

```python
class AuditLog(Base):
    __tablename__ = 'audit_log'
    id = Column(String(36), primary_key=True)
    actor = Column(String(100))
    action = Column(String(50))
    payload = Column(JSON, default={})
    ts = Column(DateTime, default=datetime.utcnow)
```

**Meaning:** Immutable log of every action (login, query, feedback, register). Not linked to other tables — it's a flat audit trail for compliance.

---

## Query Flow Example

```python
# When user asks: "Who can approve a $10K purchase?"

# 1. Log the question → queries table
query = Query(id="q-1", user_id="admin", question="Who can approve $10K?")
session.add(query)

# 2. Retrieve relevant chunks → chunks table (via search.py)
chunks = session.query(Chunk).filter(Chunk.id.in_([ch1, ch2, ch3]))

# 3. LLM generates answer → stored in answers table
answer = Answer(id="a-1", query_id="q-1", answer="The Finance Director...", verdict="clear")
session.add(answer)

# 4. Record which chunks were cited → citations table
for chunk in chunks:
    citation = Citation(answer_id="a-1", chunk_id=chunk.id, score=0.92)
    session.add(citation)

# 5. Log the action → audit_log table
audit = AuditLog(actor="admin", action="query", payload={"question": "Who can approve $10K?"})
session.add(audit)

# 6. User rates the answer → feedback table
feedback = Feedback(answer_id="a-1", rating=5, reviewer="admin")
session.add(feedback)
```

---

## Key Design Decisions

| Decision | Why |
|----------|-----|
| `Document` and `Chunk` are separate | Documents are metadata; chunks are the actual searchable text |
| `Citation` links `Answer` → `Chunk` | Allows tracing every claim back to its source clause |
| `AuditLog` is standalone | Compliance requirement — immutable, append-only, no updates |
| `Feedback` links to `Answer` | Tracks human review per specific response |
| `Entity` is separate from `Chunk` | Entities are structured extractions (thresholds, deadlines); chunks are raw text |
| `embedding` is `Column(Text)` in models | SQLAlchemy doesn't know about pgvector — raw SQL handles vector operations |
