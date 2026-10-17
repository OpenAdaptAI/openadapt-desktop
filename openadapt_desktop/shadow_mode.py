"""Shadow mode in Desktop: the opt-in notice, the status, and the prompt.

Shadow mode is OpenAdapt Capture's shadow miner
(``openadapt_capture.shadow``): it notices a task a person repeats across
apps and offers "You did this N times today." with **Review draft** and
**Ignore**. This module is Desktop's side of it.

* **Off by default, on only by the person.** The only way to turn it on is
  :meth:`ShadowModeService.enable`, called from Desktop's own window with the
  hash of the notice the person was shown. Nothing in ``EngineConfig``, the
  environment, the hosted policy, the tray's loopback socket, or the phone
  portal can turn it on. There is no admin or remote mode.
* **The notice is Capture's.** :meth:`notice` returns it as Capture defines
  it, and :meth:`enable` records the acknowledgement time locally through
  Capture's consent ledger.
* **One off switch.** :meth:`purge_and_disable` deletes the buffer, the key,
  the ignore list, and any saved drafts, records the revocation time, and
  stays off until the person opts in again.
* **Local only.** Everything lives under ``<data_dir>/shadow`` with owner-only
  permissions. Nothing here opens a network connection, and nothing here is
  uploaded or synced.

Events reach the miner through the recorder's live tap while Desktop records
(see :meth:`live_tap`). The miner is scanned every ``scan_interval_s``
seconds while shadow mode is on.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from openadapt_desktop.private_fs import ensure_private_dir, make_private_file

EmitFn = Callable[[str, dict], None]

STATE_EVENT = "shadow_state"
PROMPT_EVENT = "shadow_prompt"
BUFFER_HOURS = 24


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` to a file that is owner-only from the moment it exists."""

    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    make_private_file(tmp)
    os.replace(tmp, path)


class ShadowModeUnavailable(RuntimeError):
    """The installed OpenAdapt Capture has no shadow mode."""


def _capture_shadow() -> Any:
    try:
        from openadapt_capture import shadow
        from openadapt_capture.shadow import consent  # noqa: F401
    except Exception as exc:  # ImportError, or a broken optional dependency
        raise ShadowModeUnavailable(
            "this OpenAdapt Capture has no shadow mode; update OpenAdapt Desktop"
        ) from exc
    if not hasattr(shadow, "ConsentLedger"):
        raise ShadowModeUnavailable("this OpenAdapt Capture's shadow mode has no notice")
    return shadow


class _CurrentObserver:
    """What a recording's live tap feeds: whichever observer is current.

    A tap lives for one recording, but the observer can be replaced while it
    runs (a work-site change) or shut (Delete everything and turn off). Each
    event goes to the observer that is current when it arrives, or nowhere.
    """

    def __init__(self, service: ShadowModeService) -> None:
        self._service = service

    def observe(self, event: Any) -> None:
        with self._service._lock:
            observer = self._service._observer
            if observer is not None and observer.enabled:
                observer.observe(event)


class _Notifier:
    def __init__(self, service: ShadowModeService) -> None:
        self._service = service

    def offer(self, prompt: Any) -> None:
        self._service._offer(prompt.payload())


class ShadowModeService:
    """Desktop's owner of one person's shadow mode."""

    def __init__(
        self,
        data_dir: Path,
        emit: EmitFn | None = None,
        *,
        key_store: Any = None,
        clock: Callable[[], float] = time.time,
        scan_interval_s: float = 60.0,
        autostart_scanner: bool = True,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.dir = self.data_dir / "shadow"
        self._emit = emit or (lambda event, data: None)
        self._key_store = key_store
        self._clock = clock
        self._scan_interval_s = float(scan_interval_s)
        self._autostart = autostart_scanner
        self._lock = threading.RLock()
        self._observer: Any = None
        self._pending: dict[str, dict[str, Any]] = {}
        self._stop = threading.Event()
        self._scanner: threading.Thread | None = None

    # ------------------------------------------------------------- paths

    @property
    def _state_path(self) -> Path:
        return self.dir / "state.json"

    @property
    def _domains_path(self) -> Path:
        return self.dir / "work_domains.json"

    @property
    def _drafts_dir(self) -> Path:
        return self.dir / "drafts"

    def _ledger(self) -> Any:
        return _capture_shadow().ConsentLedger(self.dir / "consent.json")

    def _write_json(self, path: Path, data: Any) -> None:
        ensure_private_dir(path.parent, root=self.data_dir)
        _write_private(path, json.dumps(data, indent=2, sort_keys=True))

    def _read_json(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return default

    # ------------------------------------------------------------- queries

    def available(self) -> bool:
        try:
            _capture_shadow()
        except ShadowModeUnavailable:
            return False
        return True

    def work_domains(self) -> list[str]:
        domains = self._read_json(self._domains_path, [])
        return sorted(d for d in domains if isinstance(d, str)) if isinstance(domains, list) else []

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = self._observer is not None and self._observer.enabled
            ack = None
            if self.available():
                current = self._ledger().current()
                ack = current.acknowledged_at if current is not None else None
            return {
                "available": self.available(),
                "active": active,
                "acknowledged_at": ack,
                "buffer_hours": BUFFER_HOURS,
                "work_domains": self.work_domains(),
                "keys_in_keychain": bool(active and self._observer.keys_persisted),
                "epoch_ends_at": self._observer.epoch_ends_at if active else None,
                "watches": "recordings" if active else None,
                "pending": list(self._pending.values()),
            }

    def notice(self) -> dict[str, Any]:
        return _capture_shadow().notice()

    # ------------------------------------------------------------- on / off

    def enable(self, notice_sha256: str, acknowledged: bool) -> dict[str, Any]:
        """Turn shadow mode on after the person acknowledged the notice.

        ``notice_sha256`` must be the hash of the notice the window showed;
        ``acknowledged`` must be True (the person clicked the button).
        """

        shadow = _capture_shadow()
        if acknowledged is not True:
            raise PermissionError("shadow mode turns on only after the notice is acknowledged")
        with self._lock:
            ensure_private_dir(self.dir, root=self.data_dir)
            ack = self._ledger().acknowledge(str(notice_sha256), now=self._clock())
            self._start(shadow, ack)
            self._write_json(self._state_path, {"active": True})
        self._emit_state()
        return self.status()

    def resume(self) -> bool:
        """Turn shadow mode back on at engine start if the person left it on.

        Needs a standing acknowledgement of the current notice; a changed
        notice must be acknowledged again.
        """

        if not self._read_json(self._state_path, {}).get("active"):
            return False
        try:
            shadow = _capture_shadow()
            ack = self._ledger().current()
        except ShadowModeUnavailable:
            ack = None
        if ack is None:
            # It was left on, but can't come back on: the notice changed, or
            # this Capture has no shadow mode. Don't leave its buffer and key
            # behind waiting for a resume that will never happen.
            logger.info("shadow mode can't resume; deleting what it kept")
            self.purge_and_disable()
            return False
        with self._lock:
            self._start(shadow, ack)
        self._emit_state()
        return True

    def _start(self, shadow: Any, ack: Any) -> None:
        if self._observer is not None and self._observer.enabled:
            return
        config = shadow.ShadowConfig(
            enabled=True,
            store_path=self.dir / "buffer.db",
            work_domains=frozenset(self.work_domains()),
        )
        self._observer = shadow.ShadowObserver(
            config,
            acknowledgement=ack,
            notifier=_Notifier(self),
            suppression=shadow.SuppressionList(self.dir / "ignored.json"),
            key_store=self._key_store,
            clock=self._clock,
        )
        if self._autostart:
            self._start_scanner()

    def purge_and_disable(self) -> dict[str, Any]:
        """Delete everything shadow mode kept, record the revocation, turn off."""

        self._stop_scanner()
        with self._lock:
            observer, self._observer = self._observer, None
            if observer is not None:
                key_deleted = observer.purge_and_disable() is not False
            else:
                key_deleted = self._purge_without_observer()
            self._pending.clear()
            shutil.rmtree(self._drafts_dir, ignore_errors=True)
            for name in ("buffer.db", "buffer.db-journal", "ignored.json"):
                try:
                    (self.dir / name).unlink()
                except FileNotFoundError:
                    pass
            try:
                self._ledger().revoke(now=self._clock())
            except ShadowModeUnavailable:
                pass
            self._write_json(self._state_path, {"active": False})
        self._emit_state()
        # The buffer is gone either way. If the keychain refused to delete the
        # key, say so: it's retried on the next start, but it isn't gone yet.
        return {**self.status(), "key_deleted": key_deleted}

    def _purge_without_observer(self) -> bool:
        try:
            from openadapt_capture.shadow.keys import destroy_keys
        except Exception:
            return True  # no shadow mode, so no key was ever made here
        return bool(destroy_keys(self._key_store))

    # ------------------------------------------------------------- settings

    def set_work_domains(self, domains: list[str]) -> dict[str, Any]:
        """Replace the local work-domain allowlist. Applies from now on."""

        shadow = _capture_shadow()
        from openadapt_capture.shadow.config import normalize_domain

        if not isinstance(domains, list):
            raise ValueError("domains must be a list of domain names")
        cleaned = sorted({normalize_domain(d) for d in domains if str(d).strip()})
        with self._lock:
            self._write_json(self._domains_path, cleaned)
            if self._observer is not None and self._observer.enabled:
                ack = self._observer.acknowledgement
                self._observer.close()
                self._observer = None
                self._start(shadow, ack)
        self._emit_state()
        return self.status()

    # ------------------------------------------------------------- prompts

    def _offer(self, payload: dict[str, Any]) -> None:
        self._pending[payload["motif_id"]] = payload
        self._emit(PROMPT_EVENT, dict(payload))

    def scan(self) -> list[dict[str, Any]]:
        with self._lock:
            if self._observer is None or not self._observer.enabled:
                return []
            prompts = self._observer.scan()
        return [p.payload() for p in prompts]

    def decide(self, motif_id: str, action: str) -> dict[str, Any]:
        """Answer one prompt. Review draft saves the export locally; Ignore
        suppresses the task. Neither runs anything."""

        with self._lock:
            if self._observer is None or not self._observer.enabled:
                raise RuntimeError("shadow mode is off")
            export = self._observer.decide(str(motif_id), str(action))
            self._pending.pop(str(motif_id), None)
            result: dict[str, Any] = {"motif_id": motif_id, "action": action}
            if export is not None:
                ensure_private_dir(self._drafts_dir, root=self.data_dir)
                path = self._drafts_dir / f"{export['motif_id']}.episodes.json"
                _write_private(path, json.dumps(export, indent=1, sort_keys=True))
                result.update(
                    {
                        "draft_path": str(path),
                        "episodes": len(export["episodes"]),
                        "next": (
                            "This is a draft, not an automation. Record the task once "
                            "to start; a person reviews and admits it before it can run."
                        ),
                    }
                )
        self._emit_state()
        return result

    # ------------------------------------------------------------- source

    def live_tap(self) -> Any:
        """A live tap for the next recording, or None while shadow mode is off."""

        with self._lock:
            if self._observer is None or not self._observer.enabled:
                return None
            from openadapt_capture.live import LiveEventTap

            shadow = _capture_shadow()
            return LiveEventTap(shadow.RecorderShadowBridge(_CurrentObserver(self))).start()

    # ------------------------------------------------------------- scanner

    def _start_scanner(self) -> None:
        if self._scanner is not None and self._scanner.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(self._scan_interval_s):
                try:
                    self.scan()
                except Exception as exc:
                    logger.warning("shadow scan failed: {}", type(exc).__name__)

        self._scanner = threading.Thread(target=loop, name="shadow-scan", daemon=True)
        self._scanner.start()

    def _stop_scanner(self) -> None:
        self._stop.set()
        scanner, self._scanner = self._scanner, None
        if scanner is not None:
            scanner.join(timeout=2.0)

    def _emit_state(self) -> None:
        try:
            self._emit(STATE_EVENT, self.status())
        except Exception as exc:
            logger.warning("shadow state event failed: {}", type(exc).__name__)

    def close(self) -> None:
        self._stop_scanner()
        with self._lock:
            if self._observer is not None:
                self._observer.close()
