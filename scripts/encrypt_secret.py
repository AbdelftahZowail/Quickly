#!/usr/bin/env python3
"""Encrypt (or decrypt) a secret with Quickly's ``QUICKLY_ENCRYPTION_KEY``.

The ``smtp_account.smtp_password`` / ``imap_password`` columns are stored as
Fernet ciphertext — see ``app.security.EncryptedText``.  Normally you set them
through the UI or the ``/api/smtp`` endpoints; this helper exists for headless
setups and for repairing credentials directly in the database (issue #4:
"smtp_password encryption path is unclear").

Key resolution order:

  1. ``--key <passphrase-or-fernet-key>``
  2. ``QUICKLY_ENCRYPTION_KEY`` environment variable
  3. ``--key-from-db``: read ``quickly_encryption_key`` from the ``app_setting``
     table (the key the app auto-generates and stores when the env var is unset)
  4. interactive hidden prompt

Examples::

    # Encrypt a password using the key the app stored in the database
    python scripts/encrypt_secret.py --key-from-db

    # Print a ready-to-run SQL statement for inbox_id 7
    python scripts/encrypt_secret.py --key-from-db --inbox-id 7 --sql

    # Same, but target the IMAP password column instead of smtp_password
    python scripts/encrypt_secret.py --key-from-db --inbox-id 7 --column imap_password --sql

    # Non-interactive (password is visible in shell history — prefer the prompt)
    python scripts/encrypt_secret.py --key-from-db --password 'my-smtp-password'

    # Verify an existing ciphertext
    python scripts/encrypt_secret.py --key-from-db --decrypt 'gAAAAA...'
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _key_from_db() -> str:
    """Read ``app_setting.quickly_encryption_key`` from the configured database."""
    try:
        import psycopg2  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "psycopg2 is required for --key-from-db (pip install psycopg2-binary)"
        ) from exc

    dsn = (os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL") or "").strip()
    if not dsn:
        raise SystemExit("DATABASE_URL is not set — cannot read the key from the database")
    # SQLAlchemy-style URLs are not valid libpq DSNs.
    dsn = dsn.replace("+asyncpg", "").replace("+psycopg2", "")
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql://", 1)

    try:
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT value FROM app_setting WHERE key = %s",
                ("quickly_encryption_key",),
            )
            row = cur.fetchone()
    except Exception as exc:  # pragma: no cover - depends on live DB
        raise SystemExit(f"Could not read quickly_encryption_key from the database: {exc}") from exc

    if not row or not (row[0] or "").strip():
        raise SystemExit(
            "No quickly_encryption_key found in app_setting. Set QUICKLY_ENCRYPTION_KEY "
            "or pass --key instead."
        )
    return str(row[0]).strip()


def _resolve_key(args: argparse.Namespace) -> str:
    if args.key:
        return args.key.strip()
    env_key = (os.getenv("QUICKLY_ENCRYPTION_KEY") or "").strip()
    if env_key:
        return env_key
    if args.key_from_db:
        return _key_from_db()
    if sys.stdin.isatty():
        return getpass.getpass("QUICKLY_ENCRYPTION_KEY (hidden): ").strip()
    raise SystemExit(
        "No encryption key available. Pass --key, set QUICKLY_ENCRYPTION_KEY, "
        "or use --key-from-db."
    )


def _read_secret(args: argparse.Namespace, prompt: str) -> str:
    if args.password is not None:
        return args.password
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return getpass.getpass(prompt).strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Encrypt/decrypt a Quickly secret (SMTP/IMAP password) with the app's Fernet key.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "value",
        nargs="?",
        help="Plaintext to encrypt, or ciphertext with --decrypt (omit to be prompted).",
    )
    parser.add_argument("--key", help="Encryption key / passphrase (defaults to QUICKLY_ENCRYPTION_KEY).")
    parser.add_argument(
        "--key-from-db",
        action="store_true",
        help="Read quickly_encryption_key from the app_setting table (requires DATABASE_URL).",
    )
    parser.add_argument("--password", help="Secret to encrypt (non-interactive; visible in shell history).")
    parser.add_argument("--decrypt", action="store_true", help="Decrypt the given ciphertext instead.")
    parser.add_argument("--inbox-id", type=int, help="With --sql: the inbox whose SMTP row to update.")
    parser.add_argument(
        "--column",
        choices=("smtp_password", "imap_password"),
        default="smtp_password",
        help="With --sql: which credentials column to update (default: smtp_password).",
    )
    parser.add_argument(
        "--sql",
        action="store_true",
        help="Print an UPDATE smtp_account statement instead of only the ciphertext.",
    )
    args = parser.parse_args()

    key = _resolve_key(args)

    # Import after the key is resolved: app.security initialises Fernet at
    # import time from the environment, so set the resolved key first (avoids
    # the misleading "stored as PLAINTEXT" warning) and re-init explicitly.
    os.environ["QUICKLY_ENCRYPTION_KEY"] = key
    try:
        from app.security import decrypt, encrypt, init_encryption
    except Exception as exc:  # pragma: no cover - environment dependent
        raise SystemExit(f"Could not import app.security: {exc}") from exc

    init_encryption(key)

    if args.decrypt:
        ciphertext = (args.value or _read_secret(args, "Ciphertext: ")).strip()
        if not ciphertext:
            raise SystemExit("No ciphertext provided")
        plaintext = decrypt(ciphertext)
        if plaintext == ciphertext:
            print(
                "WARNING: decryption failed — the value was returned unchanged "
                "(wrong key, or it was stored as plaintext).",
                file=sys.stderr,
            )
            return 1
        print(plaintext)
        return 0

    plaintext = (args.value if args.value is not None else _read_secret(args, "Secret to encrypt: ")).strip()
    if not plaintext:
        raise SystemExit("No plaintext provided")
    ciphertext = encrypt(plaintext)

    if decrypt(ciphertext) != plaintext:
        raise SystemExit("Encryption round-trip failed — refusing to output an unusable value")

    if args.sql:
        if not args.inbox_id:
            raise SystemExit("--sql requires --inbox-id")
        print(
            "UPDATE smtp_account "
            f"SET {args.column} = '{ciphertext}', updated_at = NOW() "
            f"WHERE inbox_id = {args.inbox_id};"
        )
    else:
        print(ciphertext)
        print(
            "# Use the UI/API to store it, or add --inbox-id N --sql to print an UPDATE statement.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
