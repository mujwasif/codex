import os
from datetime import datetime, timedelta
from jose import jwt
from hashward import CryptContext

# In production, set these via environment variables!
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-to-a-random-secure-string")
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 30

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
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except Exception:
        return None