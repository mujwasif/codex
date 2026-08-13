# Codex — Policy Intelligence Engine

Enterprise document intelligence system that ingests corporate policy documents (DOCX/PDF) and serves grounded, cited answers via API. **Every answer must cite a governing clause or abstain — no hallucinated responses allowed.**

---

## Architecture

```
                    INGESTION (offline)
                    ===================
archive/*.docx
  → doc_parser.py          (parse DOCX/PDF structure)
  → clause_detector.py     (split into individual clauses via Qwen3-4B LLM)
  → structure_chunker.py   (clause-level chunks with overlap)
  → ingest.py              (embed with bge-large-en-v1.5, store in PostgreSQL)
  → migrate_to_neo4j.py    (build knowledge graph via LLM)

                    QUERY (online)
                    ==============
User: "Who can approve a purchase over $10,000?"
  → /query endpoint         (authenticate, orchestrate pipeline)
  → search.py               (vector + BM25 → RRF fusion → CrossEncoder rerank → top 5)
  → orchestrator.py         (intent classification → specialized agent pipeline)
  → reasoner.py             (Qwen3-8B generates grounded answer with citations)
  → verifier.py             (regex check — all citations match retrieved chunks?)
  → confidence.py           (multi-signal confidence score as percentage)
  → Response: {answer, verdict, confidence, reasoning, next_steps, missing, citations}
```

---

## Agents

| # | Agent | File | Purpose |
|---|-------|------|---------|
| 1 | Orchestrator | `services/agents/orchestrator.py` | Pure Python state machine. Classifies intent, selects pipeline, routes through specialized agents. No LangChain/LangGraph. |
| 2 | Retriever | `services/api/search.py` | Three-stage retrieval: vector + BM25 → RRF fusion → CrossEncoder rerank. Modes: `vector`, `bm25`, `hybrid`. |
| 3 | Reasoner | `services/agents/reasoner.py` | Sends top-5 chunks + question to Qwen3-8B with a strict cite-or-abstain prompt. Temperature 0.0. |
| 4 | Verifier | `services/agents/verifier.py` | Regex-extracts `[Doc: X, Clause: Y]` citations and validates each against actual chunks. |
| 5 | Approval-Matrix | `services/agents/approval_agent.py` | Neo4j query for who can approve a process, amount thresholds, user authority. |
| 6 | Conflict Detector | `services/agents/conflict_agent.py` | Detects contradictions via Neo4j CONFLICTS_WITH, version mismatches, and LLM pairwise checks. |
| 7 | Risk & Compliance | `services/agents/risk_agent.py` | Classifies verdict as `clear`/`conditional`/`violation`, maps to regulations, generates recommendations. |
| 8 | Ingestion Worker | `services/ingestion/ingestion_agent.py` | Explicit queue worker; processes only documents uploaded via `POST /v1/ingest` (no `archive/` folder watching). |

### Intent Pipeline Routing

| Intent | Pipeline |
|--------|----------|
| APPROVAL | retrieve → approval → reason → verify |
| CONFLICT | retrieve → conflict_check → reason → verify |
| COMPLIANCE | retrieve → risk_compliance → reason → verify |
| PROCEDURE | retrieve → reason → verify |
| GENERAL | retrieve → reason → verify |

---

## Guardrails: Cite-or-Abstain Chain

1. **RBAC** — JWT carries `access_level`; retrieval filters chunks by level.
2. **Retrieval hygiene** — Low-information chunks (prompt leaks, table junk, bare cross-references) are excluded from vector + BM25 retrieval; the reranker sees document titles so the right policy surfaces.
3. **System Prompt** — LLM instructed: "Use ONLY provided context"; says "Insufficient policy basis" otherwise.
4. **Citation Verification** — All `[Doc, Clause]` citations must match retrieved chunks.
5. **Confidence Threshold** — Multi-signal scoring; low confidence → abstained verdict.

---

## Setup

### Prerequisites

- PostgreSQL with pgvector
- Neo4j
- llama.cpp servers for Qwen3-4B (8081) and Qwen3-8B (8080)

### Environment

```bash
cp .env.example .env   # then edit values
```

### Start Services

```bash
# PostgreSQL
export PATH="/home/mujtaba/postgresql/bin:$PATH"
pg_ctl -D /home/mujtaba/pgdata -l /home/mujtaba/pgdata/logfile start

# LLM servers
./infra/server_qwen3.sh      # Qwen3-4B (ingestion) — port 8081
./infra/server.sh            # Qwen3-8B (queries)   — port 8080

# FastAPI
source /home/mujtaba/new_folder/fastmcp/venv/bin/activate
cd /home/mujtaba/new_folder/codex
PYTHONPATH=/home/mujtaba/new_folder python3 -m uvicorn services.api.main:app --host 0.0.0.0 --port 8000

# Background ingestion agent
PYTHONPATH=/home/mujtaba/new_folder/codex nohup python3 -m services.ingestion.ingestion_agent \
    > /tmp/ingestion_agent.log 2>&1 &
```

### Ingest Documents

```bash
# Place .docx/.pdf files in archive/, then:
PYTHONPATH=/home/mujtaba/new_folder/codex python3 services/ingestion/ingest.py

# Build the Neo4j knowledge graph:
PYTHONPATH=/home/mujtaba/new_folder/codex python3 services/ingestion/migrate_to_neo4j.py
```

Or upload via API (admin only):

```bash
curl -X POST http://localhost:8000/v1/ingest \
  -H "Authorization: Bearer <admin_token>" \
  -F "file=@/path/to/policy.docx"
```

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/register` | Public | Register a user |
| POST | `/login` | Public | Get JWT token |
| POST | `/query` | User | Ask a policy question |
| GET | `/query/history` | User | User's query history (question + answer + verdict, latest 50) |
| POST | `/feedback` | User | Rate an answer (1-5) |
| GET | `/feedback/{answer_id}` | User | Feedback for an answer |
| GET | `/documents` | User | List documents |
| GET | `/documents/{id}` | User | Document detail (chunks + entities) |
| GET | `/chunks` | User | Browse/search chunks |
| GET | `/chunks/{id}` | User | Chunk detail |
| GET | `/entities` | User | List extracted entities |
| GET | `/departments` | User | Departments from Neo4j |
| GET | `/audit` | Admin | Audit log |
| POST | `/admin/refresh-index` | Admin | Rebuild BM25 index |
| POST | `/v1/ingest` | Admin | Upload document for background ingestion |
| GET | `/v1/metrics` | Admin | System telemetry |
| POST | `/v1/integrations/slack` | Slack signature | Slack Events API inbound webhook (signed) |
| POST | `/v1/integrations/teams` | — | Teams webhook (not implemented — 501) |
| GET | `/health` | Public | System health |

### Mock Users

| Username | Password | Access Level | Department |
|----------|----------|-------------|------------|
| admin | password123 | 3 | IT |
| manager | password123 | 2 | Finance |
| employee | password123 | 1 | HR |

### Example Query

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/login -d "username=admin&password=password123" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -X POST http://localhost:8000/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Who can approve a purchase over $10,000?", "search_mode": "hybrid"}'
```

---

## Slack Integration

Codex answers policy questions from Slack via the **Slack Events API** — no Socket Mode, no external reverse proxy required (only needs an inbound HTTPS URL).

### How it works

1. Slack sends the app-mention / DM event to `POST /v1/integrations/slack`.
2. Codex verifies Slack's HMAC signature (`X-Slack-Signature`) over the raw body.
3. The Slack user ID is mapped to a Codex username via `integrations/slack_users.json` (unmapped → `employee`, level 1).
4. Codex mints an internal JWT (same payload as `/login`), runs the normal query pipeline, and posts the answer back **in-thread**.
5. Context is the server's source of truth: `/query` loads the last 5 turns from the `queries`/`answers` tables for that user, so conversational memory works the same across CLI, UI, and Slack.

### Slack App Setup

1. Create a Slack app at https://api.slack.com/apps.
2. Add scopes: `chat:write` and `app_mentions:read`.
3. Enable **Events**, subscribe to `app_mention` and `message.im`, and set the request URL to:
   `https://<your-host>/v1/integrations/slack`
4. Install the app to your workspace and copy the **Bot User OAuth Token** (`xoxb-…`).
5. In your app's **Basic Information → App Credentials**, copy the **Signing Secret**.

### Environment

```bash
SLACK_SIGNING_SECRET=v0-your-signing-secret
SLACK_BOT_TOKEN=xoxb-your-bot-token
CODEX_API_URL=http://localhost:8000
```

> **Dev mode:** if `SLACK_SIGNING_SECRET` is unset, the signature check is skipped and replies are dry-run logged — handy for local testing.

### User Mapping

Map Slack user IDs to Codex usernames in `integrations/slack_users.json`:

```json
{
  "U0123456789": "admin",
  "U0ABCDEFGH1": "manager"
}
```

### Local Smoke Test

```bash
# Boot Codex, then send a signed fake payload:
./infra/server_slack_test.sh
```

> Production requires an HTTPS endpoint; use a tunnel (e.g. ngrok) only for testing — it is not part of the supported architecture.

---

## Testing

```bash
PYTHONPATH=/home/mujtaba/new_folder/codex python3 tests/test_chunking.py
PYTHONPATH=/home/mujtaba/new_folder/codex python3 tests/test_reasoning.py
PYTHONPATH=/home/mujtaba/new_folder/codex python3 tests/test_clause_detection_cot.py
PYTHONPATH=/home/mujtaba/new_folder/codex python3 tests/test_graph_sanitizer.py
PYTHONPATH=/home/mujtaba/new_folder/codex python3 tests/test_rbac.py
PYTHONPATH=/home/mujtaba/new_folder/codex python3 tests/test_slack_integration.py
```

---

## Project Structure

```
codex/
├── services/
│   ├── api/               # FastAPI app, routers, dependencies, search
│   ├── agents/            # Orchestrator + 7 specialized agents + tools
│   ├── ingestion/         # Parse, chunk, embed, graph generation, BM25
│   └── chat/              # CLI chat interface
├── integrations/          # Chat webhooks: Slack (signed), Teams (stub)
├── packages/
│   └── shared/            # Models, schemas, db, auth, doc_parser, confidence
├── archive/               # Source policy documents
├── infra/                 # Server startup scripts
├── tests/                 # Test suites
└── web/                   # Streamlit UI (optional)
```
