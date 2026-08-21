"""
Shared FastAPI dependencies for the Codex API.

Centralizes authentication, audit logging, and the user store so the
endpoint routers stay thin.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from packages.shared.auth import verify_token
from packages.shared.db import get_db_session
from packages.shared.models import AuditLog, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def get_user_from_db(username: str) -> dict | None:
    """Look up a user by username from PostgreSQL."""
    try:
        with get_db_session() as session:
            user = session.query(User).filter(User.username == username).first()
            if not user:
                return None
            return {
                "username": user.username,
                "department": user.department,
                "access_level": user.access_level,
                "is_active": user.is_active,
            }
    except Exception as e:
        print(f"⚠️ DB user lookup failed: {e}")
        return None


def log_audit_action(actor: str, action: str, payload: dict = None):
    """Log an action to the audit trail."""
    try:
        with get_db_session() as session:
            audit_entry = AuditLog(actor=actor, action=action, payload=payload or {})
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
    user = get_user_from_db(username)
    if not user or not user.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Inactive or deleted user"
        )
    return current_user


def require_admin(current_user: dict = Depends(get_current_active_user)):
    """Enforce admin access (access_level >= 3)."""
    if current_user.get("access_level", 1) < 3:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
