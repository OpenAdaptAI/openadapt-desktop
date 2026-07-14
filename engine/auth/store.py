"""Shared keychain credential store -- the single source of truth for auth.

Both providers (:mod:`engine.auth.paste`, :mod:`engine.auth.browser_pkce`)
write the SAME :class:`~engine.auth.provider.Credential` here, and every
outbound hosted call (``push``, the needs-attention count poll, the
ingest-report emitter) reads the active bearer token through
:func:`auth_header`.

Secrets go to the OS secure store (macOS Keychain / Windows Credential Manager
/ Linux Secret Service) via ``keyring`` -- NEVER to ``config.toml`` or any
plaintext file (spec section 3e). The keyring service name is
``ai.openadapt.desktop`` and the account is the credential's ``host``.

A tiny non-secret pointer records which host is "active" so the no-arg
:func:`auth_header` can resolve a token without being told the host. That
pointer is stored in the keyring too (account ``__active_host__``), so it
inherits the same secure lifecycle and clean-uninstall purge.

Token resolution precedence for :func:`auth_header` (spec section 3e):
    ``OPENADAPT_INGEST_TOKEN`` env  ->  keychain (active host)  ->  {} (no auth)
"""

from __future__ import annotations

import json
import os

from loguru import logger

from engine.auth.provider import Credential

SERVICE_NAME = "ai.openadapt.desktop"
_ACTIVE_HOST_ACCOUNT = "__active_host__"

# Default hosted control-plane base URL. Overridable per-call and via config.
DEFAULT_HOST = "https://app.openadapt.ai"

# Env var that lets the headless / CI / BYOC-server path supply an ingest token
# with zero interaction and zero keychain access.
INGEST_TOKEN_ENV = "OPENADAPT_INGEST_TOKEN"


def _keyring():
    """Return the keyring module, or None if no usable backend is present.

    Isolated so callers degrade gracefully on headless boxes without a Secret
    Service (rather than crashing), and so tests can inject a fake backend.
    """
    try:
        import keyring

        return keyring
    except Exception:  # pragma: no cover - keyring is a hard dep, defensive only
        logger.warning("keyring unavailable; hosted credentials cannot be persisted")
        return None


def store_credential(c: Credential) -> None:
    """Persist a credential and mark its host active.

    Args:
        c: The credential to store. ``c["host"]`` is the keyring account.
    """
    kr = _keyring()
    if kr is None:
        raise RuntimeError("keyring backend unavailable; cannot store credential")
    host = c["host"]
    kr.set_password(SERVICE_NAME, host, json.dumps(c))
    kr.set_password(SERVICE_NAME, _ACTIVE_HOST_ACCOUNT, host)


def load_credential(host: str) -> Credential | None:
    """Load a stored credential for ``host``, or None if absent/unreadable."""
    kr = _keyring()
    if kr is None:
        return None
    raw = kr.get_password(SERVICE_NAME, host)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("stored credential for {host} is corrupt; ignoring", host=host)
        return None
    return data  # type: ignore[return-value]


def clear_credential(host: str) -> None:
    """Delete the stored credential for ``host`` (used by logout / uninstall purge)."""
    kr = _keyring()
    if kr is None:
        return
    for account in (host, _ACTIVE_HOST_ACCOUNT):
        try:
            existing = kr.get_password(SERVICE_NAME, account)
            if account == _ACTIVE_HOST_ACCOUNT and existing != host:
                continue
            kr.delete_password(SERVICE_NAME, account)
        except Exception:
            # keyring raises PasswordDeleteError when the entry is already gone.
            pass


def active_host() -> str | None:
    """Return the host most recently stored, or None."""
    kr = _keyring()
    if kr is None:
        return None
    return kr.get_password(SERVICE_NAME, _ACTIVE_HOST_ACCOUNT)


def active_credential() -> Credential | None:
    """Return the credential for the active host, or None."""
    host = active_host()
    if not host:
        return None
    return load_credential(host)


def auth_header() -> dict[str, str]:
    """Resolve the active bearer token to an HTTP ``Authorization`` header.

    Resolution order (spec section 3e): ``OPENADAPT_INGEST_TOKEN`` env, then
    the active host's stored credential. Returns an empty dict when no
    credential is available so callers can decide how to handle "unauthed".

    Returns:
        ``{"Authorization": "Bearer <token>"}`` or ``{}``.
    """
    env_token = os.environ.get(INGEST_TOKEN_ENV, "").strip()
    if env_token:
        return {"Authorization": f"Bearer {env_token}"}

    cred = active_credential()
    if cred and cred.get("token"):
        return {"Authorization": f"Bearer {cred['token']}"}

    return {}
