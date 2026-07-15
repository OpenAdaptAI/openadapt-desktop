# OpenAdapt Desktop

[![Tests](https://github.com/OpenAdaptAI/openadapt-desktop/actions/workflows/test.yml/badge.svg)](https://github.com/OpenAdaptAI/openadapt-desktop/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

The local cockpit for [OpenAdapt](https://openadapt.ai): **record a GUI workflow
once → compile it into a deterministic bundle → replay it locally at $0 → teach a
correction when it halts.** A cross-platform desktop app (Tauri shell + a Python
engine sidecar) that wraps the [`openadapt-flow`](https://github.com/OpenAdaptAI/openadapt-flow)
compiler and, optionally, pushes workflows to a cloud workspace.

> **This is not a screen-recorder for AI training data.** OpenAdapt compiles a
> single demonstration into a governed, self-healing replay that verifies real
> effects and **halts rather than guessing**. Nothing leaves your machine unless
> you explicitly push it.

## The loop

```
  record  ─▶  compile  ─▶  replay ($0, deterministic)  ─▶  HALT on ambiguity
    ▲                                                            │
    │                                                            ▼
    └──────────────  teach the fix (governed promote)  ◀── review the halt
```

- **Record** your own app locally (via [openadapt-capture](https://github.com/OpenAdaptAI/openadapt-capture)).
- **Compile** the recording into a workflow bundle with `openadapt-flow` — a
  vision-anchored, deterministic program, not a model prompt.
- **Replay** it locally with **zero model calls**; it self-heals on UI drift and
  **halts** instead of performing a wrong write.
- **Teach** a correction from a single demonstration when it halts; the fix is
  promoted only if it passes a regression + held-out gate.
- **Push** (optional) a recording or bundle to a cloud workspace for org-wide
  visibility — the **non-PHI lane** only.

## Where the desktop app fits

OpenAdapt has three local/remote surfaces that share one loop:

| Surface | Role |
|---|---|
| **Desktop app** (this repo) | The authoring/teaching cockpit — record & review, local **teach-the-fix**, login + push, deployment-lane / PHI-mode settings, the durable upload queue. Hosts the engine sidecar. The only surface that mutates state. |
| **[Tray](https://github.com/OpenAdaptAI/openadapt-tray)** | The always-on, lightweight menu-bar status + launcher (recording/sync state, a break-count badge, quick actions). |
| **Cloud** (`app.openadapt.ai`) | The org control plane — dashboard, ingest, needs-attention triage. |

On the **regulated / on-prem (byoc) lane**, the recording and teaching **never
leave the machine** — only a PHI-free break descriptor syncs up. On the **cloud
(non-PHI) lane**, a recording is pushed and compiled in the cloud. The lane is a
routing decision, not a footgun.

## Status

The **engine** (`openadapt-flow`) is the mature, tested core of the loop and
works today from the command line — see the
[docs](https://docs.openadapt.ai) and the
[five-minute tour](https://docs.openadapt.ai/get-started/). This repository is
building the **graphical desktop cockpit** around it (Tauri 2.x shell + a frozen
Python engine sidecar + a durable offline upload queue). It is under active
development; expect the CLI loop to be ahead of the GUI.

## Get started

Right now, drive the loop with the CLI:

```bash
pip install openadapt-flow
openadapt-flow demo-record --out rec          # record a sample workflow
openadapt-flow compile rec --out bundle --name my-task
openadapt-flow replay bundle                  # local, deterministic, $0
```

Full walkthroughs:

- [Desktop app — install and first run](https://docs.openadapt.ai/desktop/install/)
  (including the **OS permissions** you must grant — the #1 silent-failure mode)
- [Connect the desktop app to a cloud workspace](https://docs.openadapt.ai/desktop/connect-to-cloud/)
- [Record your own app](https://docs.openadapt.ai/guides/record-your-app/)
- [The `openadapt flow` CLI reference](https://docs.openadapt.ai/reference/cli/)
- [Troubleshooting](https://docs.openadapt.ai/guides/troubleshooting/)

## OS permissions (read before your first recording)

Recording needs OS permission, and a missing grant is the #1 silent failure —
capture comes back **blank** with no error.

- **macOS**: grant **Screen Recording** and **Accessibility** in
  **System Settings → Privacy & Security**, then **restart the app** (macOS only
  applies a new Screen Recording grant after a restart).
- **Windows**: ordinary windows work out of the box; to capture an **elevated
  (administrator)** window, run OpenAdapt **as administrator** too.

Details: [permissions guide](https://docs.openadapt.ai/desktop/install/#3-grant-os-permissions-the-step-everyone-misses).

## Development

```bash
git clone https://github.com/OpenAdaptAI/openadapt-desktop.git
cd openadapt-desktop
uv sync --extra dev
uv run pytest tests/ -v
uv run ruff check engine/ tests/
```

The two-process model: a **Tauri shell** (Rust + WebView — system tray, native
window, IPC dispatch) and a **Python engine** sidecar that wraps `openadapt-flow`
and communicates over JSON-over-stdin/stdout. See [DESIGN.md](DESIGN.md).

## Related projects

| Project | Description |
|---|---|
| [openadapt-flow](https://github.com/OpenAdaptAI/openadapt-flow) | The demonstration compiler — the engine this app wraps |
| [openadapt-tray](https://github.com/OpenAdaptAI/openadapt-tray) | Menu-bar status + launcher for the loop |
| [openadapt-capture](https://github.com/OpenAdaptAI/openadapt-capture) | Cross-platform desktop recording |
| [openadapt-privacy](https://github.com/OpenAdaptAI/openadapt-privacy) | PII/PHI detection and redaction (Presidio) |

## License

[MIT](https://opensource.org/licenses/MIT)
