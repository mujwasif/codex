# Persistent Authentication in Codex

How Codex maintains user sessions across browser reloads and deployments.

---

## The Problem

Every time the user reloaded the browser page, they were logged out and had to log in again. Their chat history also disappeared from the UI.

### Why It Happened

Streamlit stores `st.session_state` in **server RAM**, tied to a browser session ID. When you reload:

```
Login → token stored in st.session_state.token (server RAM)
         ↓
Browser reload → Streamlit creates NEW session → st.session_state.token = None
         ↓
Line: if not st.session_state.token → shows login form
```

The JWT was only in RAM. The server never wrote it anywhere persistent. So on reload, it was gone.

---

## The Journey: 3 Failed Attempts, Then the Fix

### Attempt 1: Browser Cookies via `components.html()` ❌

**Idea**: Write the JWT to a browser cookie so it survives reloads.

**Why it failed**: `components.html()` runs JavaScript inside a tiny `<iframe>`.
Modern browsers treat iframe cookies as **third-party cookies** and block them.
The cookie was written into the iframe's separate cookie jar, then the iframe
was destroyed — the main page never saw it.

| Context | What runs there | Cookie access |
|---------|----------------|---------------|
| **Main page** | `st_javascript()` | Main page's cookie jar |
| **Iframe** | `components.html()` | Separate third-party jar (blocked) |

### Attempt 2: Browser Cookies via `st_javascript()` + Spinner ❌

**Idea**: Use `st_javascript()` for both writing AND reading, so both happen in
the main page context.

**Why it failed**: `st_javascript()` returns `None` on its **first render**
because the JavaScript hasn't finished executing yet. We tried using
`st.rerun()` to force a second render, but this created an **infinite loop**:

```
Render 1: cookie_token = None → st.rerun()
Render 2: cookie_checked flag not set yet (rerun killed execution)
           → st.rerun() again → INFINITE LOOP → UI never loads
```

`st.rerun()` kills script execution, so any flag set *after* the rerun call
was never reached.

### Attempt 3: Fixed the infinite loop with `_cookie_attempted` flag ❌

**Idea**: Set the flag *before* calling `st.rerun()` to prevent the loop.

```python
if not token and not _cookie_attempted:
    _cookie_attempted = True      # ← Set BEFORE rerun to break loop
    cookie_token = st_javascript(...)
    if cookie_token is None:
        st.rerun()                 # ← Now safe, loop broken
```

**Why it failed partially**: The loop stopped, but two new bugs appeared:

1. **Function definition order**: `fetch_chat_history()` and
   `messages_from_history()` were called at line ~1020 but defined at lines
   ~1092-1116. On a fresh reload, Python raised `NameError`, caught silently
   by `except Exception:` → chat history silently set to `[]`.

2. **Logout broken**: After logout, `_clear_cookie()` used `st_javascript`
   which may fail silently. The auto-restore logic still read the old cookie
   on the next render and silently re-logged the user in.

### Final Solution: `st.query_params` ✅

**Idea**: Stop fighting with third-party JS timing entirely. Use Streamlit's
built-in `st.query_params` which persists data **in the URL bar** natively.

```python
# On login:
st.query_params["token"] = token          # URL becomes ?token=eyJ...

# On page reload:
url_token = st.query_params.get("token")   # Reads from URL instantly — no JS needed

# On logout:
del st.query_params["token"]               # Removes from URL immediately
```

**Why this works**: Query parameters live in the **URL itself**, not in server
RAM or browser cookies. A page reload sends the same URL back, so
`?token=eyJ...` is still there. No timing issues, no iframes, no JavaScript
execution delays.

---

## Function Ordering Bug (Fixed Along the Way)

During implementation, a subtle bug caused chat history to be empty even when
the session was restored:

```
Line 1020: history = fetch_chat_history(url_token)     ← CALLED HERE
Line 1021: messages = messages_from_history(history)   ← CALLED HERE
...
Line 1092: def fetch_chat_history(token):              ← DEFINED HERE
Line 1116: def messages_from_history(history):         ← DEFINED HERE
```

On a fresh reload, the script runs top-to-bottom. Line 1020 tries to call
`fetch_chat_history()`, but it's defined at line 1092 — which hasn't been
reached yet. Python raises `NameError`, caught silently by `except Exception:`,
and chat history is set to `[]`.

**Fix**: Moved the three function definitions (`fetch_chat_history`,
`clear_chat_history`, `messages_from_history`) to **above** the restore block.

---

## Current Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    LOGIN                                     │
│  POST /login → verify password → mint JWT                   │
│  st.session_state.token = JWT   (RAM)                       │
│  st.query_params["token"] = JWT  (URL — persists reloads)   │
│  fetch_chat_history()            (PostgreSQL → chat bubbles) │
├─────────────────────────────────────────────────────────────┤
│                    PAGE RELOAD                               │
│  st.session_state wiped (fresh session)                     │
│  URL still has ?token=eyJ...                                │
│  st.query_params.get("token") → "eyJ..."                    │
│  verify_token("eyJ...") → valid                             │
│  st.session_state.token = "eyJ..."                          │
│  fetch_chat_history() → 50 items from PostgreSQL             │
│  App loads with full chat history                            │
├─────────────────────────────────────────────────────────────┤
│                    LOGOUT                                    │
│  del st.query_params["token"]  (URL clears)                 │
│  st.session_state cleared       (RAM clears)                │
│  Login form shown               (no token anywhere)          │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation Details

### Login Flow (`ui.py`, login form)

```python
token = login_user(username_input, password_input)
if token:
    st.session_state.token = token
    st.session_state.username = username_input
    st.query_params["token"] = token            # Persist to URL
    history = fetch_chat_history(token)
    st.session_state.messages = messages_from_history(history) if history else []
    st.rerun()
```

### Session Restore Flow (`ui.py`, top of script)

```python
if not st.session_state.get("token"):
    url_token = st.query_params.get("token")
    if url_token:
        from packages.shared.auth import verify_token
        payload = verify_token(url_token)
        if payload:
            st.session_state.token = url_token
            st.session_state.username = payload.get("username", "")
            try:
                history = fetch_chat_history(url_token)
                st.session_state.messages = messages_from_history(history) if history else []
            except Exception:
                st.session_state.messages = []
        else:
            del st.query_params["token"]       # Expired — clear and re-login
```

### Logout Flow (`ui.py`, sidebar)

```python
if st.button("Log Out"):
    if "token" in st.query_params:
        del st.query_params["token"]
    st.session_state.token = None
    st.session_state.username = None
    st.session_state.messages = []
    st.rerun()
```

---

## Security Considerations

| Concern | Mitigation |
|---------|-----------|
| Token visible in URL bar | Acceptable for internal tool. JWT expires in 30 min, signed with SECRET_KEY, cannot be forged |
| Stale token after expiry | `verify_token()` checks `exp` claim; expired tokens trigger `del st.query_params["token"]` |
| User logs out | `del st.query_params["token"]` removes from URL immediately |
| Server restart / deploy | Query params persist in browser URL — no server-side state to lose |

---

## Key Lesson

**Don't fight the framework.** Streamlit's `st.query_params` is built-in,
reliable, and instant. Third-party JS bridges (`streamlit-javascript`) have
unpredictable timing relative to Streamlit's render cycle, and
`components.html` iframes have browser security restrictions that make them
unsuitable for cookie management.
