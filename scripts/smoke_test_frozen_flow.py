#!/usr/bin/env python3
"""Clean-user smoke for the native binary's embedded Flow + browser runtime.

Also proves the frozen sidecar can serve the mobile decision portal's upstream:
the attended console. That console is a *packaging* contract as much as a
product one -- it needs fastapi/uvicorn/starlette, ``openadapt_flow.console``
(including ``human_decisions``), and a capability banner that survives a pipe.
Every one of those has silently shipped broken before, and none of them is
visible from a version string.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: How long the frozen console may take to announce its capability banner.
CONSOLE_BANNER_TIMEOUT_S = 180.0
#: How long uvicorn may take to bind after the banner is printed.
CONSOLE_READY_TIMEOUT_S = 60.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _seed_halted_run(runs_root: Path) -> None:
    """Write one synthetic halted run so the queue has something to project."""

    run_dir = runs_root / "run-frozen-console-smoke"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "workflow_name": "frozen-console-smoke",
                "started_at": "2026-01-01T00:00:00Z",
                "success": False,
                "execution_outcome": "HALTED",
                "execution_completed": False,
                "halt": {
                    "state_id": "state-1",
                    "intent": "open the record",
                    "reason": "identity was not confirmed",
                    "outcome": "halt",
                    "observed_texts": ["synthetic observation"],
                    "completed_intents": [],
                },
                "results": [],
            }
        ),
        encoding="utf-8",
    )


def _console_command(executable: Path, root: Path, port: int) -> list[str]:
    """The exact shape ``engine.portal.service`` spawns, minus mutations.

    ``--allow-actions`` is exercised separately: Flow deliberately refuses
    attended mutations that are not bound to a deployment target, and that
    refusal is asserted rather than worked around.
    """

    # Flow refuses a root that traverses a symlink, and a macOS temporary
    # directory reaches ``/private/var`` through one.
    return [
        str(executable),
        "__openadapt_flow__",
        "console",
        "--attend",
        "--bundles",
        str((root / "console-bundles").resolve()),
        "--runs",
        str((root / "console-runs").resolve()),
        "--port",
        str(port),
    ]


def _await_console_banner(process: subprocess.Popen) -> tuple[int, str]:
    """Read the capability banner exactly the way the portal service does."""

    from engine.portal.service import _parse_console_banner

    found: queue.Queue = queue.Queue(maxsize=1)

    def drain_stdout() -> None:
        published = False
        for line in iter(process.stdout.readline, ""):
            if published:
                continue
            parsed = _parse_console_banner(line)
            if parsed is not None:
                published = True
                found.put_nowait(parsed)
        if not published:
            found.put_nowait(None)

    diagnostics: deque[str] = deque(maxlen=20)

    def drain_stderr() -> None:
        for line in iter(process.stderr.readline, ""):
            diagnostics.append(line.rstrip())

    threading.Thread(target=drain_stdout, daemon=True).start()
    threading.Thread(target=drain_stderr, daemon=True).start()
    try:
        parsed = found.get(timeout=CONSOLE_BANNER_TIMEOUT_S)
    except queue.Empty:
        parsed = None
    if parsed is None:
        raise RuntimeError(
            "the frozen attended console never announced a capability banner; "
            "the mobile decision portal reads that exact line from a pipe: "
            + " | ".join(diagnostics)
        )
    return parsed


def _verify_attended_console(executable: Path, root: Path, env: dict[str, str]) -> dict:
    """Prove the frozen sidecar serves the portal's decision surface."""

    import urllib.error
    import urllib.request

    from engine.portal.flow_client import FlowConsoleClient, FlowConsoleUnavailable
    from engine.portal.notifications import assert_generic_notification, notification_from_upstream

    (root / "console-bundles").mkdir(parents=True, exist_ok=True)
    _seed_halted_run(root / "console-runs")

    started = time.monotonic()
    process = subprocess.Popen(
        _console_command(executable, root, _free_port()),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        port, access_token = _await_console_banner(process)
        banner_seconds = time.monotonic() - started
        flow = FlowConsoleClient(port=port, access_token=access_token)

        deadline = time.monotonic() + CONSOLE_READY_TIMEOUT_S
        while True:
            try:
                session = flow.request("session")
                break
            except FlowConsoleUnavailable:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        if session.status != 200 or not isinstance(session.json, dict):
            raise RuntimeError(f"frozen console did not serve a session: {session.status}")
        flow.csrf_token = str(session.json.get("csrf_token") or "")
        if not flow.csrf_token:
            raise RuntimeError("frozen console served no CSRF capability")
        if session.json.get("attend") is not True:
            raise RuntimeError("frozen console did not start attention-first")

        notification = flow.request("notification")
        if notification.status != 200:
            raise RuntimeError(f"attention notification is absent: {notification.status}")
        assert_generic_notification(notification_from_upstream(notification.json))

        tasks = flow.request("tasks")
        payload = tasks.json
        items = payload.get("items") if isinstance(payload, dict) else payload
        if tasks.status != 200 or not isinstance(items, list) or not items:
            raise RuntimeError(f"frozen console projected no attention queue: {tasks.status}")
        run_id = str(items[0].get("id") or "")

        detail = flow.request("task_detail", run_id=run_id)
        if detail.status != 200 or not isinstance(detail.json, dict):
            raise RuntimeError(f"frozen console served no decision detail: {detail.status}")
        # ``item``/``task``/``task_digest``/``presentation`` is the shape
        # ``openadapt_flow.console.human_decisions.decision_detail`` returns.
        # Flow 1.23.0 -- the version this installer used to freeze -- returned a
        # bare attention item here, and the portal shell renders none of it.
        missing = [
            key
            for key in ("item", "task", "task_digest", "presentation")
            if key not in detail.json
        ]
        if missing:
            raise RuntimeError(
                "frozen console did not serve the human-decision projection; "
                f"missing: {', '.join(missing)}"
            )
        presentation = detail.json.get("presentation")
        if not isinstance(presentation, dict) or not presentation.get("question"):
            raise RuntimeError("decision detail carried no operator question")

        # The capability is load-bearing: a phone reaches the portal, never the
        # console, and an unauthenticated caller must get nothing at all.
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/session", timeout=10)
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise RuntimeError(f"console answered an unauthenticated caller: {exc.code}")
        else:
            raise RuntimeError("console served an unauthenticated caller")
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()

    refusal = subprocess.run(
        _console_command(executable, root, _free_port()) + ["--allow-actions"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=env,
    )
    combined = refusal.stdout + refusal.stderr
    if refusal.returncode == 0 or "requires an explicit --config" not in combined:
        raise RuntimeError(
            "the frozen console no longer refuses attended mutations without a "
            f"deployment target (exit {refusal.returncode}): {combined[-1000:]}"
        )

    return {
        "console_banner_seconds": round(banner_seconds, 3),
        "console_decision_question": bool(presentation.get("question")),
        "console_unauthenticated_status": 401,
        "console_refuses_unbound_mutations": True,
    }


def _run(command: list[str], *, env: dict[str, str], timeout: float = 900) -> tuple[str, float]:
    started = time.monotonic()
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    elapsed = time.monotonic() - started
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        raise RuntimeError(f"{command!r} exited {result.returncode}: {output[-3000:]}")
    return output, elapsed


def main() -> int:
    parser = argparse.ArgumentParser()
    suffix = ".exe" if sys.platform == "win32" else ""
    parser.add_argument(
        "--artifact",
        type=Path,
        default=ROOT / "dist" / f"openadapt-engine{suffix}",
    )
    args = parser.parse_args()
    executable = args.artifact.resolve(strict=True)

    with tempfile.TemporaryDirectory(prefix="openadapt-frozen-flow-") as raw_root:
        root = Path(raw_root)
        env = dict(os.environ)
        # A brand-new path proves the frozen executable itself performs the
        # first-use provision.  No system Python or openadapt-flow command is
        # used by any lifecycle command below.
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(root / "browser-runtime")
        env["OPENADAPT_VISION_RUNTIME_ROOT"] = str(root / "vision-runtime")
        env.pop("OPENADAPT_FLOW_NO_AUTO_INSTALL", None)

        flow = [str(executable), "__openadapt_flow__"]
        first_output, first_seconds = _run(
            [*flow, "demo-record", "--out", str(root / "recording")],
            env=env,
        )
        if "local vision runtime is ready" not in first_output:
            raise RuntimeError("clean-user run did not exercise first-use vision provision")
        if not any((root / "vision-runtime").glob("*/*/.complete.json")):
            raise RuntimeError("vision runtime was not persisted outside the one-file extraction")
        if "Downloading the Chromium browser" not in first_output:
            raise RuntimeError("clean-user run did not exercise first-use browser provision")
        if not any((root / "browser-runtime").glob("chromium*")):
            raise RuntimeError("browser runtime was not persisted outside the one-file extraction")

        _run(
            [
                *flow,
                "compile",
                str(root / "recording"),
                "--out",
                str(root / "bundle"),
                "--name",
                "native-frozen-demo",
            ],
            env=env,
        )
        _, replay_seconds = _run(
            [
                *flow,
                "replay",
                str(root / "bundle"),
                "--run-dir",
                str(root / "run"),
            ],
            env=env,
        )

        report = json.loads((root / "run" / "report.json").read_text(encoding="utf-8"))
        results = report.get("results") or []
        if not results or not all(item.get("ok") is True for item in results):
            raise RuntimeError("frozen replay did not produce an all-success report")
        if report.get("execution_outcome") != "COMPLETED_UNVERIFIED":
            raise RuntimeError(
                "Demo replay did not preserve the precise COMPLETED_UNVERIFIED outcome"
            )
        if (
            report.get("execution_profile") != "demo"
            or report.get("production_eligible") is not False
            or report.get("execution_completed") is not True
        ):
            raise RuntimeError(
                "Demo replay did not remain completed and explicitly non-production"
            )
        envelope = report.get("outcome_envelope")
        if not isinstance(envelope, dict) or (
            envelope.get("version") != "openadapt.execution-outcome/v1"
            or envelope.get("outcome") != report.get("execution_outcome")
            or envelope.get("profile") != report.get("execution_profile")
            or envelope.get("production_eligible") != report.get("production_eligible")
            or envelope.get("execution_completed") != report.get("execution_completed")
        ):
            raise RuntimeError("frozen replay did not emit a bound v1 outcome envelope")
        for contract_counts in ("required_contracts", "passed_contracts"):
            counts = envelope.get(contract_counts)
            if not isinstance(counts, dict) or set(counts) != {
                "authorization",
                "identity",
                "postcondition",
                "effect",
            }:
                raise RuntimeError(
                    f"frozen replay emitted invalid {contract_counts} evidence counts"
                )
        if any(
            envelope["passed_contracts"][contract]
            > envelope["required_contracts"][contract]
            for contract in envelope["required_contracts"]
        ):
            raise RuntimeError("frozen replay reported more passed than required contracts")
        metrics = report.get("metrics") or {}
        if (
            report.get("model_calls") != 0
            or envelope.get("model_calls") != 0
            or metrics.get("model_calls", 0) != 0
            or metrics.get("cost_usd", 0) != 0
        ):
            raise RuntimeError("healthy frozen replay unexpectedly used a model or incurred cost")
        network_observation = report.get("external_network_calls")
        if (
            network_observation not in {"none", "observed", "unknown"}
            or envelope.get("external_network_calls") != network_observation
        ):
            raise RuntimeError(
                "frozen replay did not bind its external-network observation to the outcome"
            )
        from engine.flow_bridge import FlowBridge

        if FlowBridge.classify_outcome(0, report) != "COMPLETED_UNVERIFIED":
            raise RuntimeError(
                "Desktop rejected the precise outcome emitted by its frozen Flow runtime"
            )

        second_output, warm_seconds = _run(
            [*flow, "demo-record", "--out", str(root / "recording-warm")],
            env=env,
        )
        if "Downloading the Chromium browser" in second_output:
            raise RuntimeError("warm run attempted to download the browser again")
        if any(
            marker in second_output
            for marker in (
                "preparing the separately licensed local vision runtime",
                "downloading rapidocr-onnxruntime",
                "downloading numpy",
                "downloading opencv-python",
            )
        ):
            raise RuntimeError("warm run attempted to download the vision runtime again")

        console = _verify_attended_console(executable, root, env)

        print(
            json.dumps(
                {
                    "artifact_bytes": executable.stat().st_size,
                    "first_provision_seconds": round(first_seconds, 3),
                    "replay_seconds": round(replay_seconds, 3),
                    "warm_record_seconds": round(warm_seconds, 3),
                    "steps": len(results),
                    "outcome": report["execution_outcome"],
                    "production_eligible": report["production_eligible"],
                    "external_network_calls": network_observation,
                    "silent_incorrect_successes": 0,
                    "model_calls": 0,
                    **console,
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
