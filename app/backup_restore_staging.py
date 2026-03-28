"""Short-lived decrypted dumps for two-step restore (preview → confirm → execute)."""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger("quickly.backup_restore_staging")

TOKEN_TTL_SEC = 15 * 60
_STAGING_SUBDIR = "quickly-restore-staging"


def _staging_root() -> Path:
    base = os.getenv("QUICKLY_RESTORE_STAGING_DIR", "").strip()
    if base:
        root = Path(base)
    else:
        root = Path(os.environ.get("TMPDIR", "/tmp"))
    d = (root / _STAGING_SUBDIR).resolve()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _meta_path(token: str) -> Path:
    return _staging_root() / f"{token}.json"


def stage_decrypted_dump(dump_bytes: bytes, *, kind: str) -> tuple[str, int]:
    """Write dump to a temp file; return (token, ttl_seconds)."""
    root = _staging_root()
    token = secrets.token_urlsafe(32)
    dump_path = root / f"{token}.dump"
    dump_path.write_bytes(dump_bytes)
    try:
        os.chmod(dump_path, 0o600)
    except OSError:
        pass
    meta = {
        "path": str(dump_path),
        "created": time.time(),
        "kind": kind,
    }
    mp = _meta_path(token)
    mp.write_text(json.dumps(meta), encoding="utf-8")
    try:
        os.chmod(mp, 0o600)
    except OSError:
        pass
    return token, TOKEN_TTL_SEC


def consume_staged_dump(token: str, *, expected_kind: str) -> Path | None:
    """Load staged path if token valid and kind matches; delete meta; caller deletes dump file."""
    mp = _meta_path(token)
    if not mp.is_file():
        return None
    try:
        meta = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if meta.get("kind") != expected_kind:
        return None
    created = float(meta.get("created", 0))
    if time.time() - created > TOKEN_TTL_SEC:
        _cleanup_token(token, meta.get("path"))
        return None
    path_str = meta.get("path")
    if not path_str or not isinstance(path_str, str):
        mp.unlink(missing_ok=True)
        return None
    p = Path(path_str)
    if not p.is_file():
        mp.unlink(missing_ok=True)
        return None
    try:
        mp.unlink()
    except OSError:
        pass
    return p


def delete_staged_dump(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Could not remove staged restore file %s: %s", path, e)


def _cleanup_token(token: str, path_str: object | None) -> None:
    _meta_path(token).unlink(missing_ok=True)
    if isinstance(path_str, str):
        Path(path_str).unlink(missing_ok=True)


def purge_expired_staging() -> None:
    """Remove expired meta + dump files (best-effort)."""
    root = _staging_root()
    now = time.time()
    for p in root.glob("*.json"):
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
            created = float(meta.get("created", 0))
            if now - created <= TOKEN_TTL_SEC:
                continue
            tok = p.stem
            _cleanup_token(tok, meta.get("path"))
        except (OSError, json.JSONDecodeError):
            continue
