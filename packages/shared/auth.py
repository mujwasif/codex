import os
import hashlib
import uuid
from datetime import datetime, timedelta
from jose import jwt
from hashward import CryptContext
from packages.shared.config import SECRET_KEY, ALGORITHM, TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_MINUTES

# Password hashing context - uses argon2 by default, supports bcrypt fallback
pwd_context = CryptContext(
    schemes=["argon2", "bcrypt", "scrypt", "pbkdf2_sha256"],
    default="argon2",
    deprecated="auto"
)

def get_pwd_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access", "jti": str(uuid.uuid4())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "refresh", "jti": str(uuid.uuid4())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except Exception:
        return None

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()