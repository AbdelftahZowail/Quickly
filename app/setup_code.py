"""One-time setup code for the first-boot admin setup.

The code is generated in memory the first time it is needed (zero users),
printed to the logs, and required to create the first admin account or to
restore a backup before setup.  It is intentionally **not** persisted: it is
invalidated after a successful setup, rotated on every restart, and can be
overridden with the ``QUICKLY_SETUP_CODE`` environment variable.

No database imports here — the gates live in the routers that know whether
setup is complete.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets

log = logging.getLogger("quickly.setup")

# Unambiguous alphabet: no 0/O, 1/I/L to make the code easy to read and type.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
ENV_VAR = "QUICKLY_SETUP_CODE"

# Module-level process state.  ``_code`` is minted lazily (first boot / first
# login page load), ``_logged`` guards the once-per-process log line, and
# ``_cleared`` makes a consumed code unusable even if the environment
# variable is still set.
_code: str | None = None
_logged = False
_cleared = False


def _normalize(value: str | None) -> str:
    """Uppercase and strip everything but ASCII letters/digits."""
    return "".join(
        ch for ch in (value or "").upper() if ch.isascii() and ch.isalnum()
    )


def _format(code: str) -> str:
    """Display an 8-character code as ``XXXX-XXXX`` (best effort)."""
    if len(code) == CODE_LENGTH:
        return f"{code[:4]}-{code[4:]}"
    return code


def _generate() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def _env_code() -> str | None:
    normalized = _normalize(os.getenv(ENV_VAR, ""))
    return normalized or None


def get_code() -> str | None:
    """Return the active setup code, or ``None`` once it has been consumed."""
    global _code
    if _cleared:
        return None
    env_code = _env_code()
    if env_code:
        return env_code
    if _code is None:
        _code = _generate()
    return _code


def log_setup_code() -> None:
    """Log the setup code once per process (never the env-provided value)."""
    global _logged
    if _logged or _cleared:
        return
    code = get_code()
    if not code:
        return
    _logged = True
    if _env_code():
        log.info("[quickly.setup] Setup code set via %s", ENV_VAR)
    else:
        log.info("[quickly.setup] Setup code: %s", _format(code))


def verify_setup_code(code: str | None) -> bool:
    """Constant-time check of a user-supplied code against the active one."""
    if _cleared:
        return False
    expected = get_code()
    candidate = _normalize(code)
    if not expected or not candidate:
        return False
    return hmac.compare_digest(candidate, expected)


def clear_setup_code() -> None:
    """Invalidate the code after setup (or a successful pre-setup restore)."""
    global _code, _cleared
    _code = None
    _cleared = True


def reset_for_tests() -> None:
    """Reset all module state so tests can exercise generation/logging."""
    global _code, _logged, _cleared
    _code = None
    _logged = False
    _cleared = False
