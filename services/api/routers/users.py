"""
User management endpoints: admin-only CRUD for the users table.
"""

from fastapi import APIRouter, Depends, HTTPException

from packages.shared.schemas import UserCreate, UserUpdate, UserListResponse
from packages.shared.db import get_db_session
from packages.shared.models import User
from packages.shared.auth import get_pwd_hash
from services.api.dependencies import require_admin, log_audit_action

router = APIRouter(tags=["users"])


@router.get("/users", response_model=list[UserListResponse])
async def list_users(current_user: dict = Depends(require_admin)):
    """List all users (admin only)."""
    try:
        with get_db_session() as session:
            users = session.query(User).order_by(User.created_at).all()
            return [
                UserListResponse(
                    id=str(u.id),
                    username=u.username,
                    department=u.department,
                    access_level=u.access_level,
                    is_active=u.is_active,
                    created_at=u.created_at.isoformat() if u.created_at else "",
                )
                for u in users
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list users: {e}")


@router.post("/users", response_model=UserListResponse)
async def create_user(user: UserCreate, current_user: dict = Depends(require_admin)):
    """Create a new user with admin-assigned password (admin only)."""
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
                current_user.get("username"),
                "user_created",
                {
                    "target_user": user.username,
                    "department": user.department,
                    "access_level": user.access_level,
                },
            )

            return UserListResponse(
                id=str(new_user.id),
                username=new_user.username,
                department=new_user.department,
                access_level=new_user.access_level,
                is_active=new_user.is_active,
                created_at=new_user.created_at.isoformat()
                if new_user.created_at
                else "",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create user: {e}")


@router.patch("/users/{user_id}", response_model=UserListResponse)
async def update_user(
    user_id: str,
    updates: UserUpdate,
    current_user: dict = Depends(require_admin),
):
    """Update a user's department and/or access level (admin only)."""
    try:
        with get_db_session() as session:
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            changes = {}
            if updates.department is not None:
                changes["department"] = updates.department
                user.department = updates.department
            if updates.access_level is not None:
                changes["access_level"] = updates.access_level
                user.access_level = updates.access_level

            if not changes:
                raise HTTPException(status_code=400, detail="No fields to update")

            session.commit()
            session.refresh(user)

            log_audit_action(
                current_user.get("username"),
                "user_updated",
                {"target_user": user.username, **changes},
            )

            return UserListResponse(
                id=str(user.id),
                username=user.username,
                department=user.department,
                access_level=user.access_level,
                is_active=user.is_active,
                created_at=user.created_at.isoformat() if user.created_at else "",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update user: {e}")


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, current_user: dict = Depends(require_admin)):
    """Permanently delete a user (admin only)."""
    try:
        with get_db_session() as session:
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            username = user.username
            session.delete(user)
            session.commit()

            log_audit_action(
                current_user.get("username"),
                "user_deleted",
                {"target_user": username},
            )

            return {"status": "deleted", "user_id": user_id, "username": username}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete user: {e}")
