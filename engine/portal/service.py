"""Portal lifecycle: start, describe, pair, notify, and stop.

Desktop's half of the mobile attended-decision loop is a lifecycle, not a
decision.  :class:`PortalService` owns:

* resolving and validating ingress before anything binds
  (:mod:`engine.portal.ingress`);
* launching and supervising the ``openadapt-flow console --attend`` process
  that produces every task, evidence artifact, and decision outcome;
* running the phone-facing portal socket (:mod:`engine.portal.server`);
* minting and approving one-use QR device pairings
  (:mod:`engine.portal.pairing`);
* projecting a generic operating-system notification
  (:mod:`engine.portal.notifications`).

Everything downstream of "relay this request" belongs to ``openadapt-flow``.

Known seam, called out deliberately: the attended console generates its bearer
capability inside ``serve()`` and only prints it on stdout.  There is no
injection point today, so :func:`_parse_console_banner` reads the exact banner
line.  A narrow ``--capability-file`` option in Flow would replace this with a
supported interface; until then the parser is strict and fails loud rather than
guessing.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from engine.flow_bridge import FLOW_BIN, _flow_command, _subprocess_env
from engine.portal.flow_client import FlowConsoleClient, FlowConsoleUnavailable
from engine.portal.ingress import IngressError, PortalIngress, resolve_ingress
from engine.portal.notifications import (
    assert_generic_notification,
    build_notification,
    notification_from_upstream,
)
from engine.portal.pairing import DevicePairingStore, PairingRefused
from engine.portal.server import PortalApp, PortalServer

#: The exact banner ``openadapt_flow.console.server.serve`` prints.
_CONSOLE_BANNER = re.compile(
    r"^\s*http://(?:127\.0\.0\.1|localhost):(?P<port>\d{1,5})/#token=(?P<token>[A-Za-z0-9_-]{16,})\s*$"
)

#: How long to wait for the console to announce itself before failing loud.
CONSOLE_START_TIMEOUT_S = 60.0


class PortalError(RuntimeError):
    """The portal could not be started or is not in a state to serve."""


def _parse_console_banner(line: str) -> tuple[int, str] | None:
    match = _CONSOLE_BANNER.match(line)
    if match is None:
        return None
    return int(match.group("port")), match.group("token")


@dataclass
class ConsoleProcess:
    """A supervised attended-console subprocess."""

    process: Any
    port: int
    access_token: str

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        if not self.alive():
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=10)
        except Exception:  # pragma: no cover - defensive
            try:
                self.process.kill()
            except Exception:
                pass


class PortalService:
    """Owns the runner-local decision portal for one Desktop process.

    Args:
        config: Engine configuration supplying the ``portal_*`` fields.
        popen: Injected process spawner (tests supply a fake).
        clock: Injected monotonic clock shared with the pairing store.
    """

    def __init__(
        self,
        config: Any,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._popen = popen
        self._clock = clock
        self._lock = threading.Lock()
        self._ingress: PortalIngress | None = None
        self._server: PortalServer | None = None
        self._console: ConsoleProcess | None = None
        self._flow: FlowConsoleClient | None = None
        self._pairings: DevicePairingStore | None = None
        self._last_error: str | None = None

    # -------------------------------------------------------------- lifecycle

    @property
    def running(self) -> bool:
        return self._server is not None

    def runner_id(self) -> str:
        """A stable, non-secret local runner identifier for pairing binding."""
        import hashlib
        import socket

        raw = f"{socket.gethostname()}|{Path.home()}"
        return f"runner_{hashlib.sha256(raw.encode()).hexdigest()[:24]}"

    def start(self) -> dict[str, Any]:
        """Start the portal, or fail closed with an exact reason.

        Raises:
            PortalError: If ingress is not fully configured, the attended
                console cannot start, or the socket cannot bind.  The portal is
                left stopped; it never falls back to a wider bind address.
        """
        with self._lock:
            if self._server is not None:
                return self._status_locked()
            try:
                ingress = resolve_ingress(self.config)
            except IngressError as exc:
                self._last_error = str(exc)
                raise PortalError(str(exc)) from exc

            console = self._start_console()
            flow = FlowConsoleClient(
                port=console.port, access_token=console.access_token
            )
            try:
                session = flow.request("session")
                if isinstance(session.json, dict):
                    flow.csrf_token = str(session.json.get("csrf_token") or "")
            except FlowConsoleUnavailable as exc:
                console.stop()
                self._last_error = str(exc)
                raise PortalError(
                    "The local decision service started but did not answer. "
                    "Stop the portal and try again."
                ) from exc

            pairings = DevicePairingStore(
                runner_id=self.runner_id(), clock=self._clock
            )
            server = PortalServer(PortalApp(ingress, pairings, flow))
            try:
                port = server.start()
            except OSError as exc:
                console.stop()
                self._last_error = str(exc)
                raise PortalError(
                    f"The portal could not listen on {ingress.bind_host}:{ingress.port}."
                ) from exc

            if ingress.port == 0:
                # An ephemeral port only makes sense for the loopback default;
                # a published ingress must name the port it forwards to.
                ingress = PortalIngress(
                    mode=ingress.mode,
                    bind_host=ingress.bind_host,
                    port=port,
                    public_origin=(
                        f"http://{ingress.bind_host}:{port}"
                        if ingress.mode == "loopback"
                        else ingress.public_origin
                    ),
                    reachable_from_phone=ingress.reachable_from_phone,
                )
                server.app.ingress = ingress

            self._ingress = ingress
            self._console = console
            self._flow = flow
            self._pairings = pairings
            self._server = server
            self._last_error = None
            logger.info(
                "Decision portal listening on {h}:{p} ({m})",
                h=ingress.bind_host,
                p=port,
                m=ingress.mode,
            )
            return self._status_locked()

    def _start_console(self) -> ConsoleProcess:
        prefix = _flow_command(FLOW_BIN)
        if prefix is None:
            raise PortalError(
                "OpenAdapt Flow is not available, so there is nothing to decide "
                "about. Install openadapt-flow with its console extra."
            )
        command = [
            *prefix,
            "console",
            "--attend",
            "--allow-actions",
            "--bundles",
            str(Path(self.config.data_dir) / "bundles"),
            "--runs",
            str(Path(self.config.data_dir) / "runs"),
            "--port",
            str(int(getattr(self.config, "portal_console_port", 7863))),
        ]
        process = self._popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_subprocess_env(),
        )
        deadline = time.monotonic() + CONSOLE_START_TIMEOUT_S
        while time.monotonic() < deadline:
            if process.stdout is None:  # pragma: no cover - defensive
                break
            line = process.stdout.readline()
            if not line:
                break
            parsed = _parse_console_banner(line)
            if parsed is not None:
                port, token = parsed
                return ConsoleProcess(process=process, port=port, access_token=token)
        try:
            process.terminate()
        except Exception:  # pragma: no cover - defensive
            pass
        raise PortalError(
            "The local decision service did not start. It requires "
            "openadapt-flow with the 'console' extra (fastapi and uvicorn) and a "
            "qualified deployment configuration."
        )

    def stop(self) -> dict[str, Any]:
        """Stop the portal and the attended console it supervises."""
        with self._lock:
            if self._server is not None:
                self._server.stop()
            if self._console is not None:
                self._console.stop()
            self._server = None
            self._console = None
            self._flow = None
            self._pairings = None
            self._ingress = None
            return {"running": False, "ingress": self.describe_ingress()}

    def describe_ingress(self) -> dict[str, Any]:
        """Describe configured ingress without starting anything."""
        try:
            return resolve_ingress(self.config).describe() | {"configured": True}
        except IngressError as exc:
            return {
                "configured": False,
                "mode": str(getattr(self.config, "portal_ingress_mode", "loopback")),
                "loopback_only": True,
                "reachable_from_phone": False,
                "error": str(exc),
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> dict[str, Any]:
        if self._server is None or self._ingress is None:
            return {
                "running": False,
                "ingress": self.describe_ingress(),
                "devices": [],
                "error": self._last_error,
            }
        return {
            "running": True,
            "console_alive": self._console is not None and self._console.alive(),
            "ingress": self._ingress.describe(),
            "port": self._server.port,
            "devices": self._pairings.devices() if self._pairings else [],
            "error": None,
        }

    # ---------------------------------------------------------------- pairing

    def _require_pairings(self) -> DevicePairingStore:
        if self._pairings is None or self._ingress is None:
            raise PortalError("Start the decision portal before pairing a phone.")
        return self._pairings

    def create_pairing(self) -> dict[str, Any]:
        """Mint one QR pairing.  The secret rides in the link's fragment only."""
        with self._lock:
            pairings = self._require_pairings()
            assert self._ingress is not None
            pairing = pairings.create(
                self._ingress.public_origin,
                reachable_from_phone=self._ingress.reachable_from_phone,
            )
        result = pairing.public()
        result["qr_svg"] = _qr_svg(pairing.url)
        if not self._ingress.reachable_from_phone:
            result["note"] = (
                "This portal is loopback-only, so a phone cannot reach this link. "
                "Configure your organization's HTTPS or VPN ingress to publish it."
            )
        return result

    def approve_pairing(self, pairing_id: str) -> dict[str, Any]:
        with self._lock:
            return self._require_pairings().approve(pairing_id)

    def cancel_pairing(self, pairing_id: str) -> dict[str, Any]:
        with self._lock:
            return self._require_pairings().cancel(pairing_id)

    def pairing_status(self, pairing_id: str) -> dict[str, Any]:
        with self._lock:
            return self._require_pairings().pairing_status(pairing_id)

    def devices(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._pairings.devices() if self._pairings else []

    def revoke_device(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            return self._require_pairings().revoke(session_id)

    # ----------------------------------------------------------- notification

    def notification(self) -> dict[str, Any]:
        """Return the generic operating-system notification payload.

        Only an integer count crosses this boundary.  No upstream string is
        read, so no question, value, identifier, or application name can reach
        a lock screen.
        """
        flow = self._flow
        if flow is None:
            return assert_generic_notification(build_notification(0))
        try:
            response = flow.request("notification")
        except FlowConsoleUnavailable:
            return assert_generic_notification(build_notification(0))
        return assert_generic_notification(notification_from_upstream(response.json))


def _qr_svg(url: str) -> str | None:
    """Render the pairing link as an inline SVG, when a QR encoder is present.

    Rendering happens locally and the result is handed straight to the Desktop
    window.  When the optional encoder is unavailable the caller still has the
    URL and the matching code, so pairing degrades to typing rather than
    failing.
    """
    try:
        import segno
    except ImportError:  # pragma: no cover - optional dependency
        return None
    try:
        import io

        buffer = io.StringIO()
        segno.make(url, error="m").save(
            buffer, kind="svg", scale=6, border=2, xmldecl=False, svgns=True
        )
        return buffer.getvalue()
    except Exception:  # pragma: no cover - defensive
        return None


__all__ = [
    "ConsoleProcess",
    "PairingRefused",
    "PortalError",
    "PortalService",
    "_parse_console_banner",
]
