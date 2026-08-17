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
  → Response: {answer, verdict, confidence, citations, search_mode}
```

---

## Prerequisites

| Component | Version | Purpose |
|-----------|---------|---------|
| Python | 3.12+ | Runtime |
| PostgreSQL + pgvector | 16+ | Vector storage + chunk metadata |
| Neo4j | 5.26+ | Knowledge graph |
| llama.cpp | latest | LLM inference (Qwen3-8B + Qwen3-4B) |
| NVIDIA GPU | RTX 4060 8GB+ | GPU acceleration for LLMs |
| CUDA | 12.1+ | GPU driver compatibility |

---

## Recommended Setup

Codex runs natively and supports three LLM modes:

- `api`: hosted LLM using an API key
- `local`: llama.cpp with local Qwen models
- `auto`: uses API mode when a key exists, otherwise local mode

For a new Ubuntu/Debian machine, the complete installer is:

```bash
git clone <repository-url>
cd codex
bash infra/install_codex.sh --llm api
```

Use `--llm local` for llama.cpp and local Qwen models, or `--llm auto` to
choose automatically. The installer creates `.env`, installs system and
Python dependencies, initializes the databases, and validates the deployment.

### 1. Install System Requirements

Required on the target machine:

- Python 3.10+
- PostgreSQL with the `pgvector` extension
- Neo4j 5.26+
- `curl`
- `git`

For local LLM mode, the installer builds llama.cpp and downloads the required
models when their download URLs are configured in `.env`:

- Qwen3-8B GGUF model
- Qwen3-4B GGUF model

### 2. Clone and Configure

```bash
git clone <repository-url>
cd codex
cp .env.example .env
```

Edit `.env` with the values for the target machine.

For hosted API mode:

```env
LLM_PROVIDER=api
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_INGESTION_MODEL=gpt-4o-mini
```

For local llama.cpp mode:

```env
LLM_PROVIDER=local
LLAMA_CPP_BIN=/path/to/llama-server
MODELS_DIR=/path/to/models
QWEN3_8B_MODEL_PATH=/path/to/models/Qwen3-8B-Q4_K_M.gguf
QWEN3_4B_MODEL_PATH=/path/to/models/Qwen3-4B-Instruct-2507-UD-Q4_K_XL.gguf
```

For automatic selection:

```env
LLM_PROVIDER=auto
```

### 3. Configure PostgreSQL

If PostgreSQL is already installed and running, configure it explicitly:

```env
PG_BIN=/path/to/postgresql/bin
PG_DATA=/path/to/postgresql/data
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_USER=codex_admin
POSTGRES_PASSWORD=your_password
POSTGRES_DB=codex_db
DATABASE_URL=postgresql://codex_admin:your_password@127.0.0.1:5432/codex_db
```

When using a local PostgreSQL data directory, startup initializes the cluster,
creates the database and user, enables `pgvector`, and loads the schema.

### 4. Configure Neo4j

```env
NEO4J_HOME=/path/to/neo4j
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USER=neo4j
NEO4J_PASS=your_neo4j_password
```

If Neo4j is managed externally, start it before Codex. If it is installed
locally, `start_codex.sh` attempts to start it.

### 5. Create the Codex Environment

Do not use an external virtualenv. Create the project-local environment:

```bash
bash infra/setup_venv.sh
source .venv/bin/activate
```

For CUDA-enabled PyTorch:

```bash
CODEX_CUDA=true bash infra/setup_venv.sh
```

The setup script creates `codex/.venv` and does not modify virtualenvs outside
the repository.

### 6. Validate Dependencies

```bash
python infra/check_dependencies.py
```

The command must report:

```text
All Codex Python dependencies are installed and importable.
```

### 7. Start Codex

```bash
bash infra/start_codex.sh
```

This starts PostgreSQL, Neo4j, FastAPI, Streamlit, and the ingestion worker.
Local llama.cpp servers start only when the resolved LLM mode is `local`.

### 8. Verify

```bash
curl http://127.0.0.1:8000/health
```

Open the UI at `http://127.0.0.1:8501`.

Logs are stored in `logs/`.

### Stop Codex

```bash
bash infra/unused/stop.sh
```

Important: `.env` contains passwords and API keys. Never commit it. Rotate any
credentials that have previously been exposed in a local `.env` file.

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
| `QWEN3_8B_MODEL_PATH` | `${MODELS_DIR}/Qwen3-8B-Q4_K_M.gguf` | Path to Qwen3-8B model |
| `QWEN3_4B_MODEL_PATH` | `${MODELS_DIR}/Qwen3-4B-Instruct-2507-UD-Q4_K_XL.gguf` | Path to Qwen3-4B model |
| `LLM_8B_PORT` | `8080` | Qwen3-8B server port |
| `LLM_4B_PORT` | `8081` | Qwen3-4B server port |
| `GPU_LAYERS_8B` | `30` | GPU layers to offload for 8B |
| `GPU_LAYERS_4B` | `10` | GPU layers to offload for 4B |
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

With `LLM_PROVIDER=api`, PostgreSQL, Neo4j, FastAPI, Streamlit, and the
ingestion worker start normally. Hosted API calls are used for clause detection,
classification, graph extraction, and answers. Local llama.cpp and Qwen GGUF
files are not required.

### Local llama.cpp Mode

With `LLM_PROVIDER=local`, `start_codex.sh` also starts the local Qwen3-8B and
Qwen3-4B llama.cpp servers. Verify both servers before ingestion:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8081/health
```

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

### Full Batch Reset and Migration

Use this workflow only for a new corpus or when you intentionally want to
delete all existing policy data. Place DOCX/PDF files in `archive/`, then run:

```bash
bash infra/reset_and_reingest.sh --reset
bash infra/run_graph_migration.sh --reset
```

Both `--reset` flags are mandatory and destructive. The first command clears
PostgreSQL data and ingests the archive. The second clears and rebuilds Neo4j.
This workflow requires local llama.cpp mode because the scripts start the local
Qwen graph server.

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
  ├── clause_detector.py   Split sections into individual clauses (Qwen3-4B LLM)
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

## Agents

| # | Agent | File | Purpose |
|---|-------|------|---------|
| 1 | Orchestrator | `services/agents/orchestrator.py` | State machine. Classifies intent, routes to specialized agents. |
| 2 | Retriever | `services/api/search.py` | Vector + BM25 → RRF fusion → CrossEncoder rerank → top 5 |
| 3 | Reasoner | `services/agents/reasoner.py` | Qwen3-8B generates grounded answer with citations |
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
