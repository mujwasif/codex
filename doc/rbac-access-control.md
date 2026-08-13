# Access Control & RBAC

How Codex assigns a security level to every policy document and uses it to filter what each user can see.

## Access Levels

| Level | Role | Sees |
|-------|------|------|
| 1 | employee | level-1 documents only |
| 2 | manager | level-1 and level-2 documents |
| 3 | admin | all documents |

The rule is a simple comparison: a user may read a document iff `user_level >= document_level` (`can_access` in `packages/shared/access_control.py`).

## How Document Levels Are Assigned (`infer_access_level`)

Assignment is a priority chain — the first match wins and the function returns immediately:

1. **Explicit tags** (highest priority)
   - Numeric forms: `level:3`, `access_level=2`, `access:1`, `sensitivity-3`, bare `3`
   - Named sensitivity tags at level 2+: `confidential`, `restricted`, `secret`, `classified` (level 3); `manager`, `managerial` (level 2)
   - Level-1 names (`public`, `internal`, `employee`) are **not binding** — the ingestion layer stamps `internal` on every document by default, so treating it as binding would block keyword-based promotion. Use a numeric form (`level:1`) to pin a document to level 1.
2. **Title/filename keywords**
   - Level 3: `confidential`, `restricted`, `secret`, `top-secret`, `classified`, `salary`, `compensation`, `bonus`, `termination`, `litigation`, `legal`, `audit-finding`, `executive`
   - Level 2: `manager`, `management`, `hr`, `human-resource`, `finance`, `financial`, `payroll`, `budget`, `forecast`, `strategy`, `strategic`, `board`
3. **Body keywords** (first `SAMPLE_CHARS` = 5000 chars only)
   - Level 3: `salary`, `compensation`, `bonus`, `termination`, `litigation`, `confidential`, `restricted`, `classified`
   - Level 2: `payroll`, `budget`, `forecast`, `performance review`, `board of directors`
4. **Default**: level 1.

Matching is case-insensitive and substring-based. Level 3 is always checked before level 2, so a title like `Manager's Secret Salary Report` resolves to level 3.

## How the Level Is Used

There are two phases:

### 1. Ingestion (stamping)
When a document is ingested, `infer_access_level` runs and the resulting number is stored in the `documents.access_level` column in PostgreSQL. It is just a label at this point.

### 2. Querying (filtering)
When a user asks a question, `search_policy` in `services/api/search.py`:

- reads the user's level from the JWT (`access_level` claim),
- adds a SQL predicate to the retrieval query:
  ```sql
  WHERE documents.access_level <= <user_level>
  ```
- restricted chunks never reach the reasoner LLM, so it cannot summarize or leak them.

## Design Notes

### Document-level, not clause-level
The smallest unit of security is the whole document. A document stamped level 3 is treated as level 3 entirely, even if most of its content is low-sensitivity. This is a deliberate conservative choice:

- **Pros** — no leakage-by-proximity, fast integer filtering in SQL, easy to audit, restricted text never reaches the LLM.
- **Cons** — over-blocking (a mostly-public handbook with one admin page becomes admin-only), coarse granularity, and inference relies on keywords (an untagged secret doc could default to level 1).

### Token payload
The JWT carries `{"username": ..., "access_level": ...}`, minted by `create_access_token` (same payload as `/login` and the Slack integration). Slack users are mapped via `integrations/slack_users.json`, so an admin-tagged Slack user gets level-3 answers, an unmapped user gets employee (level 1).

## Key Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `clamp_access_level(level)` | access_control.py:101 | Coerce any value into 1–3 |
| `can_access(user_level, doc_level)` | access_control.py:110 | Gatekeeper check (`user >= doc`) |
| `_explicit_level_from_tags(tags)` | access_control.py:128 | Parse explicit numeric/named tags |
| `infer_access_level(title, tags, text)` | access_control.py:173 | Assign a level to a document |
| `search_policy(...)` | services/api/search.py | Applies the SQL `access_level` filter |
