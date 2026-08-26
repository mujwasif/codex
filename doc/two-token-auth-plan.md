# Variant A Implementation Plan: Two-Token Auth with Silent Refresh

Status: **PLANNED — not yet implemented**

Replaces the single 30-minute JWT with **Access (15 min) + Refresh (7 days)** tokens.
Reload persistence stays on `st.query_params`. Active users never see the login form;
idle sessions expire safely.

---

## Architecture Overview

```
LOGIN
  ├─→ Access token  (15 min JWT)  ──→ query_params ?access=...
  └─→ Refresh token (7 days, DB-tracked) ──→ query_params ?refresh=...

NORMAL REQUESTS (chat, history, ingestion status)
  → use access token
  → if expired (UI detects via exp claim):
       POST /auth/refresh {refresh_token}
       → server validates against DB → new access token issued
       → retry original request silently

PAGE RELOAD
  → read ?access + ?refresh from URL
  → access valid?  → instant restore
  → access expired, refresh valid?  → silent refresh → restore
  → refresh expired/revoked?  → login form

LOGOUT
  → del both query_params  (+ optional server-side revoke)
```

---

## Why Two Tokens

| | Single Long-Lived JWT | Access + Refresh Split |
|---|----------------------|------------------------|
| Stolen token window | Full lifetime | ~15 min (access) |
| Revocable before expiry | No (stateless) | Yes (refresh row in DB) |
| Logout = server-side kill switch | No | Yes via /auth/revoke |
| User re-login frequency | Every expiry | Only after 7 idle days |

The split costs one endpoint and one DB table; buys instant revocation and a
tiny theft window.

---

## Changes by File

### 1. `packages/shared/config.py` (+2 lines)

```python
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
```

### 2. Database — new table

Live DDL on `codex_db` + added to `infra/db/init.sql`:

```sql
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     VARCHAR(100) NOT NULL,
    token_hash  CHAR(64) NOT NULL,        -- SHA-256 hex of raw token
    created_at  TIMESTAMPTZ DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked     BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_refresh_hash ON refresh_tokens(token_hash);
```

Raw tokens are never stored — only their SHA-256 hash. A DB leak yields
unusable hashes.

### 3. `packages/shared/models.py` (+~12 lines)

SQLAlchemy `RefreshToken` model matching the table above.

### 4. `packages/shared/auth.py` (+~50 lines)

| New Function | Behavior |
|--------------|----------|
| `create_refresh_token(username)` | Generate `secrets.token_urlsafe(48)` → SHA-256 hash → insert row (7-day expiry) → return raw token |
| `verify_refresh_token(raw)` | Hash input → lookup by hash → return `username` if found, not revoked, not expired; else `None` |
| `revoke_refresh_token(raw)` | Mark matching row revoked |

`create_access_token()` unchanged in logic; honors new 15-minute constant
(`ACCESS_TOKEN_EXPIRE_MINUTES`) instead of legacy `TOKEN_EXPIRE_MINUTES`.

### 5. `services/api/routers/auth.py` (+~60 lines)

| Endpoint | Behavior |
|----------|----------|
| `POST /login` *(modified)* | Now returns `{access_token, refresh_token}` |
| `POST /auth/refresh` *(new)* | Input: `{"refresh_token": raw}` → validate vs DB → issue new **access** token only (refresh NOT rotated in v1) → return `{access_token}`. Invalid/revoked/expired → 401 |
| `POST /auth/revoke` *(new)* | Input: `{"refresh_token": raw}` → mark revoked. Used by UI logout, best-effort |

### 6. `services/chat/ui.py` (+~70 net)

**New helper** — central gatekeeper used by every API function
(`ask_codex`, `fetch_chat_history`, `fetch_ingestion_status`, `clear_chat_history`):

```python
def _jwt_expired(token):
    """Client-side expiry check via payload decode (no signature check)."""
    p = token_payload(token)
    exp = p.get("exp", 0)
    return datetime.utcnow().timestamp() >= exp


def _ensure_access_token():
    """Return a valid access token; silently refresh via /auth/refresh if expired."""
    tok = st.session_state.get("token")
    if tok and not _jwt_expired(tok):
        return tok
    refresh = st.session_state.get("refresh_token")
    if not refresh:
        return None
    try:
        resp = requests.post(
            f"{API_BASE_URL}/auth/refresh",
            json={"refresh_token": refresh},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        return None
    if resp.ok:
        data = resp.json()
        st.session_state.token = data["access_token"]
        st.query_params["access"] = data["access_token"]
        return data["access_token"]
    return None   # caller decides how to handle (usually: login form)
```

**Modified blocks:**

| Block | Change |
|-------|--------|
| Session restore (line ~1011) | Read `?access` + `?refresh`; fast-path if access valid; else attempt silent refresh; else fall to login |
| Login success (line ~1129) | Set both params: `st.query_params["access"]=…; st.query_params["refresh"]=…` |
| Logout (line ~1159) | Best-effort `POST /auth/revoke`, then `del st.query_params["access"]` + `del st.query_params["refresh"]` |
| Each `requests.*` call site | First line becomes `tok = _ensure_access_token(); if not tok: … handle gracefully` |

**Session restore block (target shape):**

```python
if not st.session_state.get("token"):
    url_access = st.query_params.get("access")
    url_refresh = st.query_params.get("refresh")

    if url_access and not _jwt_expired(url_access):
        # Fast path: access still valid
        from packages.shared.auth import verify_token
        payload = verify_token(url_access)
        if payload:
            st.session_state.token = url_access
            st.session_state.username = payload.get("username", "")
            st.session_state.refresh_token = url_refresh
            try:
                history = fetch_chat_history(url_access)
                st.session_state.messages = messages_from_history(history) if history else []
            except Exception:
                st.session_state.messages = []
        else:
            for k in ("access", "refresh"):
                del st.query_params[k]
    elif url_refresh:
        # Access expired — attempt silent renewal via server
        resp = requests.post(f"{API_BASE_URL}/auth/refresh",
                             json={"refresh_token": url_refresh}, timeout=10)
        if resp.ok:
            data = resp.json()
            st.session_state.token = data["access_token"]
            st.query_params["access"] = data["access_token"]
            payload = token_payload(data["access_token"])
            st.session_state.username = payload.get("username", "")
            try:
                history = fetch_chat_history(data["access_token"])
                st.session_state.messages = messages_from_history(history) if history else []
            except Exception:
                st.session_state.messages = []
        else:
            for k in ("access", "refresh"):
                del st.query_params[k]
```

Note: function-definition ordering matters — all helper functions must be
defined **above** this block (see `doc/persistent-auth.md` for the lesson).

---

## Key Design Decisions

### Q1 — Refresh rotation: strict or relaxed?

| Mode | Theft Detection | Multi-tab Safe | Complexity |
|------|----------------|---------------|------------|
| **A. Non-rotating** ✅ *recommended v1* | ❌ None — refresh valid until 7-day expiry | ✅ Yes — all tabs share it | Lowest |
| **B. Strict rotation** | ✅ Replay → revoke all user sessions | ❌ Second tab gets logged out | Higher |

Facebook-style security needs B, but B reintroduces the multi-tab logout bug
class we just escaped. **Decision: A for v1.** Rotation can be added later
behind a config flag with a grace window.

### Q2 — Include server-side logout revoke?

✅ **Yes.** ~8 lines. Makes logout meaningful server-side instead of purely
client-side param deletion.

### Q3 — Access lifetime 15 min?

✅ **Yes.** Industry default (Google/Microsoft). Halving/halving-again the
refresh call frequency is a minor optimization; 15 min keeps the stolen-token
window minimal.

---

## Execution Order

1. Config constants (`config.py`)
2. Model (`models.py`) + live-DDL migration on `codex_db`
3. Auth helpers (`packages/shared/auth.py`)
4. API endpoints (`services/api/routers/auth.py`)
5. UI helpers (`_jwt_expired`, `_ensure_access_token`) + wire into request functions
6. UI blocks: session restore, login success, logout
7. Compile checks → restart uvicorn + Streamlit
8. Manual test matrix below

---

## Test Matrix

| Scenario | Expected Result |
|----------|----------------|
| Login → chat immediately | Works; URL contains `?access=...&refresh=...` |
| Wait 16+ min → send another chat message | Silent refresh fires → message succeeds, no login form |
| Reload within 15 min of last refresh | Instant restore (fast path) |
| Reload at minute 20 (access expired) | Silent refresh on restore → restored with fresh access |
| Logout → page reload | Login form shown (both params gone, refresh revoked server-side) |
| Reuse revoked/expired refresh token via curl against `/auth/refresh` | 401 |

---

## Security Notes

| Aspect | Status |
|--------|--------|
| Token visible in URL bar | Accepted tradeoff (internal tool); both tokens signed/expiring; refresh hashed at rest |
| HttpOnly cookies | Deferred to Variant B (requires HTTPS + reverse proxy decisions) |
| Brute force on `/login` | Deferred — nginx rate limiting at deploy time |
| Password hashing argon2 | Already implemented ✅ |
