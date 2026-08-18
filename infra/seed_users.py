#!/usr/bin/env python3
"""Seed development users without overwriting existing accounts."""

from __future__ import annotations

import os
import sys
import uuid

from packages.shared.auth import get_pwd_hash
from packages.shared.db import get_db_session
from packages.shared.models import User


DEFAULT_USERS = (
    ("admin", "password123", "IT", 3),
    ("manager", "password123", "Finance", 2),
    ("employee", "password123", "HR", 1),
)


def main() -> int:
    enabled = os.getenv("SEED_DEFAULT_USERS", "false").lower() == "true"
    if not enabled:
        print("Default user seeding disabled (SEED_DEFAULT_USERS is not true).")
        return 0

    created = []
    with get_db_session() as session:
        for username, password, department, access_level in DEFAULT_USERS:
            if session.query(User).filter(User.username == username).first():
                continue
            session.add(
                User(
                    id=str(uuid.uuid4()),
                    username=username,
                    password_hash=get_pwd_hash(password),
                    department=department,
                    access_level=access_level,
                    is_active=True,
                )
            )
            created.append(username)
        session.commit()

    if created:
        print(f"Created development users: {', '.join(created)}")
        print("WARNING: Replace password123 before production use.")
    else:
        print("Default users already exist; no accounts changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
