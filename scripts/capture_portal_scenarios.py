#!/usr/bin/env python3
# ruff: noqa: E501
"""Capture deterministic, synthetic phone decision screens.

This is a presentation-fixture generator. It starts no runner, reads no run
directory, and never uses customer evidence. The generated PNG files stay in
the caller-selected directory, outside the Desktop package by default.

Example:
    uv run --extra build python scripts/capture_portal_scenarios.py \
      --out /tmp/openadapt-desktop-phone-fixtures
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "engine" / "portal" / "shell"


def _task(*, kind: str, delivery: str, actions: list[str]) -> dict[str, Any]:
    return {
        "capability_digest": "sha256:" + "a" * 64,
        "signature": "hmac-sha256:" + "b" * 64,
        "delivery_state": delivery,
        "task_kind": kind,
        "risk_class": "consequential" if kind in {"effect", "delivery_uncertain"} else "unknown",
        "created_at": "2026-07-29T12:00:00+00:00",
        "expires_at": "2026-07-29T13:00:00+00:00",
        "allowed_actions": actions,
        "evidence": {
            "identity_required_count": 2 if kind == "identity" else None,
            "identity_confirmed_count": 1 if kind == "identity" else None,
            "effect_required_count": 1 if kind in {"effect", "delivery_uncertain"} else None,
            "effect_confirmed_count": 0 if kind in {"effect", "delivery_uncertain"} else None,
            "minimum_effect_tier": 1 if kind in {"effect", "delivery_uncertain"} else None,
            "observed_effect_tier": None,
            "frame_available_locally": False,
            "sensitive_evidence_local_only": True,
        },
    }


def _detail(
    *,
    title: str,
    kind: str,
    delivery: str = "not_delivered",
    actions: list[str],
    category: str,
    question: str,
) -> dict[str, Any]:
    return {
        "task": _task(kind=kind, delivery=delivery, actions=actions),
        "task_digest": "sha256:" + "c" * 64,
        "presentation": {
            "question": question,
            "explanation": title,
            "halt": {
                "category": category,
                "step_ordinal": 3,
                "step_count": 6,
                "action_kind": "click",
                "target_role": "button",
                "target_label": "Save",
                "resolution_ladder": [],
                "will_recheck": [
                    {
                        "check": "delivery_reconciliation"
                        if kind == "delivery_uncertain"
                        else "record_identity",
                        "count": None,
                    }
                ],
            },
        },
    }


SCENARIOS: dict[str, dict[str, Any]] = {
    "identity": _detail(
        title="OpenAdapt could not confirm the intended record.",
        kind="identity",
        actions=["verify_and_resume", "reject", "teach", "escalate"],
        category="identity",
        question="Is the intended record now open in the live application?",
    ),
    "ambiguity": _detail(
        title="More than one target matched, so OpenAdapt chose none.",
        kind="ambiguity",
        actions=["verify_and_resume", "reject", "teach", "escalate"],
        category="disambiguation",
        question="Can you leave one clear target ready in the live application?",
    ),
    "human-step": _detail(
        title="The application needs a person before the workflow can continue.",
        kind="human_step",
        actions=["verify_and_resume", "escalate"],
        category="human_required",
        question="Complete the sign-in or challenge in the live application, then check again.",
    ),
    "effect": _detail(
        title="The saved system-of-record result was not confirmed.",
        kind="effect",
        actions=["verify_and_resume", "reject", "teach", "escalate"],
        category="effect_indeterminate",
        question="Is the live destination ready for OpenAdapt to check the saved result again?",
    ),
    "delivery-uncertain": _detail(
        title="The action may already have been delivered.",
        kind="delivery_uncertain",
        delivery="unknown",
        actions=["reconcile", "teach", "escalate"],
        category="effect_indeterminate",
        question="Is the live destination ready for OpenAdapt to reconcile the uncertain action?",
    ),
    "halt": _detail(
        title="The workflow stopped instead of guessing.",
        kind="halt",
        actions=["verify_and_resume", "reject", "teach", "escalate"],
        category="halt",
        question="Is the live application ready for OpenAdapt to verify and continue?",
    ),
    "optional-step": _detail(
        title="This optional workflow step needs an operator decision.",
        kind="halt",
        actions=["skip", "reject", "teach", "escalate"],
        category="halt",
        question="Should OpenAdapt leave this declared optional step undone?",
    ),
}


RESULTS: dict[str, dict[str, Any]] = {
    "verify_and_resume": {
        "state": "completed",
        "reason_code": "verified_and_resumed",
        "action": "verify_and_resume",
    },
    "skip": {"state": "completed", "reason_code": "skipped_and_resumed", "action": "skip"},
    "reject": {"state": "rejected", "reason_code": "rejected_by_operator", "action": "reject"},
    "teach": {
        "state": "demonstration_requested",
        "reason_code": "demonstration_requested",
        "action": "teach",
    },
    "escalate": {
        "state": "escalated",
        "reason_code": "escalation_recorded",
        "action": "escalate",
    },
    "reconcile": {
        "state": "completed",
        "reason_code": "reconciled_and_resumed",
        "action": "reconcile",
        "report_success": True,
        "transition_receipt_digest": "sha256:" + "d" * 64,
    },
}


def _write_fixture_site(site: Path) -> None:
    shutil.copy(SHELL / "app.js", site / "app.js")
    shutil.copy(SHELL / "styles.css", site / "styles.css")
    fixture = json.dumps({"scenarios": SCENARIOS, "results": RESULTS}, separators=(",", ":"))
    (site / "index.html").write_text(
        """<!doctype html><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">
<link rel=\"stylesheet\" href=\"/styles.css\"><body>
<header class=\"bar\"><span class=\"brand\">OpenAdapt</span><span id=\"device\" class=\"device\">Demo phone</span></header>
<main id=\"main\" class=\"main\">Loading…</main><footer id=\"actions\" class=\"actions\" hidden></footer>
<script>window.__fixtures__="""
        + fixture
        + """;
const params=new URLSearchParams(location.search); const scenario=params.get('scenario')||'identity';
const result=params.get('result')||null; const detail=window.__fixtures__.scenarios[scenario];
window.IntersectionObserver=undefined;
sessionStorage.setItem('portal_token','fixture-token'); sessionStorage.setItem('portal_csrf','fixture-csrf');
window.fetch=async (url, options={})=>{let body=null,status=404;
 if(url==='/api/portal/session'){status=200;body={device_label:'Demo phone'}}
 else if(url==='/api/portal/tasks'){status=200;body=[{id:'fixture-run',headline:detail.presentation.explanation,category:detail.task.task_kind}]}
 else if(url==='/api/portal/tasks/fixture-run'){status=200;body=detail}
 else if(String(url).includes('/actions/')){status=200;body=window.__fixtures__.results[result||'verify_and_resume']}
 return {status,ok:status<400,json:async()=>body};};
</script><script src=\"/app.js\"></script>""",
        encoding="utf-8",
    )


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


def _serve(directory: Path) -> tuple[ThreadingHTTPServer, Thread]:
    def handler(*args: object, **kwargs: object) -> _QuietHandler:
        return _QuietHandler(*args, directory=str(directory), **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _capture(out: Path) -> None:
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="openadapt-portal-fixtures-") as temp:
        site = Path(temp)
        _write_fixture_site(site)
        server, thread = _serve(site)
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
                for name, detail in SCENARIOS.items():
                    page.goto(f"{origin}/?scenario={name}", wait_until="networkidle")
                    page.locator("[data-run='fixture-run']").click()
                    page.wait_for_timeout(50)
                    # A canonical phone image is the phone viewport. A
                    # full-page capture repeats the sticky heading in the
                    # middle of long decision cards and is not what a staff
                    # member sees on a device.
                    page.screenshot(path=str(out / f"{name}.png"))
                    for action in detail["task"]["allowed_actions"]:
                        page.goto(
                            f"{origin}/?scenario={name}&result={action}", wait_until="networkidle"
                        )
                        page.locator("[data-run='fixture-run']").click()
                        page.locator(f'[data-action="{action}"]').click()
                        page.wait_for_timeout(50)
                        page.screenshot(path=str(out / f"{name}-{action}-result.png"))
                browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Output directory for generated PNG files.")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    _capture(args.out)
    print(f"Wrote synthetic phone fixtures to {args.out}")


if __name__ == "__main__":
    main()
