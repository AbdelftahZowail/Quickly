"""Authentication API routes: register, login, token refresh, API key management."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Response,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_api_key,
    hash_password,
    is_setup_complete,
    require_admin,
    verify_password,
)
from app.backup_pg import BackupToolError, BackupUnsupportedError
from app.backup_restore_ops import restore_database_from_bytes
from app.database import AsyncSessionLocal, get_db
from app.models import APIKey, User
from app.time import utcnow

log = logging.getLogger("quickly.auth.routes")

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=150)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username must contain only letters, numbers, hyphens, and underscores")
        return v.lower()

    @field_validator("email")
    @classmethod
    def email_format(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email format")
        return v.lower()

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class CreateAPIKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scopes: list[str] = []
    expires_in_days: int | None = None  # None = no expiry


class APIKeyResponse(BaseModel):
    id: int
    name: str
    prefix: str
    scopes: list[str]
    revoked: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TOKEN_EXPIRE_MINUTES * 60


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Setup check endpoint (publicly accessible)
# ---------------------------------------------------------------------------


@router.get("/setup-status")
async def setup_status(db: AsyncSession = Depends(get_db)):
    """Check if initial setup (first user registration) is complete."""
    done = await is_setup_complete(db)
    return {"setup_complete": done}


@router.post("/restore-setup")
async def restore_setup(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """Destructively restore PostgreSQL from a backup before any user exists."""
    async with AsyncSessionLocal() as db:
        if await is_setup_complete(db):
            raise HTTPException(
                status_code=403,
                detail="Setup already complete. Sign in and use Settings → Backup to restore.",
            )
    raw = await file.read()
    if not raw or len(raw) < 64:
        raise HTTPException(status_code=400, detail="Invalid or empty backup file")
    try:
        await restore_database_from_bytes(raw, background_tasks)
    except BackupUnsupportedError:
        raise HTTPException(status_code=501, detail="Restore requires PostgreSQL.")
    except BackupToolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log.warning("Database restored from backup during pre-setup flow")
    return {"ok": True, "detail": "Database restored; you can sign in or create an admin account."}


# ---------------------------------------------------------------------------
# Registration & Login
# ---------------------------------------------------------------------------


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user. The first user automatically becomes admin.
    Subsequent registrations are closed and require an admin to create accounts.
    """
    setup_done = await is_setup_complete(db)
    if setup_done:
        raise HTTPException(
            status_code=403,
            detail="Registration closed. Contact an admin to create new accounts.",
        )

    # Check for existing username/email
    existing = await db.execute(
        select(User).where((User.username == data.username) | (User.email == data.email))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username or email already taken")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        role="admin",  # First user is always admin
        is_active=True,
    )
    db.add(user)
    await db.flush()
    log.info("First user registered: %s (admin)", user.username)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """Authenticate and return JWT tokens."""
    result = await db.execute(select(User).where(User.username == data.username.lower()))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    access_token = create_access_token(user.id, user.role)
    refresh_token = create_refresh_token(user.id, user.role)

    # Set httpOnly cookie for the refresh token scoped to the auth prefix
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,  # 7 days
        path="/api/auth",
    )
    # Also set the access token as an httpOnly cookie so logged-in browsers
    # can reach API endpoints directly (e.g. /api/office365/graph-webhook/subscriptions)
    # without needing a custom Authorization header.
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/api",
    )

    return TokenResponse(access_token=access_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Exchange a refresh token for a new access token."""
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")

    payload = decode_token(token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = payload.get("sub")
    user = await db.get(User, int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or disabled")

    # Issue new tokens (refresh token rotation)
    new_access = create_access_token(user.id, user.role)
    new_refresh = create_refresh_token(user.id, user.role)

    response.set_cookie(
        key="refresh_token",
        value=new_refresh,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,
        path="/api/auth",
    )
    # Rotate the access token cookie too
    response.set_cookie(
        key="access_token",
        value=new_access,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/api",
    )

    return TokenResponse(access_token=new_access)


@router.post("/logout")
async def logout(response: Response):
    """Clear the refresh token cookie."""
    response.delete_cookie("refresh_token", path="/api/auth")
    return {"detail": "Logged out"}


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserResponse)
async def get_me(user=Depends(get_current_user)):
    """Get the currently authenticated user."""
    return user


@router.put("/change-password")
async def change_password(
    data: ChangePasswordRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change the current user's password."""
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(data.new_password)
    await db.flush()
    return {"detail": "Password changed"}


# ---------------------------------------------------------------------------
# User management (admin only)
# ---------------------------------------------------------------------------


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    data: RegisterRequest,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin creates a new user."""
    existing = await db.execute(
        select(User).where((User.username == data.username) | (User.email == data.email))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username or email already taken")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        role="user",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


@router.get("/users", response_model=list[UserResponse])
async def list_users(admin=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """List all users (admin only)."""
    result = await db.execute(select(User).order_by(User.id))
    return result.scalars().all()


# ---------------------------------------------------------------------------
# API Key management
# ---------------------------------------------------------------------------


@router.post("/api-keys")
async def create_api_key(
    data: CreateAPIKeyRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new API key. The raw key is returned ONLY once."""
    raw_key = f"qk_{secrets.token_urlsafe(48)}"
    prefix = raw_key[:12]

    expires_at = None
    if data.expires_in_days:
        expires_at = utcnow() + timedelta(days=data.expires_in_days)

    api_key = APIKey(
        user_id=user.id,
        name=data.name,
        key_hash=hash_api_key(raw_key),
        prefix=prefix,
        scopes=data.scopes,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.flush()

    return {
        "id": api_key.id,
        "name": api_key.name,
        "key": raw_key,  # ONLY returned on creation
        "prefix": prefix,
        "scopes": data.scopes,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "message": "Save this key now — it cannot be retrieved again.",
    }


@router.get("/api-keys", response_model=list[APIKeyResponse])
async def list_api_keys(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List all API keys for the current user (hashes are never exposed)."""
    result = await db.execute(
        select(APIKey).where(APIKey.user_id == user.id).order_by(APIKey.created_at.desc())
    )
    return result.scalars().all()


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    key_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an API key (soft delete)."""
    api_key = await db.get(APIKey, key_id)
    if not api_key or api_key.user_id != user.id:
        raise HTTPException(status_code=404, detail="API key not found")
    api_key.revoked = True
    await db.flush()
    return {"detail": "API key revoked"}
