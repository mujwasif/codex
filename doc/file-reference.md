# Codebase File Reference

This document maps all functions and variables currently implemented in the Codex project.

## 📂 packages/shared/auth.py
**Purpose:** Security and Token management.
- `verify_password(plain_password, hashed_password)`: Verifies bcrypt password.
- `create_access_token(data)`: Generates a signed JWT with 30m expiry.
- `verify_token(token)`: Decodes and validates a JWT.
- `SECRET_KEY`: Key used for signing tokens.

## 📂 packages/shared/schemas.py
**Purpose:** Pydantic data models for type safety.
- `User`: Model for user profiles (username, dept, access_level).
- `Token`: Model for authentication responses.

## 📂 services/api/main.py
**Purpose:** API Gateway and Orchestration.
- `get_current_user(token)`: Dependency that verifies the user's identity and level.
- `login(form_data)`: Authenticates user and returns a JWT.
- `secure_query(query, current_user)`: Coordinates search $\rightarrow$ reason $\rightarrow$ verify flow.
- `MOCK_USERS`: Temporary user store for MVP.

## 📂 services/api/search.py
**Purpose:** Semantic retrieval and reranking.
- `search_policy(query, access_level)`:
  - 1. Encodes query into vector.
  - 2. Queries pgvector for top 20 candidates.
  - 3. Reranks candidates via `CrossEncoder`.
  - 4. Returns the top 3 most relevant chunks.
- `MOCK_CHUNKS`: Fallback data when database is offline.

## 📂 services/agents/reasoner.py
**Purpose:** LLM logic using DeepSeek-Distill.
- `generate_grounded_answer(query, context_chunks)`:
  - Formats context for the LLM.
  - Sends a grounded prompt to `llama.cpp` server.
  - Returns the cited answer.

## 📂 services/agents/verifier.py
**Purpose:** Truth-checking and citation audit.
- `verify_citations(answer, context_chunks)`:
  - Uses regex to find cited clauses.
  - Compares citations against the actual retrieved chunks.
  - Returns `True` if grounded, `False` if hallucinated.

## 📂 services/ingestion/ingest.py
**Purpose:** Document processing pipeline.
- `get_db_connection()`: Connects to Postgres.
- `ingest_file(file_path, cur, conn)`:
  - Extracts text from PDF/DOCX.
  - Splits text into chunks (2000 chars).
  - Embeds chunks and saves them to the `chunks` table.

## 📂 tests/test_reasoning.py
**Purpose:** Integration testing suite.
- `get_token(username, password)`: Automates login for testing.
- `test_scenario(...)`: Validates a specific query against expected la- la- laL results.
