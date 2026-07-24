"""Private staging for PHI-capable Flow deployment configuration.

Desktop target selectors (window owner/title/readiness text and endpoints) can
contain PHI or customer-identifying data.  Flow accepts the same values through
its deployment config, so Desktop merges direct target overrides over the
operator's YAML/JSON config and gives Flow only a private file path. POSIX uses
mode 0600; Windows uses its current-user profile/run-directory ACL boundary.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from engine.targets import ExecutionTarget

_PHI_CAPABLE_BACKEND_KEYS = {
    "url",
    "agent_url",
    "macos_app",
    "macos_window_title",
    "linux_app",
    "linux_window_title",
    "rdp_host",
    "rdp_window",
    "rdp_window_title",
    "rdp_readiness_text",
}


class PrivateFlowConfigError(ValueError):
    """Raised before execution when a deployment config cannot be staged."""


def _load_mapping(source: Path | None) -> dict[str, Any]:
    if source is None:
        return {}
    try:
        loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PrivateFlowConfigError(
            "Selected deployment config could not be read as YAML or JSON"
        ) from exc
    if not isinstance(loaded, Mapping):
        raise PrivateFlowConfigError("Selected deployment config must contain an object")
    return dict(loaded)


def _merged_config(
    source: Path | None,
    target: ExecutionTarget | None,
) -> dict[str, Any]:
    deployment = _load_mapping(source)
    if target is None:
        return deployment

    existing_backend = deployment.get("backend") or {}
    if not isinstance(existing_backend, Mapping):
        raise PrivateFlowConfigError("Selected deployment config backend must contain an object")
    deployment["backend"] = {
        **dict(existing_backend),
        **target.deployment_overrides(),
    }
    return deployment


def flow_log_redactions(
    source: Path | None,
    target: ExecutionTarget | None,
) -> tuple[str, ...]:
    """Return exact PHI-capable/secret values that must be removed from logs."""

    deployment = _merged_config(source, target)
    values: set[str] = set()

    def collect(value: object, key: str = "") -> None:
        normalized = key.lower()
        sensitive = (
            normalized in _PHI_CAPABLE_BACKEND_KEYS
            or "password" in normalized
            or "token" in normalized
            or "secret" in normalized
        )
        if sensitive and isinstance(value, str) and value:
            values.add(value)
        if isinstance(value, Mapping):
            for child_key, child_value in value.items():
                collect(child_value, str(child_key))
        elif isinstance(value, list):
            for child in value:
                collect(child, key)

    collect(deployment)
    return tuple(sorted(values, key=len, reverse=True))


def redact_flow_log(text: str, redactions: tuple[str, ...]) -> str:
    """Replace exact sensitive values while preserving structural diagnostics."""

    for value in redactions:
        text = text.replace(value, "[REDACTED]")
    return text


@contextmanager
def private_flow_config(
    directory: Path,
    *,
    source: Path | None = None,
    target: ExecutionTarget | None = None,
) -> Iterator[Path | None]:
    """Yield a short-lived private merged config, then remove it.

    If there is no source and no direct target, no file is needed and ``None``
    is yielded. POSIX files are mode 0600; Windows temp files inherit the
    current user's protected run-directory ACL. The caller must keep the context
    open until Flow exits.
    """

    if source is None and target is None:
        yield None
        return

    deployment = _merged_config(source, target)
    directory.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(
        prefix=".deployment-",
        suffix=".yaml",
        dir=directory,
        text=True,
    )
    path = Path(raw_path)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):  # pragma: no cover - Windows fallback
            os.chmod(path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            yaml.safe_dump(deployment, handle, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        yield path
    finally:
        if fd >= 0:
            os.close(fd)
        path.unlink(missing_ok=True)
