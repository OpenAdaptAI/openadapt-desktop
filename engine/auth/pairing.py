"""Narrow one-click Cloud pairing for the installed Desktop application.

Only ``openadapt://connect`` is accepted.  The URI is parsed again inside the
Python engine even though the Tauri shell validates it first, so neither IPC
nor an operating-system protocol invocation can become a general command,
browser-navigation, or arbitrary-network surface.
"""

from __future__ import annotations

import re
import socket
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx

from engine.auth.provider import Credential
from engine.auth.store import (
    DEFAULT_HOST,
    clear_credential,
    secure_store_available,
    store_credential_secure,
)

PAIRING_SECRET_RE = re.compile(r"^oap_[A-Za-z0-9_-]{43}$")
INGEST_TOKEN_RE = re.compile(r"^oai_ingest_[A-Za-z0-9_-]{32,}$")
ALLOWED_FIELDS = frozenset({"pairing", "host", "destination_kind"})
ALLOWED_DESTINATIONS = frozenset({"openadapt-managed", "local"})
API_TIMEOUT_S = 8.0


class PairingError(RuntimeError):
    """A safe, user-facing pairing failure with no secret-bearing text."""


def _origin(raw: str) -> str:
    parsed = urlparse(str(raw).strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise PairingError("Connect link contains an invalid Cloud origin")
    if parsed.hostname.endswith("."):
        raise PairingError("Connect link contains an invalid Cloud origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise PairingError("Connect link contains an invalid Cloud origin") from exc
    host = parsed.hostname.lower()
    authority = f"[{host}]" if ":" in host else host
    if port is not None and (parsed.scheme, port) not in {
        ("http", 80),
        ("https", 443),
    }:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme}://{authority}"


def _validate_destination(host: str, destination_kind: str | None) -> str:
    origin = _origin(host)
    managed_origin = _origin(DEFAULT_HOST)
    kind = destination_kind or (
        "openadapt-managed" if origin == managed_origin else None
    )
    if kind not in ALLOWED_DESTINATIONS:
        raise PairingError("Connect link has an unsupported destination")
    if kind == "openadapt-managed":
        if origin != managed_origin:
            raise PairingError("Connect link does not name the managed OpenAdapt service")
        return origin

    hostname = urlparse(origin).hostname
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise PairingError("A local connect link must use this computer")
    return origin


def parse_connect_uri(uri: str) -> dict[str, str]:
    """Parse the fixed connect action and reject ambiguity or extra fields."""
    if not isinstance(uri, str) or not uri or len(uri) > 2048:
        raise PairingError("Invalid OpenAdapt connect link")
    parsed = urlparse(uri)
    if (
        parsed.scheme != "openadapt"
        or parsed.netloc != "connect"
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise PairingError("Invalid OpenAdapt connect link")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise PairingError("Invalid OpenAdapt connect link") from exc
    if set(query) - ALLOWED_FIELDS or any(len(values) != 1 for values in query.values()):
        raise PairingError("Connect link contains unknown or duplicate fields")
    if set(query) < {"pairing", "host"}:
        raise PairingError("Connect link is missing pairing or host")

    secret = query["pairing"][0]
    if not PAIRING_SECRET_RE.fullmatch(secret):
        raise PairingError("Pairing code is malformed")
    destination_kind = query.get("destination_kind", [None])[0]
    if destination_kind is not None and destination_kind not in ALLOWED_DESTINATIONS:
        raise PairingError("Connect link has an unsupported destination kind")
    host = _validate_destination(query["host"][0], destination_kind)
    result = {"pairing": secret, "host": host}
    if destination_kind:
        result["destination_kind"] = destination_kind
    return result


def _safe_device_name() -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", "", socket.gethostname()).strip()[:80]
    return value or "this computer"


def connect_uri(uri: str) -> dict[str, Any]:
    """Claim, store, verify, and confirm one exact Desktop pairing URI."""
    request = parse_connect_uri(uri)
    host = request["host"]
    if not secure_store_available():
        raise PairingError(
            "Secure pairing needs an unlocked operating-system keychain. "
            "Unlock it, then create a new connection from Cloud settings."
        )

    device_name = _safe_device_name()
    try:
        claim = httpx.post(
            f"{host}/api/local-bridge/pairings/claim",
            json={"pairing_secret": request["pairing"], "device_name": device_name},
            timeout=API_TIMEOUT_S,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise PairingError("Could not reach OpenAdapt Cloud") from exc
    if claim.status_code == 410:
        raise PairingError("Pairing code expired, was cancelled, or was already used")
    if claim.status_code >= 400:
        raise PairingError(f"Pairing failed ({claim.status_code})")
    try:
        body = claim.json()
        token = str(body["ingest_token"])
        pairing_id = str(UUID(str(body["pairing_id"])))
    except (KeyError, TypeError, ValueError) as exc:
        raise PairingError("Pairing response did not contain a valid credential") from exc
    if not INGEST_TOKEN_RE.fullmatch(token):
        raise PairingError("Pairing response contained a malformed credential")

    credential: Credential = {
        "kind": "ingest_token",
        "token": token,
        "refresh_token": None,
        "org_id": None,
        "host": host,
        "expires_at": None,
    }
    if not store_credential_secure(credential):
        raise PairingError(
            "The pairing was claimed, but the operating-system keychain refused "
            "the credential. No plaintext copy was written. Revoke this connection "
            "in Cloud settings before trying again."
        )

    headers = {"Authorization": f"Bearer {token}"}
    try:
        validation = httpx.get(
            f"{host}/api/needs-attention/count",
            headers=headers,
            timeout=API_TIMEOUT_S,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise PairingError(
            "The credential is stored, but Cloud could not verify the connection. "
            "Revoke it in Cloud settings before trying again."
        ) from exc
    if validation.status_code == 401:
        clear_credential(host)
        raise PairingError(
            "Cloud rejected the paired credential, so Desktop removed it from the keychain"
        )
    if validation.status_code >= 400:
        raise PairingError(
            f"The credential is stored, but verification failed ({validation.status_code})"
        )

    try:
        confirmation = httpx.post(
            f"{host}/api/local-bridge/pairings/confirm",
            json={"pairing_id": pairing_id},
            headers=headers,
            timeout=API_TIMEOUT_S,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise PairingError(
            "The credential is stored and usable, but Cloud could not confirm the connection"
        ) from exc
    try:
        confirmed = confirmation.json().get("connected") is True
    except (TypeError, ValueError):
        confirmed = False
    if confirmation.status_code >= 400 or not confirmed:
        raise PairingError(
            f"The credential is usable, but confirmation failed ({confirmation.status_code})"
        )

    return {
        "authenticated": True,
        "kind": "ingest_token",
        "host": host,
        "org_id": None,
        "paired": True,
        "token_storage": "keyring",
        "device_name": device_name,
        "settings_url": f"{host}/dashboard/settings/ingest",
    }
