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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from engine.targets import ExecutionTarget

# This is an explicit consumer capability declaration, not a hint.  Desktop
# writes it only after the launched Flow version supports the matching task
# schema.  Flow then emits V2 only for a sealed qualification that supplies a
# label for the exact paused step.
HUMAN_DECISION_TASK_V1_SCHEMA = "openadapt.human-decision-task/v1"
HUMAN_DECISION_TASK_V2_SCHEMA = "openadapt.human-decision-task/v2"

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

_BACKEND_FIELDS_BY_KIND: dict[str, set[str]] = {
    "web": {"url", "headed"},
    "windows": {"agent_url", "agent_token", "agent_tls_pin"},
    "macos": {"macos_app", "macos_window_title"},
    "linux": {
        "linux_app",
        "linux_window_title",
        "linux_allow_physical_input",
    },
    "rdp": {
        "rdp_host",
        "rdp_username",
        "rdp_password",
        "rdp_domain",
        "rdp_port",
        "rdp_max_frame_age_s",
        "rdp_readiness_text",
        "rdp_readiness_min_ratio",
        "rdp_window",
        "rdp_window_title",
    },
    "citrix": {
        "rdp_max_frame_age_s",
        "rdp_readiness_text",
        "rdp_readiness_min_ratio",
        "rdp_window",
        "rdp_window_title",
    },
}
_ALL_BACKEND_FIELDS = set().union(*_BACKEND_FIELDS_BY_KIND.values())
_RDP_NETWORK_FIELDS = {
    "rdp_host",
    "rdp_username",
    "rdp_password",
    "rdp_domain",
    "rdp_port",
}
_RDP_WINDOW_FIELDS = {"rdp_window", "rdp_window_title"}


class PrivateFlowConfigError(ValueError):
    """Raised before execution when a deployment config cannot be staged."""


@dataclass(frozen=True)
class PreparedPrivateYaml:
    """One immutable serialization and its same-snapshot log redactions.

    ``remote_decisions`` is read from the SAME snapshot as ``payload``, not by
    re-opening the operator's file. Deciding "does this deployment answer halts
    on a phone?" from a second read would reintroduce exactly the TOCTOU gap
    this class exists to close: Flow could be launched with the outbound lane on
    while executing a config that never enabled it.
    """

    payload: str
    redactions: tuple[str, ...]
    remote_decisions: bool = False
    remote_decision_runner_id: str | None = None

    def with_remote_task_schemas(
        self, schemas: tuple[str, ...]
    ) -> "PreparedPrivateYaml":
        """Advertise this Desktop build's remote task schemas from this snapshot.

        The method reparses only the immutable in-memory payload.  It never
        rereads the operator file, so the launch config, the decision switch,
        and the peer capability declaration remain one source snapshot.

        Empty schemas deliberately remove a stale declaration.  This keeps an
        older embedded Flow on the V1 task contract even when a deployment file
        was previously used by a newer Desktop build.
        """

        if not self.remote_decisions:
            return self
        try:
            deployment = yaml.safe_load(self.payload) or {}
        except (UnicodeError, yaml.YAMLError) as exc:  # pragma: no cover - sealed payload
            raise PrivateFlowConfigError("Prepared deployment config could not be read") from exc
        if not isinstance(deployment, Mapping):  # pragma: no cover - sealed payload
            raise PrivateFlowConfigError("Prepared deployment config must contain an object")
        normalized = tuple(dict.fromkeys(schemas))
        if any(
            schema not in {HUMAN_DECISION_TASK_V1_SCHEMA, HUMAN_DECISION_TASK_V2_SCHEMA}
            for schema in normalized
        ):
            raise PrivateFlowConfigError("Desktop does not support one remote decision task schema")

        updated = dict(deployment)
        human_decisions = updated.get("human_decisions")
        remote = (
            dict(human_decisions.get("remote") or {})
            if isinstance(human_decisions, Mapping)
            else {}
        )
        if normalized:
            remote["peer_task_schemas"] = list(normalized)
        else:
            remote.pop("peer_task_schemas", None)
        updated["human_decisions"] = {
            **(dict(human_decisions) if isinstance(human_decisions, Mapping) else {}),
            "remote": remote,
        }
        return PreparedPrivateYaml(
            payload=yaml.safe_dump(updated, sort_keys=False),
            redactions=_redactions_for_mapping(updated),
            remote_decisions=self.remote_decisions,
            remote_decision_runner_id=self.remote_decision_runner_id,
        )


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

    # A direct Desktop selection is authoritative. Retain advanced settings
    # only for that selected substrate and discard stale fields from every
    # other backend before applying the direct target. This prevents a prior
    # RDP/network value (or native selector) from silently selecting the wrong
    # transport after the operator switches targets.
    allowed = _BACKEND_FIELDS_BY_KIND[target.backend]
    backend = {
        key: value
        for key, value in dict(existing_backend).items()
        if key not in _ALL_BACKEND_FIELDS or key in allowed
    }
    backend.update(target.deployment_overrides())

    if target.backend == "rdp":
        explicit = target.model_fields_set
        selected_network = "rdp_host" in explicit
        selected_window = "rdp_window" in explicit
        if selected_network and selected_window:
            raise PrivateFlowConfigError(
                "Selected RDP target declares both network-host and local-window "
                "transports; choose exactly one"
            )
        if selected_network:
            for key in _RDP_WINDOW_FIELDS:
                backend.pop(key, None)
        elif selected_window:
            for key in _RDP_NETWORK_FIELDS:
                backend.pop(key, None)
        elif backend.get("rdp_host") and (
            backend.get("rdp_window") or backend.get("rdp_window_title")
        ):
            raise PrivateFlowConfigError(
                "Selected RDP deployment contains both network-host and local-window "
                "transports; choose exactly one"
            )
        if not backend.get("rdp_host") and not backend.get("rdp_window"):
            raise PrivateFlowConfigError(
                "Selected RDP deployment requires a network host or local client window"
            )
    elif target.backend == "citrix":
        for key in _RDP_NETWORK_FIELDS:
            backend.pop(key, None)

    deployment["backend"] = backend
    return deployment


def _redactions_for_mapping(
    deployment: Mapping[str, Any],
    *,
    extra_sensitive_keys: set[str] | None = None,
) -> tuple[str, ...]:
    """Collect exact sensitive strings from one already-parsed mapping."""

    values: set[str] = set()
    extra = extra_sensitive_keys or set()

    def collect(value: object, key: str = "") -> None:
        normalized = key.lower()
        sensitive = (
            normalized in _PHI_CAPABLE_BACKEND_KEYS
            or normalized in extra
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


def prepare_flow_config(
    source: Path | None,
    target: ExecutionTarget | None,
) -> PreparedPrivateYaml | None:
    """Parse, merge, serialize, and derive redactions from one snapshot.

    Reading the operator file twice creates a TOCTOU gap where Flow could
    receive values different from those selected for redaction. The prepared
    object contains only an immutable serialization and redaction tuple, so
    staging never reopens the source file.
    """

    if source is None and target is None:
        return None
    deployment = _merged_config(source, target)
    remote_decisions, remote_decision_runner_id = _remote_decision_settings(deployment)
    return PreparedPrivateYaml(
        payload=yaml.safe_dump(deployment, sort_keys=False),
        redactions=_redactions_for_mapping(deployment),
        remote_decisions=remote_decisions,
        remote_decision_runner_id=remote_decision_runner_id,
    )


def _remote_decisions_enabled(deployment: Mapping[str, Any]) -> bool:
    """Whether this deployment answers halts through the hosted lane.

    Strictly ``True``: a truthy string, a 1, or a missing section all mean "no".
    Turning on an outbound lane that carries decision context is not a default
    and is not inferred.
    """

    return _remote_decision_settings(deployment)[0]


def _remote_decision_settings(
    deployment: Mapping[str, Any],
) -> tuple[bool, str | None]:
    """Return the remote-decision switch and runner from one config snapshot."""

    human_decisions = deployment.get("human_decisions")
    if not isinstance(human_decisions, Mapping):
        return False, None
    remote = human_decisions.get("remote")
    if not isinstance(remote, Mapping):
        return False, None
    runner_id = remote.get("runner_id")
    normalized_runner_id = (
        runner_id.strip() if isinstance(runner_id, str) and runner_id.strip() else None
    )
    return remote.get("enabled") is True, normalized_runner_id


def prepare_flow_record_request(
    *,
    target: ExecutionTarget,
    out_dir: Path,
    task: str,
    stop_path: Path,
    ready_path: Path,
) -> PreparedPrivateYaml:
    """Prepare the private input consumed by the canonical Flow record helper."""

    request = {
        "target": target.model_dump(
            mode="json",
            exclude_none=True,
            exclude_unset=True,
        ),
        "out_dir": str(out_dir),
        "task": task,
        "stop_path": str(stop_path),
        "ready_path": str(ready_path),
    }
    return PreparedPrivateYaml(
        payload=yaml.safe_dump(request, sort_keys=False),
        redactions=_redactions_for_mapping(
            request,
            extra_sensitive_keys={"task"},
        ),
    )


def flow_log_redactions(
    source: Path | None,
    target: ExecutionTarget | None,
) -> tuple[str, ...]:
    """Compatibility helper derived from a single prepared snapshot."""

    prepared = prepare_flow_config(source, target)
    return prepared.redactions if prepared is not None else ()


def redact_flow_log(text: str, redactions: tuple[str, ...]) -> str:
    """Replace exact sensitive values while preserving structural diagnostics."""

    for value in redactions:
        text = text.replace(value, "[REDACTED]")
    return text


@contextmanager
def stage_private_yaml(
    directory: Path,
    *,
    prepared: PreparedPrivateYaml,
    prefix: str = ".deployment-",
) -> Iterator[Path]:
    """Stage one prepared serialization privately without rereading a source."""

    directory.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(
        prefix=prefix,
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
            handle.write(prepared.payload)
            handle.flush()
            os.fsync(handle.fileno())
        yield path
    finally:
        if fd >= 0:
            os.close(fd)
        path.unlink(missing_ok=True)


@contextmanager
def private_flow_config(
    directory: Path,
    *,
    source: Path | None = None,
    target: ExecutionTarget | None = None,
) -> Iterator[Path | None]:
    """Yield one short-lived staged snapshot, then remove it."""

    prepared = prepare_flow_config(source, target)
    if prepared is None:
        yield None
        return
    with stage_private_yaml(directory, prepared=prepared) as path:
        yield path
