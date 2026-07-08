# Authentication and Access Control System

## Overview
The Codex authentication system implements Role-Based Access Control (RBAC) using JSON Web Tokens (JWT). It ensures that users are identified, their identity is verified, and their access to specific policy documents is restricted based on their organizational role.

## 1. User Access Levels
The system uses a numeric hierarchy to control data visibility:
- **Level 1 (Standard/Employee):** Access to general company policies.
- **Level 2 (Manager):** Access to general policies + manager-level thresholds and internal memos.
- **Level 3 (Admin):** Full access to all documents, including restricted administrative policies.

## 2. Password Hashing (Argon2id via hashward)
All passwords are securely hashed before storage using the `argon2id` scheme via `hashward` (a modern passlib replacement):
- **`get_pwd_hash(password)`**: Converts a plain-text password into an argon2id hash.
- **`verify_password(plain_password, hashed_password)`**: Compares a raw password against its hash.
- **Why:** Argon2id is the winner of the Password Hashing Competition (2015) and is memory-hard, making it resistant to GPU/ASIC attacks. It's more secure than bcrypt and is RFC 9106 standardized.
- **Fallback:** The system supports bcrypt, scrypt, and pbkdf2_sha256 as fallback schemes for backward compatibility.

## 3. User Registration Flow (`/register`)
New users can be added to the system via the `POST /register` endpoint:
1. **Validation:** The system checks if the username already exists.
2. **Hashing:** The plain-text password is hashed using `get_pwd_hash()`.
3. **Storage:** The user record is created with `is_active: True` and the appropriate `access_level`.
4. **Response:** Returns the user profile (excluding the password).

## 4. The Login Flow (Identity Verification)
The `/login` endpoint manages the transition from credentials to a secure session:
1. **Credential Check:** The system validates the `username` and verifies the password using `verify_password()`.
2. **Active Check:** The system confirms the user's `is_active` status.
3. **Token Generation:** Upon successful validation, the `create_access_token` function generates a JWT.
4. **Payload:** The token embeds the user's `username` and `access_level`.
5. **Expiration:** Tokens are set to expire in 30 minutes to minimize the risk of token theft.

## 5. Active User Check (`get_current_active_user`)
A secondary security layer ensures that deactivated users cannot access the system:
1. **Token Verification:** The JWT is first verified by `get_current_user`.
2. **Active Status Lookup:** The system checks the `is_active` flag in the user database.
3. **Enforcement:** If the user is inactive, a `403 Forbidden` error is raised.

## 6. The Query Flow (Authorization)
Every request to `/query` must be accompanied by a Bearer token in the Authorization header.
1. **Token Extraction:** The `get_current_user` dependency extracts the token.
2. **Verification:** `verify_token` checks the signature against the `SECRET_KEY` and confirms the token has not expired.
3. **Active Check:** `get_current_active_user` verifies the user's active status.
4. **RBAC Application:** The `access_level` is extracted from the decoded JWT and passed to the `search_policy` function.
5. **Data Filtering:** The database query uses this level to exclude documents that exceed the user's permissions.

## 7. Security Implementation Details
- **Hashing:** Passwords are handled via `hashward` with the `argon2id` scheme (default), with fallback support for bcrypt, scrypt, and pbkdf2_sha256.
- **Signing:** Tokens are signed using the `HS256` algorithm.
- **RBAC Logic:** The system fails closed; if no access level is found, it defaults to Level 1.
- **Self-Contained JWT:** The access level is embedded in the token, avoiding database lookups on every request.

## 8. MVP Limitations & Roadmap
- **Current:** Hardcoded SECRET_KEY. $\rightarrow$ **Future:** Environment variable management.
- **Current:** Short-lived tokens. $\rightarrow$ **Future:** Refresh token rotation.
- **Current:** Mock database. $\rightarrow$ **Future:** PostgreSQL with SQLAlchemy ORM.
- **Current:** Migrated from bcrypt to argon2id via hashward. $\rightarrow$ **Future:** Automatic scheme upgrades and migration from legacy hashes.
