"""Quickly .qbk backup wrapper format."""
from __future__ import annotations

import pytest

from app.backup_package import (
    BackupPackageError,
    MIN_PASSWORD_LEN,
    build_manifest_dict,
    mask_email_for_preview,
    pack_backup,
    read_backup_metadata,
    read_password_hint,
    unpack_backup,
)


def _minimal_dump() -> bytes:
    return b"PGDMP" + b"\x00" * 80


def _sample_manifest() -> dict:
    return build_manifest_dict(
        lead_count=3,
        inbox_count=1,
        campaign_count=2,
        user_count=1,
        admin_emails=["alice@example.com", "bob@test.org"],
        encrypted=False,
    )


def test_mask_email_for_preview():
    assert "@" in mask_email_for_preview("alice@example.com")
    assert "alice" not in mask_email_for_preview("alice@example.com")
    assert mask_email_for_preview("no-at") == "***"


def test_pack_unpack_plain_roundtrip():
    dump = _minimal_dump()
    manifest = _sample_manifest()
    raw = pack_backup(manifest, dump, encrypt=False, password_hint="not secret")
    assert read_password_hint(raw) == "not secret"
    meta, enc, hint = read_backup_metadata(raw)
    assert enc is False
    assert hint == "not secret"
    assert meta["lead_count"] == 3
    assert meta["admin_emails"] == ["alice@example.com", "bob@test.org"]
    m2, d2 = unpack_backup(raw)
    assert d2 == dump
    assert m2["lead_count"] == 3
    assert m2["admin_emails"] == ["alice@example.com", "bob@test.org"]


def test_pack_unpack_encrypted_roundtrip_and_metadata():
    dump = _minimal_dump()
    manifest = _sample_manifest()
    pw = "a" * MIN_PASSWORD_LEN
    raw = pack_backup(manifest, dump, encrypt=True, password=pw, password_hint="hint")
    assert read_password_hint(raw) == "hint"
    meta, enc, hint = read_backup_metadata(raw)
    assert enc is True
    assert hint == "hint"
    assert meta["lead_count"] == 3
    assert "@" in (meta["admin_emails"][0] or "")
    assert "alice" not in "".join(meta["admin_emails"]).lower()
    with pytest.raises(BackupPackageError):
        unpack_backup(raw, password=None)
    with pytest.raises(BackupPackageError):
        unpack_backup(raw, password="wrong" * 5)
    m2, d2 = unpack_backup(raw, password=pw)
    assert d2 == dump
    assert m2["encrypted"] is True
    assert m2["admin_emails"] == ["alice@example.com", "bob@test.org"]


def test_rejects_non_quickly_file():
    with pytest.raises(BackupPackageError, match="Not a Quickly backup"):
        unpack_backup(b"PGDMP" + b"x" * 20)


def test_rejects_legacy_magic():
    legacy = b"QUICKLYBK\x01" + b"\x00" * 40
    with pytest.raises(BackupPackageError, match="older file format"):
        read_backup_metadata(legacy)
