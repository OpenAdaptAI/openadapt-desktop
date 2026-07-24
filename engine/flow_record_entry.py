"""Private process entry for canonical ``openadapt-flow record`` authoring.

The shipped Flow 1.20.1 recorder accepts target selectors as command flags.
Those values can contain PHI, so Desktop starts this helper with only a private
request-file path in the operating-system process list. The helper reads and
deletes that file, validates the closed target schema, reconstructs Flow's
canonical ``record`` arguments in memory, and calls Flow's own CLI entry point.

A private stop sentinel lets the long-running recorder finish through its
normal ``KeyboardInterrupt`` path on every platform instead of terminating it
before capture conversion is complete.
"""

from __future__ import annotations

import _thread
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml

from engine.targets import ExecutionTarget


def _request(path: Path) -> tuple[ExecutionTarget, Path, str, Path, Path]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    finally:
        path.unlink(missing_ok=True)
    if not isinstance(loaded, Mapping):
        raise ValueError("private Flow record request must contain an object")
    target = ExecutionTarget.model_validate(loaded.get("target"))
    out_dir = Path(str(loaded["out_dir"]))
    task = str(loaded.get("task") or "")
    stop_path = Path(str(loaded["stop_path"]))
    ready_path = Path(str(loaded["ready_path"]))
    return target, out_dir, task, stop_path, ready_path


def _watch_for_stop(stop_path: Path) -> None:
    while not stop_path.exists():
        time.sleep(0.1)
    stop_path.unlink(missing_ok=True)
    _thread.interrupt_main()


def main(argv: Sequence[str] | None = None) -> int:
    """Read one private request and execute Flow's own record handler."""

    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 1:
        raise SystemExit("private Flow record helper expects one request path")
    target, out_dir, task, stop_path, ready_path = _request(Path(args[0]))
    flow_args = target.record_args(out_dir, task=task)

    from openadapt_flow.__main__ import main as flow_main

    monitor = threading.Thread(
        target=_watch_for_stop,
        args=(stop_path,),
        name="flow-record-stop",
        daemon=True,
    )
    monitor.start()
    ready_path.touch(mode=0o600, exist_ok=False)
    try:
        return int(flow_main(flow_args))
    finally:
        ready_path.unlink(missing_ok=True)
        stop_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
