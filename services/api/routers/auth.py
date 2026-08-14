"""
Authentication endpoints: register and login.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from codex.packages.shared.auth import get_pwd_hash, verify_password, create_access_token
from codex.packages.shared.schemas import Token, UserCreate, UserResponse
from codex.packages.shared.db import get_db_session
from codex.packages.shared.models import User
from codex.services.api.dependencies import log_audit_action

router = APIRouter(tags=["auth"])


@router.post("/register", response_model=UserResponse)
async def register(user: UserCreate):
    try:
        with get_db_session() as session:
            existing = session.query(User).filter(User.username == user.username).first()
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

            log_audit_action(user.username, "register", {
                "department": user.department,
                "access_level": user.access_level
            })

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
            user = session.query(User).filter(User.username == form_data.username).first()
            if not user or not verify_password(form_data.password, user.password_hash):
                raise HTTPException(status_code=400, detail="Incorrect username or password")

            if not user.is_active:
                raise HTTPException(status_code=403, detail="Inactive user")

            access_token = create_access_token(
                data={"username": user.username, "access_level": user.access_level}
            )

            log_audit_action(user.username, "login", {"success": True})

            return {"access_token": access_token, "token_type": "bearer"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Login failed: {e}")
