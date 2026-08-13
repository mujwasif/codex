"""
Shared FastAPI dependencies for the Codex API.

Centralizes authentication, audit logging, and the mock user store so the
endpoint routers stay thin.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from codex.packages.shared.auth import verify_token, get_pwd_hash
from codex.packages.shared.db import get_db_session
from codex.packages.shared.models import AuditLog

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# MOCK USER DATABASE for MVP (Normally in Postgres)
# Passwords are hashed with argon2 at startup (via hashward CryptContext)
MOCK_USERS = {
    "admin": {"username": "admin", "password": get_pwd_hash("password123"), "department": "IT", "access_level": 3, "is_active": True},
    "manager": {"username": "manager", "password": get_pwd_hash("password123"), "department": "Finance", "access_level": 2, "is_active": True},
    "employee": {"username": "employee", "password": get_pwd_hash("password123"), "department": "HR", "access_level": 1, "is_active": True},
}


def log_audit_action(actor: str, action: str, payload: dict = None):
    """Log an action to the audit trail."""
    try:
        with get_db_session() as session:
            audit_entry = AuditLog(
                actor=actor,
                action=action,
                payload=payload or {}
            )
            session.add(audit_entry)
            session.commit()
    except Exception as e:
        print(f"⚠️ Audit logging failed: {e}")


async def get_current_user(token: str = Depends(oauth2_scheme)):
    user_data = verify_token(token)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_data


def get_current_active_user(current_user: dict = Depends(get_current_user)):
    username = current_user.get("username")
    user = MOCK_USERS.get(username)
    if not user or not user.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive or deleted user"
        )
    return current_user


def require_admin(current_user: dict = Depends(get_current_active_user)):
    """Enforce admin access (access_level >= 3)."""
    if current_user.get("access_level", 1) < 3:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
