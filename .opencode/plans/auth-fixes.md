# Auth Fixes Plan — 3 Changes

## Fix 1: Refresh Token Revocation

### 1a. DDL — `infra/db/init.sql`
Add after users table:
```sql
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash VARCHAR(64) PRIMARY KEY,
    username VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_username ON refresh_tokens(username);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires ON refresh_tokens(expires_at);
```

### 1b. ORM Model — `packages/shared/models.py`
Add after User class:
```python
class RefreshToken(Base):
    __tablename__ = 'refresh_tokens'
    __table_args__ = {'extend_existing': True}
    token_hash = Column(String(64), primary_key=True)
    username = Column(String(100), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
```

### 1c. Hash helper — `packages/shared/auth.py`
Add `import hashlib` at top, then:
```python
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
```

### 1d. Login — `services/api/routers/auth.py`
After creating refresh_token, store hash:
```python
from packages.shared.auth import hash_token
from packages.shared.models import RefreshToken
from datetime import timedelta
from packages.shared.config import REFRESH_TOKEN_EXPIRE_MINUTES

# After creating refresh_token (line 79):
session.add(RefreshToken(
    token_hash=hash_token(refresh_token),
    username=user.username,
    expires_at=datetime.utcnow() + timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES)
))
```

### 1e. Refresh endpoint — `services/api/routers/auth.py`
After verifying JWT and user:
```python
old_hash = hash_token(token)
db_token = session.query(RefreshToken).filter(RefreshToken.token_hash == old_hash).first()
if not db_token:
    raise HTTPException(status_code=401, detail="Refresh token revoked or not found")
session.delete(db_token)  # revoke old
# ... mint new tokens (existing code) ...
session.add(RefreshToken(token_hash=hash_token(new_refresh), username=username, expires_at=...))
```

## Fix 2: .env Security

1. `git rm --cached .env` — untrack without deleting
2. Rotate SECRET_KEY in `.env` to new random hex string
3. Add comment in `.env.example`: "Generate with: python -c 'import secrets; print(secrets.token_hex(32))'"

## Fix 3: Token Expiry Mismatch

Change in `packages/shared/config.py` line 64:
```python
# Before:
TOKEN_EXPIRE_MINUTES = int(os.getenv("TOKEN_EXPIRE_MINUTES", "30"))
# After:
TOKEN_EXPIRE_MINUTES = int(os.getenv("TOKEN_EXPIRE_MINUTES", "360"))
```

## File Summary

| File | Changes |
|------|---------|
| `infra/db/init.sql` | +8 lines (refresh_tokens table) |
| `packages/shared/models.py` | +12 lines (RefreshToken model) |
| `packages/shared/auth.py` | +3 lines (import hashlib, hash_token func) |
| `services/api/routers/auth.py` | ~15 lines (login: store hash, refresh: check/revoke/store) |
| `packages/shared/config.py` | 1 line changed (30 → 360) |
| `.env` | SECRET_KEY rotated |
| `.env.example` | +1 comment |

## Execute with
```bash
# After plan approval, execute these edits in order:
# 1. init.sql — add table
# 2. models.py — add model
# 3. auth.py — add hash helper
# 4. auth.py router — update login + refresh endpoints
# 5. config.py — fix default
# 6. .env — rotate key
# 7. git rm --cached .env
```
