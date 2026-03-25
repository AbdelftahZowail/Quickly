"""Authentication and authorization system.

Provides JWT-based session auth (httpOnly cookies) and API key auth
for programmatic access. The first registered user becomes admin.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.models import User

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import bcrypt
import hashlib
import hmac
from jose import JWTError, jwt
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.time import utcnow

log = logging.getLogger("quickly.auth")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Secret key for JWT signing – MUST be set via environment variable in production.
# A random fallback is generated at startup so dev/test still works, but it
# means tokens are invalidated on every restart (which is fine for dev).
SECRET_KEY: str = os.getenv("QUICKLY_SECRET_KEY", "")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(64)
    log.warning(
        "QUICKLY_SECRET_KEY not set – generated ephemeral key. "
        "Set it in .env for persistent sessions across restarts."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

# ---------------------------------------------------------------------------
# Password hashing (bcrypt directly – passlib is unmaintained and breaks on bcrypt>=4)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# API key hashing (HMAC-SHA256 – constant-time, no salt needed for random keys)
# ---------------------------------------------------------------------------

_API_KEY_HASH_SECRET = os.getenv("QUICKLY_SECRET_KEY", SECRET_KEY).encode()


def hash_api_key(raw_key: str) -> str:
    return hmac.new(_API_KEY_HASH_SECRET, raw_key.encode(), hashlib.sha256).hexdigest()


def verify_api_key(raw_key: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_api_key(raw_key), hashed)


def _reinit_secret_key(key: str) -> None:
    """Update the in-memory signing key – called by startup when the key is
    auto-generated and stored in the database rather than supplied via env."""
    global SECRET_KEY, _API_KEY_HASH_SECRET
    SECRET_KEY = key
    _API_KEY_HASH_SECRET = key.encode()


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def create_access_token(user_id: int, role: str) -> str:
    expire = utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": str(user_id), "role": role, "type": "access", "exp": expire},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def create_refresh_token(user_id: int, role: str) -> str:
    expire = utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    return jwt.encode(
        {"sub": str(user_id), "role": role, "type": "refresh", "exp": expire},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


# ---------------------------------------------------------------------------
# FastAPI dependency – extract current user from cookie or Bearer header
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
):
    """Resolve the authenticated user from JWT cookie or API key header.

    Priority:
    1. ``Authorization: Bearer <jwt>`` header (frontend uses this)
    2. ``X-API-Key: <raw_key>`` header (programmatic API access) — O(1) lookup by HMAC hash
    3. ``access_token`` httpOnly cookie (fallback)

    Returns the User ORM object. Raises 401 if unauthenticated.
    """
    from app.models import User, APIKey  # deferred to avoid circular import

    token: str | None = None

    # 1. Check Bearer header
    if credentials and credentials.credentials:
        token = credentials.credentials

    # 2. Check X-API-Key header – hash and do a direct DB lookup (O(1))
    api_key_header = request.headers.get("X-API-Key")
    if api_key_header:
        hashed = hash_api_key(api_key_header)
        now = utcnow()
        result = await db.execute(
            select(APIKey).where(
                APIKey.key_hash == hashed,
                APIKey.revoked == False,  # noqa: E712
                (APIKey.expires_at == None) | (APIKey.expires_at > now),  # noqa: E711
            )
        )
        ak = result.scalar_one_or_none()
        if ak is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        # Update last used timestamp
        ak.last_used_at = utcnow()
        await db.flush()
        user = await db.get(User, ak.user_id)
        if user and user.is_active:
            return user
        raise HTTPException(status_code=401, detail="User account disabled")

    # 3. Check cookie fallback
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    # Decode JWT
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await db.get(User, int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or disabled")

    return user


async def try_resolve_user_for_mcp(
    db: AsyncSession,
    *,
    x_api_key: str | None,
    authorization: str | None,
) -> Optional[User]:
    """Like ``get_current_user`` but for MCP HTTP: header strings only, no raise.

    Order matches API access: ``X-API-Key`` first, then ``Authorization: Bearer`` JWT.
    """
    from app.models import User, APIKey  # deferred to avoid circular import

    if x_api_key and x_api_key.strip():
        hashed = hash_api_key(x_api_key.strip())
        now = utcnow()
        result = await db.execute(
            select(APIKey).where(
                APIKey.key_hash == hashed,
                APIKey.revoked == False,  # noqa: E712
                (APIKey.expires_at == None) | (APIKey.expires_at > now),  # noqa: E711
            )
        )
        ak = result.scalar_one_or_none()
        if ak is None:
            return None
        ak.last_used_at = utcnow()
        await db.flush()
        user = await db.get(User, ak.user_id)
        if user and user.is_active:
            return user
        return None

    bearer: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    if not bearer:
        return None

    try:
        payload = decode_token(bearer)
    except HTTPException:
        return None
    if payload.get("type") != "access":
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    user = await db.get(User, int(user_id))
    if user and user.is_active:
        return user
    return None


async def require_admin(user=Depends(get_current_user)):
    """Dependency that raises 403 if the user is not an admin."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def is_setup_complete(db: AsyncSession) -> bool:
    """Check if at least one user exists (setup is done)."""
    from app.models import User
    result = await db.execute(select(func.count(User.id)))
    return result.scalar_one() > 0
