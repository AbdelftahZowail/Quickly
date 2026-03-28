"""PostgreSQL backup/restore via pg_dump / pg_restore (subprocess).

Requires ``postgresql-client`` on PATH (Docker image installs it; see Dockerfile).
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

log = logging.getLogger("quickly.backup_pg")

BACKUP_FILENAME_PREFIX = "quickly-backup-"
BACKUP_FILENAME_SUFFIX = ".qbk"
BACKUP_GLOB = f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}"
DEFAULT_LOCAL_RETENTION = 10
DEFAULT_RELATIVE_BACKUP_PATH = "backups"


class BackupError(Exception):
    """Base class for backup/restore failures."""


class BackupUnsupportedError(BackupError):
    """Raised when the database URL is not PostgreSQL."""


class BackupToolError(BackupError):
    """Raised when pg_dump or pg_restore exits with an error."""


def is_postgresql_url(database_url: str) -> bool:
    u = database_url.strip().lower()
    if u.startswith("sqlite"):
        return False
    return u.startswith("postgresql://") or u.startswith("postgres://") or u.startswith(
        "postgresql+asyncpg://"
    )


def sync_connection_uri(database_url: str) -> str:
    """Normalize async SQLAlchemy URL to a libpq connection URI."""
    u = database_url.strip()
    if u.startswith("postgres://"):
        u = u.replace("postgres://", "postgresql://", 1)
    if u.startswith("postgresql+asyncpg://"):
        u = u.replace("postgresql+asyncpg://", "postgresql://", 1)
    return u


def pg_dump_custom(database_url: str) -> bytes:
    """Run pg_dump -Fc --no-owner; return custom-format bytes."""
    if not is_postgresql_url(database_url):
        raise BackupUnsupportedError("Backups require a PostgreSQL DATABASE_URL")
    uri = sync_connection_uri(database_url)
    proc = subprocess.run(
        ["pg_dump", "-Fc", "--no-owner", "-d", uri],
        capture_output=True,
        timeout=3600,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode(errors="replace").strip() or "pg_dump failed"
        log.error("pg_dump failed (rc=%s): %s", proc.returncode, err)
        raise BackupToolError(err)
    return proc.stdout


def pg_restore_replace(database_url: str, dump_path: str | Path) -> None:
    """Destructively restore custom-format dump (--clean --if-exists)."""
    if not is_postgresql_url(database_url):
        raise BackupUnsupportedError("Restore requires a PostgreSQL DATABASE_URL")
    uri = sync_connection_uri(database_url)
    path = str(dump_path)
    proc = subprocess.run(
        [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "-d",
            uri,
            path,
        ],
        capture_output=True,
        timeout=7200,
    )
    stderr = (proc.stderr or b"").decode(errors="replace").strip()
    if proc.returncode > 1:
        log.error("pg_restore failed (rc=%s): %s", proc.returncode, stderr)
        raise BackupToolError(stderr or "pg_restore failed")
    if proc.returncode == 1 and stderr:
        log.warning("pg_restore completed with warnings: %s", stderr)


def local_disk_backups_enabled() -> bool:
    """True when deploy enables server-side backup files (Docker Compose dev/build only)."""
    return os.getenv("QUICKLY_LOCAL_DISK_BACKUPS", "").strip().lower() in ("1", "true", "yes")


def normalize_user_backup_path(raw: str | None) -> str:
    s = (raw or "").strip() or DEFAULT_RELATIVE_BACKUP_PATH
    s = s.replace("\\", "/").strip("/")
    return s or DEFAULT_RELATIVE_BACKUP_PATH


def resolve_backup_directory(user_path: str | None) -> Path:
    """Resolve a path relative to the app working directory; reject escapes outside cwd."""
    base = Path.cwd().resolve()
    normalized = normalize_user_backup_path(user_path)
    candidate = Path(normalized)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as e:
        raise ValueError("Backup folder must be inside the application directory") from e
    return resolved


def prune_local_backups(directory: str | Path, *, keep: int = DEFAULT_LOCAL_RETENTION) -> None:
    """Keep the *keep* newest ``quickly-backup-*.qbk`` files; delete older."""
    root = Path(directory)
    if not root.is_dir():
        return
    files = sorted(
        root.glob(BACKUP_GLOB),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in files[keep:]:
        try:
            stale.unlink(missing_ok=True)
        except OSError as e:
            log.warning("Could not remove old backup %s: %s", stale, e)


def write_local_backup(data: bytes, *, user_relative_path: str | None) -> Path | None:
    """Write dump under the configured folder (under cwd) and prune to *keep* files."""
    if not local_disk_backups_enabled():
        return None
    root = resolve_backup_directory(user_relative_path)
    root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"{BACKUP_FILENAME_PREFIX}{ts}{BACKUP_FILENAME_SUFFIX}"
    path.write_bytes(data)
    prune_local_backups(root)
    return path


async def post_backup_webhook(
    url: str,
    data: bytes,
    filename: str,
    *,
    authorization: str | None = None,
    timeout: float = 120.0,
) -> None:
    """POST multipart file to webhook URL."""
    headers = {}
    if authorization:
        headers["Authorization"] = authorization
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            headers=headers,
            files={"file": (filename, data, "application/octet-stream")},
        )
        resp.raise_for_status()


def validate_cron_expression(expr: str) -> bool:
    """Five-field cron string (minute hour day month day_of_week)."""
    parts = expr.strip().split()
    return len(parts) == 5 and all(p.strip() for p in parts)


async def dump_to_thread(database_url: str) -> bytes:
    return await asyncio.to_thread(pg_dump_custom, database_url)


async def restore_from_path_to_thread(database_url: str, path: str | Path) -> None:
    await asyncio.to_thread(pg_restore_replace, database_url, path)


def validate_custom_format_dump_file(path: str | Path) -> None:
    """Ensure *path* is readable by pg_restore (custom format)."""
    path = str(path)
    proc = subprocess.run(
        ["pg_restore", "-l", path],
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode(errors="replace").strip() or "Invalid PostgreSQL dump"
        raise BackupToolError(err)


async def validate_dump_bytes_async(dump: bytes) -> None:
    """Run :func:`validate_custom_format_dump_file` on *dump* via a temp file."""

    def _run() -> None:
        import os
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".dump")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(dump)
            validate_custom_format_dump_file(path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    await asyncio.to_thread(_run)


async def restore_from_upload_to_thread(database_url: str, raw: bytes) -> None:
    def _run() -> None:
        with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        try:
            pg_restore_replace(database_url, tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    await asyncio.to_thread(_run)
