"""
Authentication endpoints: register, login, and token refresh.
"""

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.security import OAuth2PasswordRequestForm

from packages.shared.auth import (
    get_pwd_hash, verify_password, create_access_token,
    create_refresh_token, verify_token,
)
from packages.shared.schemas import Token, UserCreate, UserResponse
from packages.shared.db import get_db_session
from packages.shared.models import User
from services.api.dependencies import get_current_active_user, log_audit_action

router = APIRouter(tags=["auth"])


@router.post("/register", response_model=UserResponse)
async def register(user: UserCreate):
    try:
        with get_db_session() as session:
            existing = (
                session.query(User).filter(User.username == user.username).first()
            )
            if existing:
                raise HTTPException(status_code=400, detail="Username already exists")

            new_user = User(
                username=user.username,
                password_hash=get_pwd_hash(user.password),
                department=user.department,
                access_level=user.access_level,
                is_active=True,
            )
            session.add(new_user)
            session.commit()
            session.refresh(new_user)

            log_audit_action(
                user.username,
                "register",
                {"department": user.department, "access_level": user.access_level},
            )

            return UserResponse(
                username=new_user.username,
                department=new_user.department,
                access_level=new_user.access_level,
                is_active=new_user.is_active,
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registration failed: {e}")


@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    try:
        with get_db_session() as session:
            user = (
                session.query(User).filter(User.username == form_data.username).first()
            )
            if not user or not verify_password(form_data.password, user.password_hash):
                raise HTTPException(
                    status_code=400, detail="Incorrect username or password"
                )

            if not user.is_active:
                raise HTTPException(status_code=403, detail="Inactive user")

            access_token = create_access_token(
                data={"username": user.username, "access_level": user.access_level}
            )
            refresh_token = create_refresh_token(
                data={"username": user.username, "access_level": user.access_level}
            )

            log_audit_action(user.username, "login", {"success": True})

            return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Login failed: {e}")


@router.post("/refresh")
async def refresh_token(authorization: str = Header(...)):
    """Accept a refresh token, return a new access+refresh pair."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization[7:]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Token is not a refresh token")

    username = payload.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # Verify user still exists and is active
    user_dict = None
    try:
        with get_db_session() as session:
            user = session.query(User).filter(User.username == username).first()
            if not user or not user.is_active:
                raise HTTPException(status_code=401, detail="User not found or inactive")
            user_dict = {
                "username": user.username,
                "access_level": user.access_level,
            }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Token refresh failed")

    access_token = create_access_token(data=user_dict)
    new_refresh_token = create_refresh_token(data=user_dict)

    log_audit_action(username, "token_refresh", {"success": True})

    return {"access_token": access_token, "refresh_token": new_refresh_token, "token_type": "bearer"}
