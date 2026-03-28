"""Quickly backup file format: manifest + pg_dump payload, optional AES-GCM encryption."""
from __future__ import annotations

import json
import os
import struct
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

MAGIC = b"QUICKLYBK\x02"
LEGACY_MAGIC = b"QUICKLYBK\x01"
VERSION = 2

FLAG_PLAIN = 0
FLAG_ENCRYPTED = 1

MIN_PASSWORD_LEN = 8
MAX_HINT_LEN = 200
MAX_MANIFEST_JSON = 256 * 1024

_PBKDF2_ITERATIONS = 390_000
_SALT_LEN = 16
_NONCE_LEN = 12


class BackupPackageError(Exception):
    """Invalid backup file, wrong password, or corrupt payload."""


def _app_version() -> str:
    try:
        from importlib.metadata import version

        return version("quickly")
    except Exception:
        pass
    return os.getenv("QUICKLY_VERSION", "unknown")


def mask_email_for_preview(email: str) -> str:
    """Mask local part for display before password unlock (anti-phishing)."""
    email = (email or "").strip()
    if not email or "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    domain = domain or "***"
    if len(local) <= 1:
        masked_local = "*"
    else:
        masked_local = f"{local[0]}***{local[-1]}"
    return f"{masked_local}@{domain}"


def build_preview_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Subset/sanitized manifest stored in plaintext before the ciphertext (encrypted backups)."""
    out = dict(manifest)
    emails = out.get("admin_emails")
    if isinstance(emails, list):
        out["admin_emails"] = [mask_email_for_preview(str(e)) for e in emails]
    return out


def build_manifest_dict(
    *,
    lead_count: int,
    inbox_count: int,
    campaign_count: int,
    user_count: int,
    admin_emails: list[str],
    encrypted: bool,
) -> dict[str, Any]:
    return {
        "format_version": VERSION,
        "app_version": _app_version(),
        "backed_up_at": datetime.now(timezone.utc).isoformat(),
        "encrypted": encrypted,
        "lead_count": lead_count,
        "inbox_count": inbox_count,
        "campaign_count": campaign_count,
        "user_count": user_count,
        "admin_emails": admin_emails,
    }


def _inner_blob(manifest: dict[str, Any], dump: bytes) -> bytes:
    mj = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(mj) > MAX_MANIFEST_JSON:
        raise BackupPackageError("Manifest too large")
    return struct.pack(">I", len(mj)) + mj + dump


def _parse_inner(inner: bytes) -> tuple[dict[str, Any], bytes]:
    if len(inner) < 4:
        raise BackupPackageError("Backup payload too small")
    (mlen,) = struct.unpack(">I", inner[:4])
    if mlen > MAX_MANIFEST_JSON or 4 + mlen > len(inner):
        raise BackupPackageError("Invalid manifest length")
    raw_m = inner[4 : 4 + mlen]
    dump = inner[4 + mlen :]
    if not dump.startswith(b"PGDMP"):
        raise BackupPackageError("Backup does not contain a valid PostgreSQL custom-format dump")
    try:
        manifest = json.loads(raw_m.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BackupPackageError("Invalid manifest JSON") from e
    if not isinstance(manifest, dict):
        raise BackupPackageError("Invalid manifest")
    return manifest, dump


def _reject_legacy_magic(raw: bytes) -> None:
    if len(raw) >= len(LEGACY_MAGIC) and raw[: len(LEGACY_MAGIC)] == LEGACY_MAGIC:
        raise BackupPackageError(
            "This backup uses an older file format. Export a new .qbk from Settings → Backup "
            "on a current Quickly instance."
        )


def pack_backup(
    manifest: dict[str, Any],
    dump: bytes,
    *,
    encrypt: bool,
    password: str | None = None,
    password_hint: str = "",
) -> bytes:
    manifest = dict(manifest)
    manifest["format_version"] = VERSION
    manifest["encrypted"] = encrypt
    if not dump.startswith(b"PGDMP"):
        raise BackupPackageError("Internal error: expected pg_dump custom-format data")
    hint = (password_hint or "").strip().encode("utf-8")
    if len(hint) > MAX_HINT_LEN:
        raise BackupPackageError(f"Password hint must be at most {MAX_HINT_LEN} characters")

    inner = _inner_blob(manifest, dump)
    out = bytearray(MAGIC)
    if encrypt:
        if not password or len(password) < MIN_PASSWORD_LEN:
            raise BackupPackageError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
        out.append(FLAG_ENCRYPTED)
        out.extend(struct.pack(">I", len(hint)))
        out.extend(hint)
        preview = build_preview_manifest(manifest)
        prev_bytes = json.dumps(preview, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(prev_bytes) > MAX_MANIFEST_JSON:
            raise BackupPackageError("Preview manifest too large")
        out.extend(struct.pack(">I", len(prev_bytes)))
        out.extend(prev_bytes)
        salt = os.urandom(_SALT_LEN)
        nonce = os.urandom(_NONCE_LEN)
        key = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=_PBKDF2_ITERATIONS,
        ).derive(password.encode("utf-8"))
        aes = AESGCM(key)
        aad = bytes(out)
        ct = aes.encrypt(nonce, inner, aad)
        out.extend(salt)
        out.extend(nonce)
        out.extend(ct)
    else:
        out.append(FLAG_PLAIN)
        out.extend(struct.pack(">I", len(hint)))
        out.extend(hint)
        out.extend(inner)
    return bytes(out)


def _header_after_hint(raw: bytes) -> tuple[int, int, str]:
    """Return (flag, position after hint bytes, hint str)."""
    if len(raw) < len(MAGIC) + 1 + 4:
        raise BackupPackageError("File too small to be a Quickly backup")
    _reject_legacy_magic(raw)
    if raw[: len(MAGIC)] != MAGIC:
        raise BackupPackageError(
            "Not a Quickly backup file (.qbk). Download a new backup from Settings → Backup."
        )
    pos = len(MAGIC)
    flag = raw[pos]
    pos += 1
    if pos + 4 > len(raw):
        raise BackupPackageError("Truncated backup header")
    (hint_len,) = struct.unpack(">I", raw[pos : pos + 4])
    pos += 4
    if hint_len > MAX_HINT_LEN or pos + hint_len > len(raw):
        raise BackupPackageError("Invalid backup header")
    hint_b = raw[pos : pos + hint_len]
    pos += hint_len
    try:
        hint_s = hint_b.decode("utf-8")
    except UnicodeDecodeError as e:
        raise BackupPackageError("Invalid password hint encoding") from e
    return flag, pos, hint_s


def read_backup_metadata(raw: bytes) -> tuple[dict[str, Any], bool, str]:
    """
    Read backup summary without decrypting.
    For encrypted files, returns the plaintext preview manifest (masked admin emails).
    """
    flag, pos, hint_s = _header_after_hint(raw)
    if flag == FLAG_PLAIN:
        inner = raw[pos:]
        manifest, _dump = _parse_inner(inner)
        return manifest, False, hint_s
    if flag == FLAG_ENCRYPTED:
        if pos + 4 > len(raw):
            raise BackupPackageError("Truncated encrypted backup")
        (preview_len,) = struct.unpack(">I", raw[pos : pos + 4])
        pos += 4
        if preview_len > MAX_MANIFEST_JSON or pos + preview_len > len(raw):
            raise BackupPackageError("Invalid preview length")
        preview_b = raw[pos : pos + preview_len]
        try:
            preview = json.loads(preview_b.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise BackupPackageError("Invalid preview manifest") from e
        if not isinstance(preview, dict):
            raise BackupPackageError("Invalid preview manifest")
        return preview, True, hint_s
    raise BackupPackageError("Unknown backup format")


def unpack_backup(raw: bytes, password: str | None = None) -> tuple[dict[str, Any], bytes]:
    flag, pos, _hint_s = _header_after_hint(raw)

    if flag == FLAG_PLAIN:
        inner = raw[pos:]
        return _parse_inner(inner)

    if flag == FLAG_ENCRYPTED:
        if pos + 4 > len(raw):
            raise BackupPackageError("Truncated encrypted backup")
        (preview_len,) = struct.unpack(">I", raw[pos : pos + 4])
        pos += 4
        if preview_len > MAX_MANIFEST_JSON or pos + preview_len > len(raw):
            raise BackupPackageError("Invalid preview length")
        pos += preview_len
        if not password:
            raise BackupPackageError("This backup is password-protected")
        need = _SALT_LEN + _NONCE_LEN
        if pos + need > len(raw):
            raise BackupPackageError("Truncated encrypted backup")
        aad_end = pos
        salt = raw[pos : pos + _SALT_LEN]
        pos += _SALT_LEN
        nonce = raw[pos : pos + _NONCE_LEN]
        pos += _NONCE_LEN
        ct = raw[pos:]
        if len(ct) < 16:
            raise BackupPackageError("Truncated ciphertext")
        key = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=_PBKDF2_ITERATIONS,
        ).derive(password.encode("utf-8"))
        aes = AESGCM(key)
        aad = raw[:aad_end]
        try:
            inner = aes.decrypt(nonce, ct, aad)
        except Exception as e:
            raise BackupPackageError("Wrong password or corrupted backup") from e
        return _parse_inner(inner)

    raise BackupPackageError("Unknown backup format version")


def read_password_hint(raw: bytes) -> str | None:
    """Return hint from file header without decrypting; None if not a current Quickly backup."""
    try:
        _flag, _pos, hint_s = _header_after_hint(raw)
        return hint_s or None
    except BackupPackageError:
        return None
