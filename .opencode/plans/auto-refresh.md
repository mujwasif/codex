# Auto-Refresh Token Plan — 3 Fixes

## Fix 1: Streamlit UI — `services/chat/ui.py`
**Status: DONE** (agent already applied changes)

Added `api_request()` helper with 401-intercept + `_try_refresh_token()`. All 16 API functions refactored to use it. `token` parameter removed from all function signatures.

## Fix 2: CLI — `services/chat/cli.py`

Replace entire file. Key changes:
- Module-level `access_token` and `refresh_token` globals
- `login()` stores both tokens (not just access_token)
- New `_refresh_access_token()` calls `POST /refresh`
- New `_api_request(method, endpoint, **kwargs)` wraps all API calls with 401-retry
- `ask_codex()`, `print_ingestion_status()`, `delete_document()` all use `_api_request()`
- Remove `token` parameter from all function signatures
- All call sites in `main()` updated

## Fix 3: Server Auto-Refresh — `services/api/dependencies.py`

Update `get_current_user()` to accept optional `X-Refresh-Token` header. When access token is expired but refresh token is valid, auto-mint a new access token and include it in `X-New-Access-Token` response header.

### Changes to `dependencies.py`:
```python
from fastapi import Header
from packages.shared.auth import create_access_token, hash_token
from packages.shared.models import RefreshToken

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    x_refresh_token: str = Header(default=None),
):
    user_data = verify_token(token)
    if not user_data:
        # Access token invalid — try auto-refresh
        if x_refresh_token:
            refresh_payload = verify_token(x_refresh_token)
            if refresh_payload and refresh_payload.get("type") == "refresh":
                username = refresh_payload.get("username")
                if username:
                    with get_db_session() as session:
                        h = hash_token(x_refresh_token)
                        db_token = session.query(RefreshToken).filter(RefreshToken.token_hash == h).first()
                        user = session.query(User).filter(User.username == username).first()
                        if db_token and user and user.is_active:
                            new_access = create_access_token({"username": username, "access_level": user.access_level})
                            # Store for response header injection
                            request.state.new_access_token = new_access
                            return {"username": username, "access_level": user.access_level}
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    ...
```

### Changes to `main.py`:
Add middleware to inject `X-New-Access-Token` header in response when auto-refreshed.

### Changes to `services/chat/ui.py` `api_request()`:
Send `X-Refresh-Token` header on every request, read `X-New-Access-Token` from response:
```python
def api_request(method, path, **kwargs):
    ...
    headers["X-Refresh-Token"] = st.session_state.get("refresh_token", "")
    resp = requests.request(...)
    # Auto-refresh happened server-side
    new_token = resp.headers.get("X-New-Access-Token")
    if new_token:
        st.session_state.token = new_token
    ...
```

### Changes to `services/chat/cli.py` `_api_request()`:
Same pattern — send `X-Refresh-Token`, read `X-New-Access-Token`.

## File Summary

| File | Status | Changes |
|------|--------|---------|
| `services/chat/ui.py` | DONE | api_request() added, 16 functions refactored |
| `services/chat/cli.py` | Plan ready | Full rewrite with refresh support |
| `services/api/dependencies.py` | Plan ready | Auto-refresh in get_current_user() |
| `services/api/main.py` | Plan ready | Middleware to inject X-New-Access-Token |
