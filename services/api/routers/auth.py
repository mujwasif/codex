"""
Authentication endpoints: register and login.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from codex.packages.shared.auth import get_pwd_hash, verify_password, create_access_token
from codex.packages.shared.schemas import Token, UserCreate, UserResponse
from codex.services.api.dependencies import MOCK_USERS, log_audit_action

router = APIRouter(tags=["auth"])


@router.post("/register", response_model=UserResponse)
async def register(user: UserCreate):
    if user.username in MOCK_USERS:
        raise HTTPException(status_code=400, detail="Username already exists")

    MOCK_USERS[user.username] = {
        "username": user.username,
        "password": get_pwd_hash(user.password),
        "department": user.department,
        "access_level": user.access_level,
        "is_active": True
    }

    log_audit_action(user.username, "register", {
        "department": user.department,
        "access_level": user.access_level
    })

    return MOCK_USERS[user.username]


@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = MOCK_USERS.get(form_data.username)
    if not user or not verify_password(form_data.password, user["password"]):
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    if not user.get("is_active", False):
        raise HTTPException(status_code=403, detail="Inactive user")

    access_token = create_access_token(data={"username": user["username"], "access_level": user["access_level"]})

    log_audit_action(user["username"], "login", {"success": True})

    return {"access_token": access_token, "token_type": "bearer"}
