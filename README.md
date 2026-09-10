# Codex — Policy Intelligence Engine

Enterprise document intelligence system that ingests corporate policy documents (DOCX/PDF) and serves grounded, cited answers via API. **Every answer must cite a governing clause or abstain — no hallucinated responses allowed.**

---

## Architecture

Get Codex running in 4 simple steps on a new Ubuntu/Debian machine:

### 1. Install System Requirements
Ensure you have Python 3.10+, PostgreSQL 16+ (with `pgvector`), Neo4j 5.26+, `curl`, and `git` installed.

### 2. Clone and Setup
```bash
git clone <repository-url>
cd codex
bash infra/install_codex.sh
cp .env.example .env
```

### 3. Launch the System
```bash
bash infra/start_codex.sh
```
This starts the PostgreSQL database (in `.data/postgres`), Neo4j, the FastAPI backend, the Streamlit UI, and the ingestion worker.

### 4. Access and Login
- **UI URL**: `http://127.0.0.1:8501`
- **API URL**: `http://127.0.0.1:8000`

**Default Mock Credentials:**
| Username | Password | Access Level | Department |
|----------|----------|-------------|------------|
| `admin` | `password123` | 3 (Admin) | IT |
| `manager` | `password123` | 2 (Manager) | Finance |
| `employee` | `password123` | 1 (Employee) | HR |

---

## Architecture
...

---

## Configuration

All configuration lives in `.env`. Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://codex_admin:password@127.0.0.1:5432/codex_db` | PostgreSQL connection string |
| `NEO4J_URI` | `bolt://127.0.0.1:7687` | Neo4j Bolt connection |
| `LLM_PROVIDER` | `auto` | `api`, `local`, or automatic provider selection |
| `LLM_API_KEY` | — | Optional API key; omit it for keyless private endpoints |
| `API_PORT` | `8000` | FastAPI server port |
| `UI_PORT` | `8501` | Streamlit UI port |
| `SECRET_KEY` | — | JWT signing secret (change in production) |
| `SLACK_SIGNING_SECRET` | — | Slack HMAC verification |
| `SLACK_BOT_TOKEN` | — | Slack bot OAuth token |

See `.env.example` for the complete list of 50+ configurable variables.

---

## Start, Ingest, and Migrate

Complete setup first, then start the native services:

```bash
source .venv/bin/activate
python infra/check_dependencies.py
bash infra/start_codex.sh
```

Verify the API before ingesting documents:

```bash
curl http://127.0.0.1:8000/health
```

### Hosted API Mode

PostgreSQL, Neo4j, FastAPI, Streamlit, and the ingestion worker start normally.
Hosted API calls are used for clause detection, classification, graph
extraction, and answers. Local LLM servers and model files are not required.

### Recommended Document Ingestion: UI or API

The ingestion worker processes database records created by an explicit upload.
It does not watch the `archive/` directory automatically.

Using the UI:

1. Open `http://127.0.0.1:8501`.
2. Sign in with an admin account.
3. Upload a DOCX or PDF file.
4. Wait for the ingestion worker to process it.
5. Check the ingestion status and refresh the search index.

Using the API:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/login \
  -d "username=admin&password=password123" \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -X POST http://127.0.0.1:8000/v1/ingest \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/policy.docx"

curl http://127.0.0.1:8000/v1/ingestion/status \
  -H "Authorization: Bearer $TOKEN"

curl -X POST http://127.0.0.1:8000/admin/refresh-index \
  -H "Authorization: Bearer $TOKEN"
```

The worker performs parsing, clause detection, chunking, embedding, graph
generation, and database persistence for each uploaded document.

The authentication dependency includes `argon2-cffi`, which is required for
admin login. If login returns a 500 error mentioning Argon2, repair the Python
environment and restart the API:

```bash
python infra/check_dependencies.py
bash infra/unused/stop.sh
bash infra/start_codex.sh
```

### Full Batch Reset and Migration

Use this workflow only for a new corpus or when you intentionally want to
delete all existing policy data. Place DOCX/PDF files in `archive/`, then run:

```bash
bash infra/reset_and_reingest.sh --reset
bash infra/run_graph_migration.sh --reset
```

Both `--reset` flags are mandatory and destructive. The first command clears
PostgreSQL data and ingests the archive. The second clears and rebuilds Neo4j.
This workflow uses the configured hosted API for clause detection and graph
generation.

### Incremental Batch Ingestion

For additional archive documents without deleting existing data:

```bash
source .venv/bin/activate
python services/ingestion/ingest.py
python services/ingestion/migrate_to_neo4j.py --incremental
curl -X POST http://127.0.0.1:8000/admin/refresh-index \
  -H "Authorization: Bearer $TOKEN"
```

For hosted API mode, prefer the UI/API upload workflow above. It uses the
configured provider and keeps ingestion state in PostgreSQL.

### Ingestion Pipeline Details

```
archive/*.docx
  │
  ├── doc_parser.py        Parse DOCX/PDF structure → sections
  │
  ├── clause_detector.py   Split sections into individual clauses (hosted LLM API)
  │
  ├── structure_chunker.py Clause-level chunks with 3-token overlap
  │
  ├── ingest.py            Generate embeddings (bge-large-en-v1.5, 1024 dims)
  │                        Extract entities (thresholds, approvals, deadlines)
  │                        Store in PostgreSQL (chunks table with vector column)
  │
  └── migrate_to_neo4j.py  Build knowledge graph in Neo4j (LLM-powered)
                           Policy → Clause (PART_OF)
                           Role → Process (CAN_APPROVE)
                           Policy → Regulation (MAPS_TO)
```

### Adding New Documents After Initial Ingestion

For hosted API mode, upload each new document through the UI or
`POST /v1/ingest`; the worker uses the configured hosted provider.

For local batch mode, place new DOCX/PDF files in `archive/` and run:

```bash
source .venv/bin/activate
python services/ingestion/ingest.py
python services/ingestion/migrate_to_neo4j.py --incremental
curl -X POST http://127.0.0.1:8000/admin/refresh-index \
  -H "Authorization: Bearer <admin_token>"
```

---

## Querying Codex

### Via Streamlit UI

Open `http://127.0.0.1:8501` and log in. Ask questions in natural language.

### Via API

```bash
# Login
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/login \
  -d "username=admin&password=password123" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# Ask a question
curl -X POST http://127.0.0.1:8000/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Who can approve a purchase over $10,000?", "search_mode": "hybrid"}'
```

### Via CLI

```bash
source .venv/bin/activate
python -m services.chat.cli
```

### Search Modes

| Mode | Description |
|------|-------------|
| `hybrid` | Vector + BM25 with RRF fusion (default, best results) |
| `vector` | Semantic similarity only |
| `bm25` | Keyword matching only |

---

## Sample Queries

### Quick Summary

| Intent | Example Question | What Codex Does |
|--------|-----------------|-----------------|
| general | "What does the Backup Policy require?" | Retrieves relevant clauses, generates grounded answer |
| procedure | "What are the steps for incident response?" | Retrieves step-by-step process with citations |
| approval | "Who can approve a purchase over $10,000?" | Checks Neo4j knowledge graph for approval authority |
| compliance | "Are we compliant with data protection?" | Classifies clear/conditional/violation with evidence |
| conflict (Type 1 Mode A) | "Are there password conflicts?" | Topic search — finds clause pairs with contradictory requirements |
| conflict (Type 1 Mode B) | "Our policy requires 90-day passwords. Does this conflict?" | Clause vs corpus — checks specific requirement against all policies |
| conflict (Type 2) | "Compare BYOD and Remote Access for conflicts" | Doc vs doc — compares two named documents |
| conflict (Type 2b) | "Which policies contradict the Software Installation Policy?" | Doc vs corpus — audits one document against all others |
| conversational | "Hello" | Friendly greeting, no policy lookup |

---

### Step-by-Step: How a Query Flows

1. **Login** → get JWT token
2. **POST /query** with your question
3. **Intent classification** → determines which agent handles the query
4. **Retrieval** → hybrid search (vector + BM25 + reranking) finds relevant chunks
5. **Agent execution** → specialized agent processes the query:
   - **Conflict**: 2-phase pipeline (resolve documents → evaluate clause pairs)
   - **Approval**: Neo4j knowledge graph lookup
   - **Compliance**: Risk assessment against regulations
   - **Procedure/General**: LLM generates grounded answer with citations
6. **Verification** → citations validated, confidence scored

---

### General QA

#### Q: "What does the Backup Policy require?"
- **Intent:** general | **Verdict:** clear
- **How it works:** Retrieves chunks about backup policy, LLM generates a structured summary.
- **Answer:** "The Backup Policy requires: 1) Critical asset prioritization — backups must target selected critical assets using imaging processes. 2) Regular and secure backups — performed regularly, stored securely, easily restorable. 3) Retention periods enforced."

#### Q: "What are the steps for the incident response process?"
- **Intent:** procedure | **Verdict:** clear
- **How it works:** Retrieves the Incident Response Plan, LLM extracts step-by-step process.
- **Answer:** "Here's how to do this, step by step: 1. **Detect Incident**: SOC team reports suspected security incidents. 2. **Analyze and Contain**: Incident Response Team gathered ASAP. 3. **Eradicate**: Remove the threat. 4. **Recover**: Restore systems. 5. **Post-Incident Review**: Lessons learned."

#### Q: "Who can approve a purchase over $10,000?"
- **Intent:** approval | **Verdict:** abstained
- **How it works:** Queries Neo4j knowledge graph for CAN_APPROVE relationships.
- **Answer:** "The provided policies do not specify who can approve purchases over $10,000. The knowledge graph confirms no matching approval chain exists for this threshold. Consult a manager or policy owner."

#### Q: "Are we compliant with data protection regulations?"
- **Intent:** compliance | **Verdict:** violation
- **How it works:** Retrieves regulatory clauses, LLM assesses obligation compliance.
- **Answer:** "Mandatory requirements not fully satisfied: DPO appointment by 31 Dec 2026 required for HL/UHL/EHL tiers. Certification accredited by the Commission required within three years. Records of processing activities must be maintained."

---

### Conflict Detection

Codex detects policy contradictions through 3 conflict types with 2 modes:

| Type | Description | When triggered |
|------|-------------|---------------|
| **Type 1** | Clause vs corpus — finds conflicts among semantically similar clauses across all policies | No specific document named (topic search) |
| **Type 2** | Doc vs doc — compares two specific named documents | Two document names in query |
| **Type 2b** | Doc vs corpus — audits one document against all other policies | One document name in query |

#### Conflict Type 1 — Mode A: Topic Search

The user asks about conflicts on a topic. Codex retrieves relevant clauses, expands with similar clauses from other documents, and evaluates for contradictions.

**Q: "Are there password conflicts across our policies?"**
- **Intent:** conflict | **Type:** type_1 | **Verdict:** clear
- **How it works:** Retrieves password-related clauses, expands to similar clauses across all docs, evaluates 150 pairs.
- **Answer:** "I compared the relevant policy clauses and everything looks consistent — no conflicting requirements found. Evaluated the 150 most similar cross-policy clause pairs."

#### Conflict Type 1 — Mode B: Specific Requirement vs Corpus

The user states a specific requirement and asks whether it conflicts with other policies. Codex evaluates the requirement against all similar clauses across the corpus.

**Q: "Our policy requires passwords to be changed every 90 days. Does this conflict with any other policy?"**
- **Intent:** conflict | **Type:** type_1 | **Verdict:** conflict
- **How it works:** Identifies the 90-day requirement, searches for similar clauses across all policies, finds contradictory retention periods.
- **Answer:** "I found 2 conflicts: 1) Data Retention Policy states logs retained for 90 days, while Log Management Policy requires admin logs retained for 6 months — threshold mismatch. 2) Log Management Policy specifies different retention for log types within the same document."

**Q: "Do any of our policies contradict the requirement for annual security training?"**
- **Intent:** conflict | **Type:** type_1 | **Verdict:** conflict
- **How it works:** Identifies the annual training requirement, finds the Business Continuity Plan mandates 6-month training instead.
- **Answer:** "I found a conflict: Incident Response Plan requires annual security training, but Business Continuity/Disaster Recovery Plan mandates training every 6 months — different timeframes for the same obligation."

**Q: "Is the 90-day password rotation requirement consistent across all policies?"**
- **Intent:** conflict | **Type:** type_1 | **Verdict:** clear
- **Answer:** "Evaluated 150 pairs — no conflicting requirements found for the 90-day password rotation requirement."

#### Conflict Type 2 — Doc vs Doc

The user names two documents and asks Codex to compare them for contradictions.

**Q: "Compare BYOD and Remote Access for conflicts"**
- **Intent:** conflict | **Type:** type_2 | **Verdict:** conflict
- **How it works:** Fetches chunks from both documents, builds pairwise clause comparisons, evaluates each pair.
- **Answer:** "I found 3 conflicts: 1) BYOD restricts non-company-owned devices from internal network, while Remote Access defines standards for external access — scope contradiction. 2) BYOD ensures confidentiality of PII, Remote Access doesn't address PII protection — gap in data security. 3) BYOD requires corporate/personal environment separation, Remote Access mandates company-provided remote access software only."

**Q: "Compare Software Installation Policy and Incident Response Plan"**
- **Intent:** conflict | **Type:** type_2 | **Verdict:** conflict
- **Answer:** "I found 3 conflicts: Software Installation Policy allows productivity tools including VPN/EDR, while Incident Response Plan explicitly blocks remote VPN connections — direct contradiction on VPN usage."

#### Conflict Type 2b — Doc vs Corpus

The user names one document and asks Codex to audit it against all other policies.

**Q: "Which other policies conflict with the Software Installation Policy?"**
- **Intent:** conflict | **Type:** type_2b | **Verdict:** conflict
- **How it works:** Uses `find_similar_clauses` across all docs except target, evaluates per candidate document (10 docs max), one LLM batch per doc.
- **Answer:** "Evaluated clauses from 10 candidate document(s) against the target. Found conflicts: Software Installation allows VPN/EDR, Incident Response blocks VPN connections. Software Installation assigns asset management responsibility, Change Management assigns it differently."

**Q: "Which policies contradict the Remote Access Policy?"**
- **Intent:** conflict | **Type:** type_2b | **Verdict:** conflict
- **Answer:** "Evaluated clauses from 10 candidate document(s) against the target. Found conflict: Remote Access Policy contradicts Removable Media Policy on data transfer methods."

---

### Conversational

**Q: "Hello"**
- **Intent:** conversational | **Verdict:** clear
- **Answer:** "Hello! How can I assist you today? If you have questions about policies, compliance, or procedures, feel free to ask!"

---

## Agents

| # | Agent | File | Purpose |
|---|-------|------|---------|
| 1 | Orchestrator | `services/agents/orchestrator.py` | State machine. Classifies intent, routes to specialized agents. |
| 2 | Retriever | `services/api/search.py` | Vector + BM25 → RRF fusion → CrossEncoder rerank → top 5 |
| 3 | Reasoner | `services/agents/reasoner.py` | Hosted LLM generates grounded answer with citations |
| 4 | Verifier | `services/agents/verifier.py` | Validates all citations against actual chunks |
| 5 | Approval-Matrix | `services/agents/approval_agent.py` | Neo4j query for approval authority |
| 6 | Conflict Detector | `services/agents/conflict_agent.py` | Detects contradictions in clauses |
| 7 | Risk & Compliance | `services/agents/risk_agent.py` | Classifies clear/conditional/violation |
| 8 | Ingestion Worker | `services/ingestion/ingestion_agent.py` | Background processor for UI/API uploads |

---

## Guardrails: Cite-or-Abstain Chain

1. **RBAC** — JWT carries `access_level`; retrieval filters chunks by level
2. **Retrieval hygiene** — Low-information chunks excluded from search
3. **System Prompt** — LLM instructed: "Use ONLY provided context"
4. **Citation Verification** — All `[Doc, Clause]` citations validated against actual chunks
5. **Confidence Threshold** — Multi-signal scoring; low confidence → abstained verdict

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/register` | Public | Register a user |
| POST | `/login` | Public | Get JWT token |
| POST | `/query` | User | Ask a policy question |
| GET | `/query/history` | User | User's query history |
| POST | `/feedback` | User | Rate an answer (1-5) |
| GET | `/documents` | User | List documents |
| GET | `/documents/{id}` | User | Document detail |
| GET | `/chunks` | User | Browse chunks |
| GET | `/entities` | User | List entities |
| GET | `/departments` | User | Departments from Neo4j |
| GET | `/audit` | Admin | Audit log |
| POST | `/admin/refresh-index` | Admin | Rebuild BM25 index |
| POST | `/v1/ingest` | Admin | Upload document |
| GET | `/v1/ingestion/status` | Admin | Ingestion status |
| GET | `/v1/metrics` | Admin | System metrics |
| GET | `/health` | Public | System health |

---

## Mock Users

| Username | Password | Access Level | Department |
|----------|----------|-------------|------------|
| admin | password123 | 3 (admin) | IT |
| manager | password123 | 2 (manager) | Finance |
| employee | password123 | 1 (employee) | HR |

---

## Slack Integration

Codex answers policy questions from Slack via the Slack Events API.

### Setup

1. Create a Slack app at https://api.slack.com/apps
2. Add scopes: `chat:write` and `app_mentions:read`
3. Enable Events, subscribe to `app_mention` and `message.im`
4. Set request URL to `https://<your-host>/v1/integrations/slack`
5. Copy Bot Token and Signing Secret to `.env`:

```bash
SLACK_SIGNING_SECRET=v0-your-signing-secret
SLACK_BOT_TOKEN=xoxb-your-bot-token
```

### User Mapping

Map Slack user IDs to Codex usernames in `integrations/slack_users.json`:

```json
{
  "U0123456789": "admin",
  "U0ABCDEFGH1": "manager"
}
```

> **Dev mode:** If `SLACK_SIGNING_SECRET` is unset, signature check is skipped.

---

## Testing

```bash
source .venv/bin/activate

python tests/test_answer_formatting.py
python tests/test_clause_detection_cot.py
python tests/test_graph_sanitizer.py
python tests/test_history.py
python tests/test_intent_routing.py
python tests/test_rbac.py
python tests/test_retrieval_quality.py
python tests/test_slack_integration.py
```

The chunking fixture tests require the referenced password-management document
to exist at `archive/UnderDefense MAXI - Password management policy.docx`. If
that document is not included in the checkout, those fixture tests are skipped
or reported as unavailable; this is separate from dependency validation.

`tests/test_retrieval_quality.py` requires a populated, current policy corpus.
Run ingestion and refresh the BM25 index before treating retrieval-quality
failures as code failures.

### Upload Troubleshooting

If the UI reports `500 Server Error` while uploading, check the API log first:

```bash
tail -n 100 logs/fastapi.log
```

The `CORS_ORIGINS` value in `.env` must remain valid JSON wrapped in shell
quotes:

```env
CORS_ORIGINS='["http://127.0.0.1:8501"]'
```

After changing `.env`, restart the API before retrying the upload. A successful
upload returns `status: queued`; the document becomes queryable only after its
ingestion status is `ready` and graph generation is complete.

---

## Project Structure

```
codex/
├── services/
│   ├── api/               # FastAPI app, routers, dependencies, search
│   ├── agents/            # Orchestrator + 7 specialized agents + tools
│   ├── ingestion/         # Parse, chunk, embed, graph generation, BM25
│   └── chat/              # CLI chat interface + Streamlit UI
├── integrations/          # Chat webhooks: Slack (signed), Teams (stub)
├── packages/
│   └── shared/            # Models, schemas, db, auth, config, doc_parser, confidence
├── archive/               # Source policy documents (drop .docx/.pdf here)
├── infra/                 # Startup scripts, DB init, migration tools, unused/archived scripts
├── tests/                 # Test suites
├── doc/                   # Architecture documentation
├── .env.example           # Environment variable template (50+ variables)
└── requirements.txt       # Python dependencies (17 packages)
```

---

## Useful Commands

```bash
# Full destructive reset and re-ingestion
bash infra/reset_and_reingest.sh --reset
bash infra/run_graph_migration.sh --reset

# Start everything locally
bash infra/start_codex.sh

# Refresh BM25 index after ingestion
curl -X POST http://127.0.0.1:8000/admin/refresh-index \
  -H "Authorization: Bearer <admin_token>"

# Check ingestion status
curl http://127.0.0.1:8000/v1/ingestion/status \
  -H "Authorization: Bearer <admin_token>"
```
