"""Typed execution-target contract shared by Desktop dispatch and FlowBridge.

``openadapt-flow`` owns backend construction.  Desktop only collects the small
set of non-secret CLI overrides that Flow exposes, then passes an optional
deployment config path for operator-managed settings (credentials, policy,
effect verification, and other advanced wiring).

The model is deliberately closed and backend-specific: stale fields from a
previous UI selection are refused rather than being silently forwarded to the
wrong substrate.  Secret-bearing Flow fields such as ``agent_token`` and
``rdp_password`` are not part of this IPC schema at all.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

TargetBackend = Literal["web", "windows", "macos", "linux", "rdp", "citrix"]

_TARGET_FIELDS = {
    "url",
    "agent_url",
    "macos_app",
    "macos_window_title",
    "linux_app",
    "linux_window_title",
    "linux_allow_physical_input",
    "rdp_host",
    "rdp_window",
    "rdp_window_title",
    "rdp_readiness_text",
}

_FIELDS_BY_BACKEND: dict[str, set[str]] = {
    "web": {"url"},
    "windows": {"agent_url"},
    "macos": {"macos_app", "macos_window_title"},
    "linux": {
        "linux_app",
        "linux_window_title",
        "linux_allow_physical_input",
    },
    "rdp": {
        "rdp_host",
        "rdp_window",
        "rdp_window_title",
        "rdp_readiness_text",
    },
    "citrix": {
        "rdp_window",
        "rdp_window_title",
        "rdp_readiness_text",
    },
}


class ExecutionTarget(BaseModel):
    """Non-secret Flow backend selection received over Desktop's local IPC."""

    model_config = ConfigDict(extra="forbid")

    backend: TargetBackend = "web"
    url: str | None = None
    agent_url: str | None = None
    macos_app: str | None = None
    macos_window_title: str | None = None
    linux_app: str | None = None
    linux_window_title: str | None = None
    linux_allow_physical_input: bool = False
    rdp_host: str | None = None
    rdp_window: str | None = None
    rdp_window_title: str | None = None
    rdp_readiness_text: str | None = None

    @field_validator(
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
        mode="before",
    )
    @classmethod
    def _strip_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def _reject_cross_backend_fields(self) -> "ExecutionTarget":
        relevant = _FIELDS_BY_BACKEND[self.backend]
        supplied: set[str] = set()
        for name in _TARGET_FIELDS:
            value = getattr(self, name)
            if value is True or (value is not None and value is not False):
                supplied.add(name)
        irrelevant = sorted(supplied - relevant)
        if irrelevant:
            raise ValueError(
                f"target fields do not apply to backend {self.backend!r}: "
                + ", ".join(irrelevant)
            )
        if self.backend == "rdp" and self.rdp_host and self.rdp_window:
            raise ValueError(
                "RDP target must select one connection: rdp_host (network) or "
                "rdp_window (local client window), not both"
            )
        return self

    def validate_required(self, *, deployment_config: bool) -> None:
        """Fail early when direct target fields cannot construct the backend.

        A supplied deployment config may provide any missing backend field, so
        Flow remains the authoritative validator for that path.  Citrix needs
        no direct owner because Flow defaults it by host OS.
        """

        if deployment_config:
            return
        required: tuple[str, ...]
        if self.backend == "windows":
            required = ("agent_url",)
        elif self.backend == "macos":
            required = ("macos_app",)
        elif self.backend == "linux":
            required = ("linux_app", "linux_window_title")
        else:
            required = ()
        missing = [name for name in required if not getattr(self, name)]
        if self.backend == "rdp" and not (self.rdp_host or self.rdp_window):
            missing.append("rdp_host or rdp_window")
        if missing:
            raise ValueError(
                f"{self.backend} target requires "
                + ", ".join(missing)
                + " (or select a deployment config that provides it)"
            )

    def flow_args(self) -> list[str]:
        """Render the exact public backend flags supported by Flow 1.20+."""

        args = ["--backend", self.backend]
        values: tuple[tuple[str, str | None], ...]
        if self.backend == "web":
            values = (("--url", self.url),)
        elif self.backend == "windows":
            values = (("--agent-url", self.agent_url),)
        elif self.backend == "macos":
            values = (
                ("--macos-app", self.macos_app),
                ("--macos-window-title", self.macos_window_title),
            )
        elif self.backend == "linux":
            values = (
                ("--linux-app", self.linux_app),
                ("--linux-window-title", self.linux_window_title),
            )
        else:
            values = (
                ("--rdp-host", self.rdp_host),
                ("--rdp-window", self.rdp_window),
                ("--rdp-window-title", self.rdp_window_title),
                ("--rdp-readiness-text", self.rdp_readiness_text),
            )
        for flag, value in values:
            if value:
                args.extend((flag, value))
        if self.backend == "linux" and self.linux_allow_physical_input:
            args.append("--linux-allow-physical-input")
        return args
