#!/usr/bin/env python3
# ruff: noqa: E501
"""Capture deterministic, synthetic phone decision screens.

This is a presentation-fixture generator. It starts no runner, reads no run
directory, and never uses customer evidence. It serves the shipped phone shell
(``openadapt_desktop/portal/shell``) unchanged, replaces only the network with
synthetic replies, and photographs every state at 390 CSS pixels in light and
dark mode.

Two screens are *card previews*: a business-decision choice and a save
approval. The Desktop portal does not serve those decision kinds yet (Cloud
answers them today). They are drawn with the shell's own ``cardMarkup``,
``answerMarkup`` and ``resultMarkup`` so every surface can be compared with one
card, and provenance marks them ``preview``.

Examples:
    uv run --extra build python scripts/capture_portal_scenarios.py \
      --out /tmp/openadapt-desktop-phone-fixtures \
      --frame /path/to/public-reference-run-frame.png

    # Regenerate the reviewed docs assets and their provenance.
    uv run --extra build python scripts/capture_portal_scenarios.py \
      --out /tmp/openadapt-desktop-phone-fixtures \
      --frame /path/to/openemr-source-recording.poster.png --docs
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "openadapt_desktop" / "portal" / "shell"
DOCS_ASSETS = ROOT / "docs" / "assets" / "mobile-decision"

#: The public synthetic OpenEMR poster the reviewed docs assets are made from.
PUBLIC_FRAME_SHA256 = "5d1f793fcbff2415a83ec894fecb45f6e129402731344b79216b2bba43b88b10"
PUBLIC_FRAME_SOURCE = {
    "repository": "https://github.com/OpenAdaptAI/openadapt-web",
    "commit": "36f643369c9b86c96b52ecdfe7f4a1c8b977cd08",
    "path": "public/reference/openemr-patient-registration-standard-synthetic-v1/recording/openemr-source-recording.poster.png",
    "sha256": PUBLIC_FRAME_SHA256,
    "data_boundary": "Public synthetic OpenEMR reference data. No customer data.",
}

#: A fixed clock, so ages, expiry and receipt times are the same on every run.
FIXED_NOW = "2026-07-29T12:14:00Z"
TIMEZONE = "America/Toronto"
VIEWPORT = {"css_width": 390, "css_height": 844, "device_scale_factor": 2}

# --------------------------------------------------------------- fixtures


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _task(kind: str, delivery: str, actions: list[str], risk: str, expires: str, evidence: dict[str, Any]) -> dict[str, Any]:
    base = {
        "identity_required_count": None,
        "identity_confirmed_count": None,
        "effect_required_count": None,
        "effect_confirmed_count": None,
        "minimum_effect_tier": None,
        "observed_effect_tier": None,
        "frame_available_locally": False,
        "sensitive_evidence_local_only": True,
    }
    base.update(evidence)
    return {
        "capability_digest": _digest("a"),
        "signature": "hmac-sha256:" + "b" * 64,
        "delivery_state": delivery,
        "task_kind": kind,
        "risk_class": risk,
        "created_at": "2026-07-29T12:00:00+00:00",
        "expires_at": expires,
        "allowed_actions": actions,
        "evidence": base,
    }


def _detail(
    *,
    kind: str,
    category: str,
    question: str,
    explanation: str,
    actions: list[str],
    delivery: str = "not_delivered",
    risk: str = "unknown",
    action_kind: str = "click",
    role: str = "button",
    label: str | None = "Save",
    recheck: tuple[str, ...] = ("record_identity", "target_resolution"),
    ladder: list[dict[str, str]] | None = None,
    expires: str = "2026-07-29T13:00:00+00:00",
    **evidence: Any,
) -> dict[str, Any]:
    return {
        "task": _task(kind, delivery, actions, risk, expires, evidence),
        "task_digest": _digest("c"),
        "presentation": {
            "question": question,
            "explanation": explanation,
            "halt": {
                "category": category,
                "step_ordinal": 3,
                "step_count": 6,
                "action_kind": action_kind,
                "target_role": role,
                "target_label": label,
                "target_label_withheld": label is None,
                "resolution_ladder": ladder or [],
                "will_recheck": [{"check": check, "count": None} for check in recheck],
            },
        },
    }


_LADDER = [
    {"rung": "structural", "evidence": "recorded", "verdict": "failed"},
    {"rung": "template", "evidence": "recorded", "verdict": "failed"},
    {"rung": "ocr", "evidence": "recorded", "verdict": "failed"},
    {"rung": "geometry", "evidence": "absent", "verdict": "not_attempted"},
]
_FULL = ["verify_and_resume", "reject", "teach", "escalate"]

#: One request per operator request type Flow emits, plus the stakes variants.
SCENARIOS: dict[str, dict[str, Any]] = {
    "identity": _detail(
        kind="identity",
        category="identity",
        question="Is the intended record now open in the live application?",
        explanation="OpenAdapt could not confirm the intended record.",
        actions=_FULL,
        identity_required_count=2,
        identity_confirmed_count=1,
    ),
    "ambiguity": _detail(
        kind="ambiguity",
        category="disambiguation",
        question="Can you leave one clear target ready in the live application?",
        explanation="More than one target matched, so OpenAdapt chose none.",
        actions=_FULL,
        recheck=("target_resolution", "postconditions"),
        ladder=_LADDER,
    ),
    "not-found": _detail(
        kind="ambiguity",
        category="resolution",
        question="Can you prepare one unambiguous target in the live application?",
        explanation="The compiled target could not be found on the screen.",
        actions=_FULL,
        label="Open",
        recheck=("target_resolution", "postconditions"),
        ladder=_LADDER,
    ),
    "human-step": _detail(
        kind="human_step",
        category="human_required",
        question="Have you completed the sign-in or challenge in the live application?",
        explanation="The application needs a person before the workflow can continue.",
        actions=["verify_and_resume", "escalate"],
        action_kind="type",
        role="textbox",
        label=None,
        recheck=("postconditions",),
    ),
    "effect": _detail(
        kind="effect",
        category="effect_indeterminate",
        question="Is the live destination ready for OpenAdapt to check the saved result again?",
        explanation="The saved system-of-record result was not confirmed.",
        actions=_FULL,
        risk="consequential",
        recheck=("system_of_record_effects",),
        effect_required_count=1,
        effect_confirmed_count=0,
        minimum_effect_tier=1,
    ),
    "delivery-uncertain": _detail(
        kind="delivery_uncertain",
        category="effect_indeterminate",
        question="Is the live destination ready for OpenAdapt to reconcile the uncertain action?",
        explanation="The action may already have been delivered.",
        actions=["reconcile", "teach", "escalate"],
        delivery="unknown",
        risk="consequential",
        recheck=("delivery_reconciliation",),
        effect_required_count=1,
        effect_confirmed_count=0,
    ),
    "optional-step": _detail(
        kind="halt",
        category="unmet_guard",
        question="Should OpenAdapt leave this declared optional step undone?",
        explanation="This optional workflow step needs an operator decision.",
        actions=["skip", "reject", "teach", "escalate"],
        recheck=("postconditions",),
    ),
    "irreversible": _detail(
        kind="identity",
        category="identity",
        question="Is the intended record now open in the live application?",
        explanation="OpenAdapt could not confirm the intended record before a step that cannot be undone.",
        actions=_FULL,
        risk="irreversible",
        label="Submit",
        recheck=("record_identity", "postconditions"),
        identity_required_count=2,
        identity_confirmed_count=1,
    ),
    "expired-request": _detail(
        kind="identity",
        category="identity",
        question="Is the intended record now open in the live application?",
        explanation="OpenAdapt could not confirm the intended record.",
        actions=_FULL,
        expires="2026-07-29T12:10:00+00:00",
    ),
}


def _receipt(state: str, reason: str, action: str = "verify_and_resume", **extra: Any) -> dict[str, Any]:
    return {"state": state, "reason_code": reason, "action": action, **extra}


#: Runner replies. ``status`` 0 means the connection dropped.
RESULTS: dict[str, dict[str, Any]] = {
    "checks-passed": {"status": 200, "body": _receipt("completed", "verified_and_resumed")},
    "checks-failed": {"status": 200, "body": _receipt("refused", "revalidation_refused")},
    "paused-again": {"status": 200, "body": _receipt("halted", "continuation_halted")},
    "expired": {"status": 200, "body": _receipt("expired", "expired")},
    "delivery-uncertain": {"status": 202, "body": _receipt("delivery_uncertain", "delivery_uncertain")},
    "rejected": {"status": 200, "body": _receipt("rejected", "rejected_by_operator", "reject")},
    "escalated": {"status": 200, "body": _receipt("escalated", "escalation_recorded", "escalate")},
    "teach": {"status": 200, "body": _receipt("demonstration_requested", "demonstration_requested", "teach")},
    "skipped": {"status": 200, "body": _receipt("completed", "skipped_and_resumed", "skip")},
    "reconciled": {
        "status": 200,
        "body": _receipt("completed", "reconciled_and_resumed", "reconcile", report_success=True, transition_receipt_digest=_digest("d")),
    },
    "reconcile-incomplete": {
        "status": 200,
        "body": _receipt("completed", "reconciled_and_resumed", "reconcile", report_success=False, transition_receipt_digest=None),
    },
    "reconcile-not-proved": {"status": 200, "body": _receipt("refused", "revalidation_refused", "reconcile")},
    # A refusal with no receipt, after which the decision is gone.
    "superseded": {"status": 409, "body": {"detail": "the attention item is no longer current; reload the queue"}},
    # A refusal with no receipt while the same decision is still open.
    "not-accepted": {"status": 409, "body": {"detail": "attended capability expired"}},
    "unknown": {"status": 200, "body": _receipt("a_future_state", "a_future_reason")},
    "network-lost": {"status": 0, "body": None},
    "pending": {"status": 200, "body": _receipt("accepted_pending_runner", "pending_runner")},
}

#: (result, scenario, action)
RESULT_EXAMPLES: list[tuple[str, str, str]] = [
    ("checks-passed", "identity", "verify_and_resume"),
    ("checks-failed", "identity", "verify_and_resume"),
    ("paused-again", "identity", "verify_and_resume"),
    ("expired", "identity", "verify_and_resume"),
    ("delivery-uncertain", "identity", "verify_and_resume"),
    ("rejected", "identity", "reject"),
    ("escalated", "identity", "escalate"),
    ("teach", "identity", "teach"),
    ("skipped", "optional-step", "skip"),
    ("reconciled", "delivery-uncertain", "reconcile"),
    ("reconcile-incomplete", "delivery-uncertain", "reconcile"),
    ("reconcile-not-proved", "delivery-uncertain", "reconcile"),
    ("superseded", "identity", "verify_and_resume"),
    ("not-accepted", "identity", "verify_and_resume"),
    ("unknown", "identity", "verify_and_resume"),
    ("network-lost", "identity", "verify_and_resume"),
    ("pending", "identity", "verify_and_resume"),
]

LIST = [
    {"id": "fixture-run", "headline": "OpenAdapt could not confirm the intended record.", "category": "identity"},
    {"id": "fixture-run-2", "headline": "The saved system-of-record result was not confirmed.", "category": "effect"},
    {"id": "fixture-run-3", "headline": "The application needs a person before the workflow can continue.", "category": "human_required"},
]

SHELL_STATES = ["pair-scan", "pair-code", "pair-failed", "pair-not-approved", "empty", "list", "unavailable", "offline"]

# The synthetic transport. It answers exactly the routes the shell calls.
_STUB = r"""<script>
window.__fx = %FIXTURES%;
(function () {
  const p = new URLSearchParams(location.search);
  const state = p.get('state') || 'task';
  const scenario = p.get('scenario') || 'identity';
  const result = p.get('result') || 'checks-passed';
  if (!state.startsWith('pair')) {
    sessionStorage.setItem('portal_token', 'fixture-token');
    sessionStorage.setItem('portal_csrf', 'fixture-csrf');
  }
  if (['pair-code', 'pair-failed', 'pair-not-approved'].includes(state) && !location.hash) {
    history.replaceState(null, '', location.pathname + location.search + '#c=fixture-secret');
  }
  const nativeFetch = window.fetch.bind(window);
  let gone = false;
  window.fetch = async (url) => {
    url = String(url);
    const reply = (status, body) => ({
      status, ok: status >= 200 && status < 300,
      json: async () => { if (body === undefined) throw new Error('no body'); return body; },
    });
    if (url.includes('/evidence?')) {
      if (!window.__frame) return reply(404, null);
      return nativeFetch('/' + window.__frame);
    }
    if (state === 'offline') throw new TypeError('Failed to fetch');
    if (url === '/api/portal/pair/claim') {
      if (state === 'pair-failed') return reply(409, { message: 'This code was already used. Show a new code on the computer running OpenAdapt.', reason: 'already_claimed' });
      return reply(200, { session_token: 'fixture-token', csrf_token: 'fixture-csrf', confirm_code: '482 913' });
    }
    if (url === '/api/portal/session') {
      if (state === 'pair-code') return reply(202, { reason: 'pending_approval' });
      if (state === 'pair-not-approved') return reply(401, {});
      return reply(200, { device_label: 'Phone' });
    }
    if (url === '/api/portal/tasks') {
      if (state === 'empty' || state === 'preview') return reply(200, []);
      if (state === 'unavailable') return reply(503, { message: 'The local OpenAdapt decision service is not reachable.' });
      if (state === 'list') return reply(200, __fx.list);
      const d = __fx.scenarios[scenario];
      return reply(200, [{ id: 'fixture-run', headline: d.presentation.explanation, category: d.task.task_kind }]);
    }
    if (url.includes('/actions/')) {
      if (state === 'submitting') return new Promise(() => {});
      const r = __fx.results[result];
      if (result === 'superseded') gone = true;
      if (r.status === 0) throw new TypeError('Failed to fetch');
      return reply(r.status, r.body);
    }
    if (url.startsWith('/api/portal/tasks/')) {
      if (gone) return reply(404, { detail: 'no open attention item' });
      return reply(200, __fx.scenarios[scenario]);
    }
    return reply(404, null);
  };
})();
</script>
"""

# Card previews for decision kinds the Desktop portal does not serve yet. They
# use the shipped shell's own markup functions and stylesheet, and they never
# call the network: an option tap renders the receipt shape directly.
_PREVIEWS = r"""
window.__preview = function (kind, phase) {
  const P = {
    business: {
      eyebrow: ['Workflow choice', 'Operations lead'],
      question: 'Which approved review path should own this exception?',
      why: 'The written rule has no exact match, and two approved paths remain. The workflow keeps this choice with a person.',
      context: [['Written rule', 'No exact match'], ['Local practice', 'Two approved paths remain']],
      next: 'Whichever you choose, OpenAdapt checks the live application before it enters that branch. If the check fails, it stops and does nothing.',
      options: [
        ['priority_review', 'Priority review', 'Routes the case to the priority queue.'],
        ['standard_review', 'Standard review', 'Routes the case to the standard queue.'],
        ['supervisor', 'Supervisor', 'Holds the case for a supervisor.'],
      ],
      primary: null,
      result: {
        tone: 'pending', mark: 'pending', label: 'Answer recorded', title: 'Checking the live application',
        text: 'Your choice is signed and stored. OpenAdapt checks the live application before it follows the Priority review branch. This is not yet a result.',
        rows: [['Your answer', 'Priority review'], ['Answered', 'On this phone · 8:14 AM'], ['Role', 'Operations lead']],
      },
    },
    save: {
      eyebrow: ['Approve a save', 'Step 5 of 6'],
      question: 'Save this form?',
      why: 'OpenAdapt filled the form and is waiting before it saves. It saves exactly what is shown here, once.',
      frame: true,
      next: 'If you approve, OpenAdapt reads the form again and saves only if nothing changed. Then it checks the record.',
      options: [
        ['approve', 'Approve and save', 'Saves exactly this form, once.'],
        ['decline', "Don't save", 'Stops this item before the save.'],
      ],
      primary: 'approve',
      result: {
        tone: 'success', label: 'Approved', title: 'Approved. Saving now',
        text: 'OpenAdapt reads the form again before it saves. If anything changed while you looked, it stops and saves nothing.',
        rows: [['Your answer', 'Approve and save'], ['Decided by', 'You, on this phone'], ['Sign-in', 'Two-factor'], ['Answered', '8:14 AM']],
      },
    },
  }[kind];
  if (phase === 'result') {
    render(`<section class="result ${P.result.tone} terminal" id="outcome">${resultMarkup(P.result)}</section>`);
    return;
  }
  const context = P.context
    ? `<dl class="receipt">${P.context.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>`
    : '';
  const evidence = P.frame
    ? frameBlock({ created_at: '2026-07-29T12:13:00+00:00' }, { after_artifact_id: 'preview-frame' })
    : context;
  render(cardMarkup({ eyebrow: P.eyebrow, question: P.question, why: P.why, evidence, next: nextMarkup({ lead: esc(P.next) }) }));
  const age = document.querySelector('.evidence-age');
  if (age) age.textContent = 'The form as it will be saved';
  const image = document.getElementById('frame');
  if (image) loadFrame('fixture-run', image);
  wireEvidence();
  actionBar.innerHTML = answerMarkup(
    P.options.map(([id, label, brief]) => ({ attr: 'data-option', id, label, brief, primary: id === P.primary })),
    { heading: 'Your answer · closes in 46 minutes' },
  );
  actionBar.hidden = false;
};
"""

PREVIEWS = ["business", "save"]


def all_fixtures() -> dict[str, Any]:
    return copy.deepcopy({"scenarios": SCENARIOS, "results": RESULTS, "list": LIST})


def _write_fixture_site(site: Path, frame: Path | None) -> None:
    for name in ("app.js", "styles.css", "manifest.webmanifest"):
        shutil.copy(SHELL / name, site / name)
    fixtures = all_fixtures()
    frame_name = None
    if frame is not None:
        frame_name = f"evidence{frame.suffix.lower()}"
        shutil.copy(frame, site / frame_name)
        for detail in fixtures["scenarios"].values():
            detail["task"]["evidence"]["frame_available_locally"] = True
            detail["presentation"]["after_artifact_id"] = "openemr-retained-frame"
    stub = _STUB.replace("%FIXTURES%", json.dumps(fixtures, separators=(",", ":")))
    stub = stub.replace("window.__fx =", f"window.__frame = {json.dumps(frame_name)}; window.__fx =", 1)
    html = (SHELL / "index.html").read_text(encoding="utf-8")
    marker = '<script src="/app.js" defer></script>'
    if marker not in html:
        raise SystemExit("the shell's index.html no longer loads /app.js the expected way")
    # The real page, with the transport replaced before app.js runs and the
    # preview helpers loaded after it.
    html = html.replace(marker, stub + '<script src="/app.js"></script>\n<script src="/previews.js"></script>')
    (site / "index.html").write_text(html, encoding="utf-8")
    (site / "previews.js").write_text(_PREVIEWS, encoding="utf-8")


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


def _capture(out: Path, frame: Path | None, schemes: list[str]) -> list[dict[str, str]]:
    """Write every state for each colour scheme. Returns what was written."""
    from playwright.sync_api import sync_playwright

    written: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="openadapt-portal-fixtures-") as temp:
        site = Path(temp)
        _write_fixture_site(site, frame)
        server, thread = _serve(site)
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                for scheme in schemes:
                    context = browser.new_context(
                        viewport={"width": VIEWPORT["css_width"], "height": VIEWPORT["css_height"]},
                        device_scale_factor=VIEWPORT["device_scale_factor"],
                        color_scheme=scheme,
                        reduced_motion="reduce",
                        service_workers="block",
                        timezone_id=TIMEZONE,
                        locale="en-US",
                    )
                    page = context.new_page()
                    page.clock.set_fixed_time(FIXED_NOW)
                    target = out / scheme
                    target.mkdir(parents=True, exist_ok=True)

                    def shot(name: str, state: str, *, full: bool = False, _t: Path = target, _p: Any = page, _s: str = scheme) -> None:
                        path = _t / f"{name}.png"
                        _p.screenshot(path=str(path), full_page=full)
                        written.append({"file": f"{_s}/{name}.png", "state": state, "scheme": _s})

                    def go(query: str, _p: Any = page) -> None:
                        _p.goto(f"{origin}/?{query}", wait_until="networkidle")
                        _p.wait_for_timeout(120)

                    def settle(_p: Any = page) -> None:
                        row = _p.locator("[data-run]")
                        if row.count():
                            row.first.click()
                        _p.wait_for_timeout(120)
                        _p.wait_for_function(
                            "() => { const f = document.querySelector('#frame'); return !f || (f.complete && f.naturalWidth > 0); }"
                        )

                    def answer(attr: str, value: str, _p: Any = page) -> None:
                        _p.locator(f'[{attr}="{value}"]').first.click()
                        _p.wait_for_timeout(200)

                    def frame_result(_p: Any = page) -> None:
                        _p.evaluate(
                            """() => { const o = document.getElementById('outcome');
                              if (!o || o.classList.contains('terminal')) { window.scrollTo(0, 0); return; }
                              const answers = document.getElementById('actions');
                              const top = o.getBoundingClientRect().top + window.scrollY;
                              window.scrollTo(0, Math.max(0, top - 24)); }"""
                        )
                        _p.wait_for_timeout(80)

                    for state in SHELL_STATES:
                        go(f"state={state}")
                        page.wait_for_timeout(250)
                        shot(state, f"Shell state: {state}")
                    for name in SCENARIOS:
                        go(f"state=task&scenario={name}")
                        settle()
                        shot(f"request-{name}", f"Request: {name}")
                        shot(f"request-{name}-full", f"Request: {name}, whole page", full=True)
                    go("state=task&scenario=identity")
                    settle()
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    context.set_offline(True)
                    page.wait_for_timeout(150)
                    shot("offline-during-decision", "Connection lost while a decision is open")
                    context.set_offline(False)
                    page.evaluate("window.scrollTo(0, 0)")
                    page.locator("#evidence-open").click()
                    page.wait_for_timeout(150)
                    shot("evidence-zoomed", "Retained frame enlarged")
                    go("state=submitting&scenario=identity")
                    settle()
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    answer("data-action", "verify_and_resume")
                    shot("submitting", "Answer sent, waiting for the runner")
                    for result, scenario, action in RESULT_EXAMPLES:
                        go(f"state=task&scenario={scenario}&result={result}")
                        settle()
                        answer("data-action", action)
                        frame_result()
                        shot(f"result-{result}", f"Result: {result} after {action}")
                    for kind in PREVIEWS:
                        for phase in ("request", "result"):
                            go("state=preview")
                            page.evaluate(f"window.__preview('{kind}', '{phase}')")
                            page.wait_for_timeout(150)
                            settle()
                            shot(f"preview-{kind}-{phase}", f"Card preview ({kind}, {phase}); not served by the Desktop portal yet")
                    context.close()
                browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
    return written


#: The reviewed set kept in docs/assets/mobile-decision/, and the capture each
#: one is copied from. Every one exists in light and dark.
DOCS_SET: dict[str, tuple[str, str]] = {
    "request": ("request-identity", "Halt request: record identity, before an answer"),
    "request-irreversible": ("request-irreversible", "Halt request before a step that cannot be undone: no answer is emphasised"),
    "submitting": ("submitting", "Answer sent, waiting for the runner"),
    "result-checks-passed": ("result-checks-passed", "Runner receipt after the live re-check passed"),
    "result-checks-failed": ("result-checks-failed", "Live re-check did not pass; the request stays answerable"),
    "result-expired": ("request-expired-request", "The request expired before an answer"),
    "result-superseded": ("result-superseded", "The decision was answered elsewhere or the run moved on"),
    "pairing": ("pair-code", "Pairing: the confirmation code only this phone can see"),
    "offline": ("offline-during-decision", "Connection lost: answers are off and nothing is queued"),
    "business-decision": ("preview-business-request", "Card preview: a business-decision choice (served by Cloud today)"),
    "save-approval": ("preview-save-request", "Card preview: a save approval (served by Cloud today)"),
}


def _png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path} is not a PNG")
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _write_docs(out: Path, schemes: list[str]) -> None:
    DOCS_ASSETS.mkdir(parents=True, exist_ok=True)
    for stale in DOCS_ASSETS.glob("*.png"):
        stale.unlink()
    captures = []
    for name, (source, state) in DOCS_SET.items():
        for scheme in schemes:
            filename = f"{name}.png" if scheme == "light" else f"{name}-{scheme}.png"
            target = DOCS_ASSETS / filename
            shutil.copyfile(out / scheme / f"{source}.png", target)
            width, height = _png_size(target)
            captures.append(
                {
                    "file": filename,
                    "source_output": f"{scheme}/{source}.png",
                    "state": state,
                    "color_scheme": scheme,
                    "kind": "preview" if source.startswith("preview-") else "shell",
                    "width": width,
                    "height": height,
                    "sha256": _sha256(target),
                }
            )
    provenance = {
        "schema_version": "openadapt.desktop-phone-capture-provenance/v1",
        "desktop": {
            "repository": "https://github.com/OpenAdaptAI/openadapt-desktop",
            "commit": _commit(),
            "generator": "scripts/capture_portal_scenarios.py",
            "command": "uv run --extra build python scripts/capture_portal_scenarios.py --out /tmp/openadapt-desktop-phone-current --frame <public-openemr-poster> --docs",
            "viewport": VIEWPORT,
            "fixed_clock": FIXED_NOW,
            "timezone": TIMEZONE,
        },
        "source_frame": PUBLIC_FRAME_SOURCE,
        "captures": captures,
        "semantic_scope": (
            "Deterministic presentation fixtures for the Desktop phone shell. They are not a customer run "
            "or a complete workflow effect-verification result. Captures marked kind=preview draw a decision "
            "kind the Desktop portal does not serve yet with the shell's own card markup."
        ),
    }
    (DOCS_ASSETS / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="Output directory for generated PNG files.")
    parser.add_argument("--frame", type=Path, help="Optional retained PNG, JPEG, or WebP frame from a public reference run.")
    parser.add_argument("--schemes", default="light,dark", help="Comma-separated colour schemes (default: light,dark).")
    parser.add_argument("--docs", action="store_true", help="Also refresh docs/assets/mobile-decision and its provenance.")
    args = parser.parse_args()
    schemes = [scheme.strip() for scheme in args.schemes.split(",") if scheme.strip()]
    if not set(schemes) <= {"light", "dark"}:
        parser.error("--schemes accepts light and dark")
    if args.frame is not None:
        if not args.frame.is_file():
            parser.error(f"--frame does not exist: {args.frame}")
        if args.frame.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            parser.error("--frame must be PNG, JPEG, or WebP")
    if args.docs and (args.frame is None or _sha256(args.frame) != PUBLIC_FRAME_SHA256):
        parser.error("--docs needs --frame to be the public synthetic OpenEMR poster named in provenance")
    args.out.mkdir(parents=True, exist_ok=True)
    written = _capture(args.out, args.frame, schemes)
    (args.out / "index.json").write_text(json.dumps(written, indent=2) + "\n", encoding="utf-8")
    if args.docs:
        _write_docs(args.out, schemes)
        print(f"Refreshed {DOCS_ASSETS.relative_to(ROOT)}")
    print(f"Wrote {len(written)} synthetic phone fixtures to {args.out}")


if __name__ == "__main__":
    main()
