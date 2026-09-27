# Codex UI — Full Overview

**File:** `services/chat/ui.py` (1,553 lines)

The UI is a **Streamlit** web app with a dark theme. It serves two user types: **admins** (4 tabs) and **employees** (chat only).

---

## Structure Overview

```
┌─────────────────────────────────────────────────────┐
│  Login Page (if not authenticated)                   │
├──────────┬──────────────────────────────────────────┤
│ SIDEBAR  │  MAIN AREA                               │
│          │                                           │
│ Username │  ADMIN HEADER (4 tab buttons):            │
│ History  │  [Assistant] [Ingestion] [Documents] [Users]
│ Clear    │                                           │
│ Logout   │  ── ACTIVE TAB CONTENT ──                │
│          │                                           │
│          │  Assistant: chat messages + input box     │
│          │  Ingestion: upload + status dashboard     │
│          │  Documents: browse chunks + search        │
│          │  Users: CRUD user management              │
└──────────┴──────────────────────────────────────────┘
```

---

## Section 1: API Layer (lines 1-200)

**Core functions that call the backend API:**

| Function | Endpoint | Purpose |
|----------|----------|---------|
| `api_request()` | Any | Central HTTP wrapper — injects JWT, handles 401 → refresh token → retry |
| `token_payload()` | — | Decodes JWT to read `access_level` without verification |
| `_try_refresh_token()` | POST `/refresh` | Attempts token refresh on 401 |
| `fetch_ingestion_status()` | GET `/v1/ingestion/status` | Polls ingestion progress |
| `upload_policy()` | POST `/v1/ingest` | Uploads PDF/DOCX file |
| `delete_policy()` | DELETE `/v1/documents/{id}` | Removes document + chunks + graph |
| `retry_policy()` | POST `/v1/ingestion/retry/{id}` | Retries failed ingestion |
| `retry_chunks_policy()` | POST `/v1/ingestion/retry-chunks` | Retries all chunking failures |
| `retry_graph_policy()` | POST `/v1/ingestion/retry-graph` | Retries all graph failures |
| `remove_policy()` | DELETE `/v1/documents/{id}` | Alias for delete with different error messages |

### How token refresh works

```
api_request() → 401 response
  → _try_refresh_token() → POST /refresh with refresh_token
  → if success: update session_state tokens, retry original request
  → if fail: clear tokens, rerun (redirects to login)
```

The `api_request()` function automatically:
1. Injects `Authorization: Bearer {token}` header
2. Injects `X-Refresh-Token` header
3. Checks response for `X-New-Access-Token` header (transparent rotation)
4. On 401: retries with refresh token, then falls back to login

---

## Section 2: User Management API (lines 200-280)

| Function | Endpoint | Purpose |
|----------|----------|---------|
| `fetch_users()` | GET `/users` | List all users |
| `create_user()` | POST `/users` | Create new user with username/password/department/access_level |
| `update_user()` | PATCH `/users/{id}` | Update department and/or access level |
| `delete_user_api()` | DELETE `/users/{id}` | Delete user permanently |
| `reset_user_password_api()` | POST `/users/{id}/reset-password` | Reset user's password |

Error handling per function:
- **400**: Duplicate username / no fields to update
- **403**: Admin access required
- **404**: User not found
- **Network errors**: Connection error, timeout

---

## Section 3: Document Fetching API (lines 282-299)

| Function | Endpoint | Purpose |
|----------|----------|---------|
| `fetch_documents()` | GET `/documents` | List all documents with status, chunk count, entity count |
| `fetch_document_detail(doc_id)` | GET `/documents/{doc_id}` | Fetch full document detail including all chunks and entities |

---

## Section 4: Ingestion Dashboard (lines 300-460)

### `render_ingestion_status_panel()` — Main admin ingestion view

```
┌──────────────────────────────────────────────┐
│ Live Feed (auto-refreshes every 5s)          │
│ > Processing "Backup_Policy.docx"...         │
│ > Chunking complete: 42 chunks               │
│ > Graph building: 15 clauses...              │
├──────────────────────────────────────────────┤
│ documents become ready only after chunking   │
│ AND graph; ingestion runs only for files     │
│ uploaded here                                │
├──────────────────────────────────────────────┤
│ [pending: 2] [processing: 1] [ready: 25]     │
│ [chunks_ready: 1] [graph_building: 0] [failed: 1]
├──────────────────────────────────────────────┤
│ Recent Documents Table:                      │
│ Title | Stage | Chunks | Graph Ready | Error │
│ ...    | ready | 42     | 2026-09-24  |       │
├──────────────────────────────────────────────┤
│ Failed Documents (2):                        │
│ [Retry Chunking (1)]  [Retry Graph (1)]      │
│ - "Old_Policy.pdf" (failed at graph)         │
│   Error: Neo4j connection timeout            │
│   [Remove]                                   │
├──────────────────────────────────────────────┤
│ Cancel / Remove Document:                    │
│ [Select document ▼]                          │
│ [☑ I understand] [Remove Document]           │
└──────────────────────────────────────────────┘
```

### Components

1. **Live Feed** (`render_live_ingestion_feed_from_data()`):
   - `@st.fragment(run_every=5)` — auto-refreshes every 5 seconds
   - Shows last 2000 chars of ingestion worker log output
   - Rendered as a code block with monospace font

2. **Stage Counters**: `st.metric` cards for each stage (`pending`, `processing`, `chunks_ready`, `graph_building`, `ready`, `failed`)

3. **Document Table**: HTML table with color-coded status badges:
   - Green (`verdict-clear`): ready
   - Yellow (`verdict-conditional`): processing/pending/chunks_ready/graph_building
   - Red (`verdict-abstained`): failed

4. **Failed Documents Panel**:
   - Separates failures into **chunking** (no chunks) vs **graph** (has chunks)
   - Batch retry buttons: "Retry Chunking (N)" and "Retry Graph (N)"
   - Per-document: error message, Remove button

5. **Cancel / Remove Document**:
   - Selectbox of all documents with status in brackets
   - Confirmation checkbox required
   - "Cancel Document" for in-flight docs, "Remove Document" for completed
   - Shows chunks removed + graph clauses deleted in success message

### `render_ingestion_tab()` — File upload area

```
┌──────────────────────────────────────────────┐
│ Ingestion & Policies                         │
│                                              │
│ Drag and drop policy documents (.pdf/.docx)  │
│ ┌──────────────────────────────────────┐     │
│ │  📁 Drop files here                  │     │
│ └──────────────────────────────────────┘     │
│ [        Upload Policies        ]            │
│                                              │
│ Processing 1/3: "Backup_Policy.docx"...      │
│ [████████████░░░░░░░░░░░░] 33%              │
├──────────────────────────────────────────────┤
│ (auto-refreshing ingestion status panel)     │
└──────────────────────────────────────────────┘
```

- Multi-file drag-and-drop uploader (PDF/DOCX, `accept_multiple_files=True`)
- "Upload Policies" button triggers sequential upload with progress bar
- Each file goes to `POST /v1/ingest` → queues for ingestion worker
- Handles 403 (admin required), 409 (duplicate), network errors

### `render_ingestion_auto_refresh()` — `@st.fragment(run_every=5)`

Wraps the ingestion status panel to auto-refresh every 5 seconds without full page rerun.

---

## Section 5: Users Tab (lines 517-675)

### `render_users_tab()` — Admin user management

```
┌──────────────────────────────────────────────┐
│ User Management                              │
│ Admin workspace. Create, edit, and delete    │
│ users. New users default to Employee level.  │
├──────────────────────────────────────────────┤
│ Add New User:                                │
│ [Username      ] [Password       ]           │
│ [Department    ] [Access Level ▼ ]           │
│ [1 - Employee]                               │
│ [       Create User       ]                  │
├──────────────────────────────────────────────┤
│ All Users:                                   │
│ Username | Dept | Level   | Active | Created │
│ admin    | IT   | Admin   | Yes    | 2026-01 │
│          |      |         |        | [Edit]  │
│          |      |         |        | [Reset] │
│          |      |         |        | [Del]   │
│ john     | HR   | Employee| Yes    | 2026-03 │
│          |      |         |        | [Edit]  │
│          |      |         |        | [Reset] │
│          |      |         |        | [Del]   │
└──────────────────────────────────────────────┘
```

### Inline Edit Form (lines 602-630)

```
┌──────────────────────────────────────────────┐
│ Edit john:                                   │
│ Department: [HR          ]                   │
│ Access Level: [2 - Manager ▼]                │
│ [Save Changes] [Cancel]                      │
└──────────────────────────────────────────────┘
```

- Department text input pre-filled with current value
- Access level selectbox (1=Employee, 2=Manager, 3=Admin) pre-selected
- Save calls `update_user()` → PATCH `/users/{id}`
- Cancel closes the expander

### Inline Password Reset (lines 632-659)

```
┌──────────────────────────────────────────────┐
│ Reset password for john:                     │
│ New Password: [••••••••]                      │
│ Confirm Password: [••••••••]                  │
│ [Reset Password] [Cancel]                     │
└──────────────────────────────────────────────┘
```

Validation:
- Password required
- Minimum 6 characters
- Must match confirmation
- Calls `reset_user_password_api()` → POST `/users/{id}/reset-password`

### Inline Delete Confirmation (lines 661-675)

```
⚠ Are you sure you want to permanently delete **john**?
[Confirm Delete] [Cancel Delete]
```

- Calls `delete_user_api()` → DELETE `/users/{id}`
- Permanent, no undo

---

## Section 6: Documents Tab (lines 678-759)

### `render_documents_tab()` — Browse ingested policy content

```
┌──────────────────────────────────────────────┐
│ Documents & Chunks                           │
│ Browse ingested policy documents and their   │
│ chunked content.                             │
├──────────────────────────────────────────────┤
│ [Search chunks by text... 🔍]                │
│ e.g. password policy                         │
├──────────────────────────────────────────────┤
│ Documents                                    │
│                                              │
│ 📄 UnderDefense Backup Policy [ready]        │
│    42 chunks · 8 entities                    │
│ [          View Chunks          ]            │
│                                              │
│   Chunks (42):                               │
│   ▸ Chunk 1: §4.1 (156 tokens) - Backup     │
│     Clause Ref: §4.1                         │
│     Section Path: 4. Backup Requirements    │
│     Token Count: 156                         │
│     ---                                     │
│     "All critical systems must be backed     │
│      up daily with a retention of 90 days.   │
│      Backups must be encrypted at rest       │
│      using AES-256 and stored in a           │
│      geographically separate location..."    │
│                                              │
│   ▸ Chunk 2: §4.2 (89 tokens) - Backup      │
│     Clause Ref: §4.2                         │
│     Section Path: 4. Backup Requirements    │
│     Token Count: 89                          │
│     ---                                     │
│     "Off-site backups must be encrypted      │
│      and stored in a facility with           │
│      physical access controls..."            │
│                                              │
│   Entities (8):                              │
│   - backup_frequency: daily                  │
│   - retention_days: 90                       │
│   - encryption_standard: AES-256             │
│   - storage_location: geographically separate│
├──────────────────────────────────────────────┤
│ 📄 UnderDefense DR Policy [ready]            │
│    35 chunks · 6 entities                    │
│ [          View Chunks          ]            │
└──────────────────────────────────────────────┘
```

### Components

1. **Search Bar**: Filters chunks by text content or clause_ref (case-insensitive substring match)

2. **Document List**: Each document shows:
   - Title (bold)
   - Status badge (color-coded: green=ready, yellow=processing, red=failed)
   - Chunk count
   - Entity count

3. **View Chunks Toggle**: Expands/collapses document detail:
   - Fetches `GET /documents/{doc_id}` for full chunk + entity data
   - Chunks displayed as **expandable accordions** with:
     - Clause reference
     - Section path
     - Token count
     - Full text content
   - Entities displayed as bullet list with optional JSON attributes

---

## Section 7: Citations Rendering (lines 762-789)

### `render_citations()` — Scrollable citation card

```
┌──────────────────────────────────────────────┐
│ View Citations (5)                           │
│                                              │
│ 1. UnderDefense Backup Policy: Clause §4.1   │
│    — Relevance Score: 0.920                  │
│    · 4. Backup Requirements                  │
│ > "All critical systems must be backed up    │
│ >  daily with a retention of 90 days."       │
│                                              │
│ 2. UnderDefense DR Policy: Clause §3.2       │
│    — Relevance Score: 0.850                  │
│    · 3. Recovery Procedures                  │
│ > "The DR Committee is responsible for       │
│ >  creating recovery plans..."               │
│                                              │
│ 3. Backup Policy: Clause §5.1 — 0.810        │
│    · 5. Testing Requirements                 │
│                                              │
│ 4. DR Policy: Clause §2.1 — 0.780            │
│    · 2. Governance Framework                  │
│                                              │
│ 5. InfoSec Policy: Clause §7.3 — 0.720       │
│    · 7. Security Controls                    │
└──────────────────────────────────────────────┘
```

Each citation shows:
- **Index** (1, 2, 3...)
- **Document title**
- **Clause reference** (code format)
- **Relevance score** (3 decimal places)
- **Section path** (if available)
- **Source quote** (if available, left-bordered blockquote)

---

## Section 8: Approval Rendering (lines 792-826)

### `render_approval()` — Approval authority callout

```
┌──────────────────────────────────────────────┐
│ [APPROVABLE BY YOU]                          │
│ COO, Claims Manager for 'Disaster Recovery   │
│ Implementation'                              │
└──────────────────────────────────────────────┘
```

Four possible states:

| State | Badge | Meaning |
|-------|-------|---------|
| `user_can_approve=True` | Green `APPROVABLE BY YOU` | User has authority to approve |
| `user_can_approve=False` | Yellow `APPROVAL REQUIRED` | Different role required |
| `process` found but no roles | Gray `NO APPROVAL CHAIN FOUND` | Process exists but no approver defined |
| No `process` found | Gray `NO APPROVAL PROCESS FOUND` | No matching process in knowledge graph |

---

## Section 9: Conflicts Rendering (lines 829-873)

### `render_conflicts()` — Detected policy contradictions

```
┌──────────────────────────────────────────────┐
│ Conflicts Detected (2)                       │
│                                              │
│ ⚠ Conflict: Backup Policy §4.1 ↔ DR §4.1   │
│ These requirements contradict each other     │
│ because the Backup Policy mandates exclusive │
│ ISMS Manager authority while DR allows       │
│ delegation to appointed employees.           │
│ > "Changes to this policy shall be           │
│ >  exclusively performed by the ISMS Manager"│
│  [Backup Policy §4.1]                        │
│ > "Changes to this policy shall be           │
│ >  exclusively performed by the ISMS Manager │
│ >  or employee that was specifically..."     │
│  [DR Policy §4.1]                            │
│                                              │
│ ⚠ Conflict: Backup Policy §5.2 ↔ DR §5.2   │
│ Training frequency mismatch...               │
└──────────────────────────────────────────────┘
```

Each conflict shows:
- **Status**: `⚠ Conflict` (red) or `⚠ Possible Conflict` (yellow)
- **Clause references**: `DocA §ref ↔ DocB §ref`
- **Reason**: Explanation of why they contradict
- **Subject** (if available): Topic label
- **Source text**: Truncated quotes from both clauses (120 chars each)

---

## Section 10: Compliance Risk Rendering (lines 876-902)

### `render_risk()` — Compliance risk verdict

```
┌──────────────────────────────────────────────┐
│ [Compliance: CONDITIONAL]                    │
│ Risk Level: Medium                           │
│ · Regulations: ISO 27001 Annex A.17.1.1,     │
│   ISO 27001 Annex A.17.1.2                   │
└──────────────────────────────────────────────┘
```

Color coding:
- Green `verdict-clear`: No compliance issues
- Yellow `verdict-conditional`: Some conditions need verification
- Red `verdict-violation`: Compliance breach detected

---

## Section 11: Next Steps Rendering (lines 905-920)

### `render_next_steps()` — Actionable recommendations

```
┌──────────────────────────────────────────────┐
│ Next Steps (3)                               │
│                                              │
│ 1. Align policy governance across Backup     │
│    and DR policies                           │
│ 2. Formalize delegation process for ISMS     │
│    Manager appointees                        │
│ 3. Submit alignment proposal to ISMS         │
│    Committee                                 │
└──────────────────────────────────────────────┘
```

---

## Section 12: CSS Theme (lines 928-1090)

Full dark theme injected via `st.markdown(unsafe_allow_html=True)`.

### Verdict Badges

| CSS Class | Background | Text Color | Border | Meaning |
|-----------|------------|------------|--------|---------|
| `.verdict-clear` | `#d4edda` (light green) | `#155724` (dark green) | `#c3e6cb` | Clear / Compliant |
| `.verdict-violation` | `#f8d7da` (light red) | `#721c24` (dark red) | `#f5c6cb` | Violation |
| `.verdict-conditional` | `#fff3cd` (light yellow) | `#856404` (dark yellow) | `#ffeeba` | Conditional |
| `.verdict-abstained` | `#e2e3e5` (light gray) | `#383d41` (dark gray) | `#d6d8db` | Abstained / Insufficient |

### Intent Chip

`.intent-chip`: Dark blue pill badge (`#233554` bg, `#9fb6ff` text, `#2f4a86` border) for displaying detected intent.

### Chat Theme

| Element | Style |
|---------|-------|
| Chat container (`.chat_transcript`) | Dark bg `#1b1f27`, fixed height, custom scrollbar (`#3a4350` thumb) |
| User messages | Blue background `#2f6fed`, white text |
| Assistant messages | Subtle bg `rgba(255,255,255,0.05)`, light text `#e6e9ef` |
| Assistant avatar | Dark circle `#2a303b` |
| Chat input | Dark bg `#161a20`, border `#2a303b` |
| Placeholder text | `rgba(255,255,255,0.45)` |
| Spinner | Light gray `#b8c0cc` |

### Metadata Cards

| CSS Class | Purpose |
|-----------|---------|
| `.meta-card` | Scrollable card container (dark bg `#222a36`, border `#323b49`, rounded) |
| `.card-scroll` | Inner scrollable area (max-height 340px, custom scrollbar) |
| `.cite-row` | Citation/conflict row (light text `#c6cdd6`) |
| `.cite-head` | Bold citation header (`#d7dce3`) |
| `.cite-quote` | Left-bordered blockquote (`#4a5464` border, `#b8c0cc` text) |

### Admin Header

- Sticky positioning (`position: sticky; top: 60px`)
- Dark background `#1b1f27` with box shadow
- Title: white, bold, 1.05em
- Subtitle: gray `#9aa3b0`, 0.7em
- Tab buttons: semi-transparent white bg, white text

### Scrollbar Styling

Custom WebKit scrollbar:
- Width: 8px
- Thumb: `#3a4350` (rounded)
- Track: transparent

---

## Section 13: Session State (lines 1092-1108)

All state variables initialized on page load:

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `token` | `str` or `None` | `None` | JWT access token |
| `refresh_token` | `str` or `None` | `None` | JWT refresh token |
| `username` | `str` or `None` | `None` | Logged-in username |
| `messages` | `list` | `[]` | Chat history (list of bubble dicts) |
| `active_view` | `str` | `"assistant"` | Current admin tab |
| `pending` | `str` or `None` | `None` | Question being processed (triggers API call) |
| `selected_doc_ids` | `list` or `None` | `None` | Document UUIDs for Phase 2 |
| `pending_conflict_question` | `str` or `None` | `None` | Original question for Phase 2 |

---

## Section 14: Chat History (lines 1110-1172)

### `fetch_chat_history()` — Load from DB

- Calls `GET /query/history`
- Returns list of `QueryResponse` items (newest first)
- Returns `None` on failure

### `clear_chat_history()` — Delete from DB

- Calls `DELETE /query/history`
- Permanently deletes all queries, answers, citations, feedback for current user

### `messages_from_history()` — Convert to chat bubbles

Converts DB records to Streamlit chat format (oldest first):
```python
[
    {"role": "user", "content": "Who approves DR?"},
    {
        "role": "assistant",
        "content": "The COO approves DR procedures...",
        "metadata": {
            "verdict": "clear",
            "confidence": 88.9,
            "citations": [...],
            "intent": "approval"
        }
    },
    ...
]
```

### Auto-load on page refresh (lines 1165-1172)

```python
if st.session_state.token and not st.session_state.messages:
    history = fetch_chat_history()
    if history:
        st.session_state.messages = messages_from_history(history)
```

Ensures chat history persists across browser refreshes.

---

## Section 15: Login Flow (lines 1233-1266)

### `login_user()` — POST `/login`

- Returns `(access_token, refresh_token)` on success
- Handles 400 (incorrect credentials) and network errors

### Login form

```
┌──────────────────────────────────────────────┐
│         Authentication Required              │
│                                              │
│         ┌──────────────────────┐             │
│         │ Please log in       │             │
│         │ Username: [admin]    │             │
│         │ Password: [••••••••] │             │
│         │ [      Log In      ] │             │
│         └──────────────────────┘             │
└──────────────────────────────────────────────┘
```

Centered in a 3-column layout (1:2:1 ratio).

### Post-login sequence

1. Store `access_token` + `refresh_token` in `session_state`
2. Store `username` in `session_state`
3. Set URL param `?token={access_token}` for SSO bookmark
4. Fetch chat history (up to 3 retries with 2-second delays)
5. Convert history to chat bubbles → `session_state.messages`
6. `st.rerun()` → loads main UI

### SSO via URL token (lines 1175-1179)

If `?token=xxx` is in the URL:
1. Decode and verify the token via `verify_token()`
2. Store in session state
3. Fetch and load chat history

---

## Section 16: Sidebar (lines 1269-1300)

```
┌────────────┐
│ Codex      │
│ Control    │
│ Panel      │
│            │
│ Logged in  │
│ as: admin  │
│            │
│ ────────── │
│ [☑] I understand this     │
│ permanently deletes my    │
│ chat history              │
│ [Clear Conversation]      │
│            │
│ [Log Out]  │
└────────────┘
```

### Clear Conversation

- Requires checkbox confirmation: "I understand this permanently deletes my chat history"
- Calls `DELETE /query/history` → removes all Q&A from PostgreSQL
- Clears `session_state.messages`, `selected_doc_ids`, `pending_conflict_question`
- Shows success message with count of deleted queries

### Log Out

- Removes `?token` from URL params
- Clears `token`, `username`, `messages`
- Resets `active_view` to `"assistant"`
- `st.rerun()` → shows login form

---

## Section 17: Chat Bubble Rendering (lines 1308-1398)

### `_verdict_class()` — Maps verdict to CSS class

| Verdict | CSS Class | Color |
|---------|-----------|-------|
| `clear` | `verdict-clear` | Green |
| `violation` | `verdict-violation` | Red |
| `conditional` | `verdict-conditional` | Yellow |
| `pending_selection` | `verdict-conditional` | Yellow |
| Everything else | `verdict-abstained` | Gray |

### `_render_bubble()` — Full assistant message

```
┌──────────────────────────────────────────────┐
│ 🤖 [Answer text with markdown formatting]    │
│                                              │
│ [Intent: CONFLICT · 90%]                     │
│ [Verdict: CLEAR]                             │
│ Confidence Score: 0.85                       │
│                                              │
│ ┌─ Approval ───────────────────────────────┐ │
│ │ [APPROVABLE BY YOU]                      │ │
│ │ COO for 'Disaster Recovery Implementation'│ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌─ Conflicts Detected (2) ─────────────────┐ │
│ │ ⚠ Conflict: Backup §4.1 ↔ DR §4.1       │ │
│ │ > "Changes shall be exclusively..."       │ │
│ │ > "Changes shall be exclusively..."       │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌─ Compliance ─────────────────────────────┐ │
│ │ [Compliance: CONDITIONAL]                │ │
│ │ Risk Level: Medium                       │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌─ View Citations (5) ─────────────────────┐ │
│ │ 1. Backup Policy: §4.1 — 0.92            │ │
│ │ > "All critical systems must be..."       │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ── Select documents to compare: ──           │
│ Slot 1: "Backup Policy"                       │
│ [UnderDefense Backup policy v2.1 ▼]           │
│ Slot 2: "DR Policy"                           │
│ [UnderDefense DR plan v1.0 ▼]                 │
│ [    Run Conflict Analysis    ]              │
└──────────────────────────────────────────────┘
```

### Metadata displayed per assistant message

| Field | Display |
|-------|---------|
| `intent` | Dark blue chip: `Intent: CONFLICT · 90%` |
| `verdict` | Color-coded badge: `Verdict: CLEAR` |
| `confidence` | Text: `Confidence Score: 0.85` |
| `approval` | Approval card (if present) |
| `conflicts` | Conflicts card (if any detected) |
| `risk` | Compliance risk card (if present) |
| `next_steps` | Next steps card (if any) |
| `citations` | Citations card (if any) |
| `document_slots` | Document selection dropdowns (if `pending_selection`) |

### Document Slot Selection (Phase 1 → Phase 2)

When verdict is `pending_selection`, the assistant bubble includes dropdowns:
- Each slot has a selectbox with up to 5 candidate documents (with similarity scores)
- User selects documents → clicks "Run Conflict Analysis"
- Stores `selected_doc_ids` + `pending_conflict_question` in session state
- Reruns → triggers Phase 2 API call

---

## Section 18: Store Result (lines 1401-1432)

### `_store_result()` — Convert API response to chat bubble

Extracts from API response:
- `answer` → bubble content
- `verdict`, `confidence` → verdict badge
- `citations` → citation card
- `reasoning.intent`, `reasoning.intent_confidence` → intent chip
- `reasoning.approval` → approval card
- `reasoning.conflicts` → conflicts card
- `reasoning.risk` → compliance card
- `next_steps` → next steps card
- `document_slots` → Phase 2 selection UI
- `resolved_documents` → resolved doc info

---

## Section 19: Main Chat Flow (lines 1435-1498)

### `render_assistant_tab()` — The chat interface

```
Flow:
  1. Phase 2 check: if selected_doc_ids + pending question → resubmit with doc IDs
  2. Chat input box → store in session_state.pending → rerun
  3. Render all messages from session_state.messages
  4. If pending: show spinner, call ask_codex(), store result, rerun
```

### Phase 2 Execution (lines 1437-1473)

When `pending_conflict_question` and `selected_doc_ids` are set:

1. Validate doc IDs are valid UUIDs
2. Clear state BEFORE submission (so selection UI disappears immediately)
3. Clear `document_slots` from Phase 1 message (prevents re-rendering selection UI)
4. Append user message to `session_state.messages`
5. Show spinner: "Running conflict analysis on selected documents..."
6. Call `ask_codex(original_question, selected_doc_ids=doc_ids)`
7. Store result via `_store_result()`
8. `st.rerun()`

### Chat Input (lines 1475-1478)

```python
if prompt := st.chat_input("Ask a policy question..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.session_state.pending = prompt
    st.rerun()
```

Streamlit's `st.chat_input()` renders a fixed-bottom text input. On submit:
1. Appends user message to messages
2. Sets `pending` flag
3. Reruns → triggers the pending block

### Message Rendering Loop (lines 1480-1498)

```python
with st.container(height=500, autoscroll=True, key="chat_transcript"):
    for msg_idx, msg in enumerate(st.session_state.messages):
        _render_bubble(msg, msg_index=msg_idx)

    if st.session_state.pending:
        with st.chat_message("assistant"):
            with st.spinner("Analyzing policy documents..."):
                result = ask_codex(st.session_state.pending)
        if result:
            _store_result(result, st.session_state.pending)
        else:
            st.session_state.messages.append(...)
        st.session_state.pending = None
        st.rerun()
```

Key behaviors:
- Fixed-height container (500px) with auto-scroll
- All previous messages rendered from `session_state.messages`
- Pending message shows spinner while waiting for API response
- On response: store result, clear pending, rerun to display

---

## Section 20: Admin Header + Tab Routing (lines 1500-1553)

### Admin Header Bar (sticky)

```
┌──────────────────────────────────────────────────────┐
│ Codex Policy Intelligence Engine                     │
│ Context-Aware RAG Engine for Policy Querying         │
│ & Compliance Verification                            │
│                                                      │
│ [Assistant] [Ingestion & Policies] [Documents] [Users]│
└──────────────────────────────────────────────────────┘
```

- Sticky positioning (stays at top when scrolling)
- Dark background with subtle box shadow
- 4 column buttons: each sets `active_view` and reruns
- Active tab: primary button style (highlighted)
- Inactive tabs: secondary button style

### Tab Routing

```python
if is_admin:
    # Show admin header with 4 tabs
    if st.session_state.active_view == "ingestion":
        render_ingestion_tab()
    elif st.session_state.active_view == "documents":
        render_documents_tab()
    elif st.session_state.active_view == "users":
        render_users_tab()
    else:
        render_assistant_tab()
else:
    # Non-admin: chat only, no tabs
    render_assistant_tab()
```

### `is_admin` Check

```python
is_admin = token_payload(st.session_state.token).get("access_level", 1) >= 3
```

Decodes JWT payload (no signature check) to read `access_level`. Only level 3 (admin) gets the tabbed interface.

---

## Section 21: ask_codex() — The API Call (lines 1210-1230)

```python
def ask_codex(question: str, selected_doc_ids=None):
    payload = {"question": question, "search_mode": "hybrid"}
    if selected_doc_ids:
        payload["selected_doc_ids"] = selected_doc_ids
    response = api_request("post", "/query", json=payload, timeout=300)
    return response.json()
```

- `search_mode`: always "hybrid" (vector + BM25)
- `selected_doc_ids`: optional list of UUIDs for Phase 2
- Timeout: 300 seconds (5 minutes) — long queries with multi-tool can take 2-3 minutes
- Error handling: ConnectionError, Timeout, generic RequestException

---

## Complete Data Flow

```
User types question in chat input
  │
  ▼
st.session_state.pending = "question"
st.rerun()
  │
  ▼
render_assistant_tab() detects pending
  │
  ├─ Phase 2? (selected_doc_ids set)
  │   ├─ ask_codex(question, selected_doc_ids=doc_ids)
  │   ├─ API: POST /query {question, selected_doc_ids}
  │   └─ _store_result(result) → append to messages
  │
  └─ Phase 1? (no doc selection)
      ├─ ask_codex(question)
      ├─ API: POST /query {question}
      ├─ Response: answer + metadata OR document_slots
      │
      ├─ If answer → _store_result() → append to messages
      │
      └─ If document_slots → append to messages with
         pending_selection verdict → render dropdowns
         → user selects → "Run Conflict Analysis"
         → stores selected_doc_ids → rerun → Phase 2
```

---

## State Machine

```
session_state.token              → None (login) or JWT string
session_state.refresh_token      → None or JWT string
session_state.username           → None or "admin"
session_state.messages           → list of chat bubble dicts
session_state.active_view        → "assistant" | "ingestion" | "documents" | "users"
session_state.pending            → question being processed (triggers API call on rerun)
session_state.selected_doc_ids   → list of UUIDs for Phase 2
session_state.pending_conflict_question → original question for Phase 2
```

### Page Lifecycle

```
Page Load
  │
  ├─ Initialize session_state defaults (if not set)
  │
  ├─ Auto-load chat history (if token exists, messages empty)
  │
  ├─ Check token:
  │   ├─ No token → Show login form
  │   │   └─ Login → fetch history → rerun
  │   │
  │   ├─ URL has ?token → SSO login → fetch history → rerun
  │   │
  │   └─ Token exists → Show main UI
  │
  ├─ Admin header (if admin):
  │   ├─ 4 tab buttons → set active_view → rerun
  │   └─ Render active tab
  │
  └─ Non-admin:
      └─ render_assistant_tab()
          ├─ Phase 2 pending? → process → rerun
          ├─ Chat input? → set pending → rerun
          ├─ Render messages
          └─ Pending? → spinner → API call → store → rerun
```
