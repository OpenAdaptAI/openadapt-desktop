"""Only ask a person about problems: the Desktop side of the switch.

The switch lets a clinic stop approving routine items one by one. With it on,
an item saves without a person only when every check before and after the
save passes, and the record read back from the records system shows exactly
the values it was given. Everything else goes to a person in Needs you.

Who decides what:

* A clinic owner or admin turns the switch on in OpenAdapt Cloud, and the
  clinic's privacy contact confirms. Desktop never turns it on. No IPC command
  writes a switch record; :func:`accept_switch` exists for the Cloud status
  client and for tests.
* Anyone at this computer can turn it off, offline too. The switch-off goes
  into Flow's hash-chained switch log, which Flow's pre-save gate reads before
  every save, so it applies before the next save and Cloud can't undo it.
* Flow decides each item: the pre-save checks (P1 to P5), the post-run checks
  (R1 and R2) and the switch-off rules. Desktop reads Flow's PHI-free
  ``approval`` block from the run report and never upgrades it. An item counts
  as saved without a person only when that block says so, both post-run checks
  passed, and the item's transaction outcome is a consistent ``VERIFIED``.

What is local, per task version (local bundle id)::

    <data_dir>/approval_mode/<bundle_id>/switch.json      the switch Cloud sent
    <data_dir>/approval_mode/<bundle_id>/switch.log       Flow's switch log
    <data_dir>/approval_mode/<bundle_id>/turned_off.json  a switch-off made here

Nothing here holds an input value, screen content or free text. The names of
the clinic staff who turned the switch on and confirmed it are kept on this
computer only, to show who is accountable.

The switch can't take effect on this computer yet. :func:`blockers` names
every missing piece, and while any is missing Desktop shows the switch as not
used here and never says that only problems come to the person.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ENVELOPE_SCHEMA = "openadapt-desktop.approval-mode-switch/v1"
TURNED_OFF_SCHEMA = "openadapt-desktop.approval-mode-turned-off/v1"

#: Off reasons Flow's switch log can name (``openadapt_flow.approval_mode``).
OFF_REASONS = frozenset(
    {
        "uncertain_result",
        "wrong_item_found",
        "too_many_problems",
        "screen_changed",
        "record_check_lost",
        "spot_checks_overdue",
        "version_changed",
        "environment_changed",
        "admission_lost",
        "setup_lapsed",
        "turned_off",
    }
)

#: Pieces the switch needs that don't exist yet. Each one keeps the switch
#: from taking effect on this computer; remove a code only together with the
#: code that builds it.
#:
#: ``engine_cannot_check_before_save``  Flow's pre-save gate runs only when the
#:     caller hands one to the Replayer. The governed ``openadapt-flow run``
#:     that Desktop calls doesn't take one yet (Flow ``docs/APPROVAL_MODES.md``,
#:     "Not built yet"), so no Desktop run is checked before each save.
#: ``cloud_status``  Desktop has no client for Cloud's signed switch status,
#:     which Flow's P1 check needs before every save.
#: ``spot_checks``   Mandatory spot checks (design 7.7) aren't built.
#: ``list_queue``    Lists run from the window, not from an engine-held queue,
#:     so a paused list doesn't survive closing the window.
NOT_BUILT: tuple[str, ...] = (
    "engine_cannot_check_before_save",
    "cloud_status",
    "spot_checks",
    "list_queue",
)

_BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")


class SwitchApprovalError(ValueError):
    """A refused switch action. The message is safe to show."""


class _Person(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=_NAME.pattern)
    at: datetime


class _TurnedOnBy(_Person):
    role: Literal["owner", "admin"]


class SwitchEnvelope(BaseModel):
    """What Cloud sends a runner when the switch turns on.

    ``record`` is Flow's switch record, validated with Flow's own model. The
    two people are the accountable admin and the privacy contact. Their names
    are display names from the clinic's own Cloud organization.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["openadapt-desktop.approval-mode-switch/v1"] = Field(
        ENVELOPE_SCHEMA, alias="schema"
    )
    record: dict[str, Any]
    turned_on: _TurnedOnBy
    confirmed: _Person


# ---------------------------------------------------------------- Flow access


def flow_switch_api() -> dict[str, Any] | None:
    """Flow's switch-log and record API, or None when this Flow lacks it."""

    try:
        from openadapt_flow.approval_mode.model import OffReason
        from openadapt_flow.approval_mode.switch import (
            SwitchLog,
            SwitchLogError,
            SwitchRecord,
            turn_off,
        )
    except ImportError:
        return None
    return {
        "OffReason": OffReason,
        "SwitchLog": SwitchLog,
        "SwitchLogError": SwitchLogError,
        "SwitchRecord": SwitchRecord,
        "turn_off": turn_off,
    }


def blockers(*, flow_has_switch: bool) -> list[str]:
    """Why the switch can't take effect on this computer, first reason first.

    An empty list is the only case in which Desktop may show the switch as on
    and say that only problems come to the person.
    """

    found: list[str] = [] if flow_has_switch else ["engine_without_switch"]
    found.extend(NOT_BUILT)
    return found


# --------------------------------------------------------------- local store


def store_dir(data_dir: Path, bundle_id: str) -> Path:
    if not _BUNDLE_ID.fullmatch(str(bundle_id or "")):
        raise SwitchApprovalError("That task version has an unexpected id.")
    return Path(data_dir) / "approval_mode" / str(bundle_id)


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write owner-only JSON and move it into place in one step."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    data = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    temporary.replace(path)


def accept_switch(data_dir: Path, bundle_id: str, envelope: Mapping[str, Any]) -> str:
    """Store a switch Cloud turned on, and open its switch log.

    For the Cloud status client (not built yet) and for tests. No IPC command
    calls this, so nothing in Desktop can turn the switch on. A closed switch
    stays closed: storing a new one moves the old record and log aside.
    Returns the switch id.
    """

    api = flow_switch_api()
    if api is None:
        raise SwitchApprovalError("This computer's OpenAdapt engine can't keep a switch log.")
    try:
        parsed = SwitchEnvelope.model_validate(dict(envelope))
        record = api["SwitchRecord"].model_validate(parsed.record)
    except (ValidationError, ValueError) as exc:
        raise SwitchApprovalError("The switch from OpenAdapt Cloud isn't valid.") from exc
    root = store_dir(data_dir, bundle_id)
    if (root / "switch.json").exists() or (root / "switch.log").exists():
        archive = root / "closed" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive.mkdir(parents=True, exist_ok=True)
        for name in ("switch.json", "switch.log", "switch.log.lock", "turned_off.json"):
            if (root / name).exists():
                (root / name).replace(archive / name)
    payload = parsed.model_dump(mode="json", by_alias=True)
    payload["record"] = record.model_dump(mode="json")
    _write_private_json(root / "switch.json", payload)
    api["SwitchLog"].open_new(root / "switch.log", record)
    return record.switch_id


def _read_turned_off(root: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads((root / "turned_off.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != TURNED_OFF_SCHEMA:
        return None
    return raw


def _manifest_digest(bundle_dir: Path | None) -> str | None:
    """The bundle's content digest from its PHI-free manifest sidecar."""

    if bundle_dir is None:
        return None
    try:
        manifest = json.loads((Path(bundle_dir) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    digest = manifest.get("content_digest") if isinstance(manifest, dict) else None
    return digest if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) else None


def read_switch(
    data_dir: Path,
    bundle_id: str,
    *,
    bundle_dir: Path | None,
    runner_id: str | None,
    item_running: bool = False,
) -> dict[str, Any]:
    """What this computer knows about one task version's switch.

    ``state`` is one of:

    * ``none``: no switch was ever turned on for this version here.
    * ``on``: the record is valid for this version and runner, and the log
      is intact with no switch-off.
    * ``waiting_for_cloud``: on, but the last Cloud status was stale. Flow
      sends items to a person until a fresh one arrives.
    * ``off``: the switch is closed for good. ``off_reason`` says why.

    Any record, log or binding Desktop can't read or confirm reads as ``off``,
    never as ``on``.
    """

    root = store_dir(data_dir, bundle_id)
    view: dict[str, Any] = {
        "state": "none",
        "switch_id": None,
        "off_reason": None,
        "off_at": None,
        "off_by_this_computer": False,
        "turned_on": None,
        "confirmed": None,
        "evidence": None,
    }
    if not (root / "switch.json").exists() and not (root / "switch.log").exists():
        return view

    def closed(reason: str, at: Any = None, *, here: bool = False) -> dict[str, Any]:
        view.update(
            state="off",
            off_reason=reason if reason in OFF_REASONS else "uncertain_result",
            off_at=at,
            off_by_this_computer=here,
        )
        return view

    api = flow_switch_api()
    try:
        envelope = SwitchEnvelope.model_validate(
            json.loads((root / "switch.json").read_text(encoding="utf-8"))
        )
        if api is None:
            raise SwitchApprovalError("no switch API")
        record = api["SwitchRecord"].model_validate(envelope.record)
    except (OSError, ValueError, ValidationError, SwitchApprovalError):
        # An unreadable switch never counts as on.
        return closed("uncertain_result")
    view["switch_id"] = record.switch_id
    view["turned_on"] = envelope.turned_on.model_dump(mode="json")
    view["confirmed"] = envelope.confirmed.model_dump(mode="json")

    note = _read_turned_off(root)
    try:
        events = api["SwitchLog"](root / "switch.log").read()
        from openadapt_flow.approval_mode.switch import summarize_events

        state = summarize_events(events)
    except api["SwitchLogError"]:
        if note is not None:
            return closed("turned_off", note.get("at"), here=True)
        return closed("uncertain_result")
    if state.switch_record_sha256 != record.digest() or state.switch_id != record.switch_id:
        return closed("uncertain_result")

    no_person = sum(
        1 for event in state.finished if event.decision is not None
        and event.decision.value == "no_person"
    )
    view["evidence"] = {
        "switch_id": record.switch_id,
        "record_sha256": record.digest(),
        "bundle_digest": record.bundle_content_digest,
        "runner_id": record.runner_id,
        "verifiers": len(record.verifier_identities),
        "profile": record.profile,
        "log_intact": True,
        "log_events": len(events),
        "items_finished": len(state.finished),
        "items_without_person": no_person,
    }
    if state.off_reason is not None:
        off_event = next(
            (event for event in events if event.kind == "switched_off"), None
        )
        here = note is not None and state.off_reason.value == "turned_off"
        return closed(
            state.off_reason.value,
            off_event.at if off_event is not None else None,
            here=here,
        )
    if note is not None:
        # Desktop wrote its own note but the log append didn't land. Off.
        return closed("turned_off", note.get("at"), here=True)
    if state.open_items and not item_running:
        # An item started and never finished: its save may have gone through.
        return closed("uncertain_result")
    if _manifest_digest(bundle_dir) != record.bundle_content_digest:
        return closed("version_changed")
    if not runner_id or runner_id != record.runner_id:
        return closed("environment_changed")
    view["state"] = "waiting_for_cloud" if state.status_unconfirmed else "on"
    return view


def turn_off(data_dir: Path, bundle_id: str) -> dict[str, Any]:
    """Turn the switch off from this computer. Works offline.

    Writes Flow's ``switched_off`` event first, so Flow's gate refuses the
    next save without a person, then a local note of when. A broken or missing
    log already reads as off; the note still records the person's choice.
    """

    root = store_dir(data_dir, bundle_id)
    if not (root / "switch.json").exists() and not (root / "switch.log").exists():
        raise SwitchApprovalError("This task has no switch to turn off.")
    now = datetime.now(timezone.utc).isoformat()
    already_off = False
    logged = False
    api = flow_switch_api()
    if api is not None:
        log = api["SwitchLog"](root / "switch.log")
        try:
            state = log.state()
            if state.off_reason is not None:
                already_off = True
            else:
                api["turn_off"](log, api["OffReason"].TURNED_OFF, at=now)
                logged = True
        except api["SwitchLogError"]:
            logged = False
    if not already_off and _read_turned_off(root) is None:
        _write_private_json(
            root / "turned_off.json",
            {"schema": TURNED_OFF_SCHEMA, "at": now, "where": "this_computer"},
        )
    return {"ok": True, "already_off": already_off, "logged": logged, "at": now}


def stored_switch_ids(data_dir: Path) -> list[str]:
    """Bundle ids that have a switch store on this computer."""

    root = Path(data_dir) / "approval_mode"
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []
    return [
        child.name
        for child in children
        if child.is_dir()
        and _BUNDLE_ID.fullmatch(child.name)
        and ((child / "switch.json").exists() or (child / "switch.log").exists())
    ]


# ------------------------------------------------------------- requirements


def local_requirements(workflow: Any, *, certified_and_sealed: bool) -> dict[str, Any]:
    """Requirements E1 to E8 from what this computer knows.

    Flow's evaluator is the one list (design 6). Desktop knows whether the
    version is checked and sealed, and Flow reads the workflow itself for E2
    and E3. Everything Cloud holds (the admission, attestations, readiness
    sign-off, approvals) defaults to "not recorded", which can't pass, so an
    unmet requirement here means "not confirmed on this computer".
    """

    try:
        from openadapt_flow.approval_mode.requirements import (
            DeploymentEvidence,
            RequirementsEvidence,
            evaluate_requirements,
        )
    except ImportError:
        return {"available": False, "reason": "engine_without_switch", "items": []}
    evidence = RequirementsEvidence(
        deployment=DeploymentEvidence(certified_and_sealed=certified_and_sealed)
    )
    try:
        report = evaluate_requirements(workflow, evidence)
    except Exception:
        return {"available": False, "reason": "requirements_error", "items": []}
    return {
        "available": True,
        "source": "this_computer",
        "items": [item.model_dump(mode="json") for item in report.requirements],
    }


# ------------------------------------------------------- per-item evidence

_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _check(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    detail = value.get("detail", "ok")
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        return None
    if not isinstance(detail, str) or not _CODE.fullmatch(detail):
        detail = "unknown"
    return {"code": code, "passed": value.get("passed") is True, "detail": detail}


def item_approval(raw: Any) -> dict[str, Any] | None:
    """Project Flow's per-item ``approval`` block to closed codes.

    Returns None when the report has no block (the run had no approval gate).
    A block Flow's own model rejects, or one Desktop can't validate because
    this Flow lacks the model, is kept as ``unverified``: it needs a person
    and never counts as saved without one.
    """

    if raw is None:
        return None
    validated = False
    try:
        from openadapt_flow.approval_mode.model import ApprovalModeItemEvidence

        ApprovalModeItemEvidence.model_validate(raw)
        validated = True
    except ImportError:
        validated = False
    except (ValidationError, ValueError, TypeError):
        validated = False
    block = raw if isinstance(raw, dict) else {}

    def code(name: str) -> str | None:
        value = block.get(name)
        return value if isinstance(value, str) and _CODE.fullmatch(value) else None

    checks = {
        group: [c for c in (_check(v) for v in block.get(group) or []) if c is not None]
        for group in ("item_checks", "pre_checks", "post_checks")
    }
    failed = next(
        (
            check
            for group in ("item_checks", "pre_checks", "post_checks")
            for check in checks[group]
            if not check["passed"]
        ),
        None,
    )
    decision = code("decision") if validated else "unverified"
    needs_person = (block.get("needs_person") is not False) or not validated
    post = {check["code"]: check["passed"] for check in checks["post_checks"]}
    off = code("switch_off_reason")
    return {
        "validated": validated,
        "mode": code("mode"),
        "decision": decision,
        "reason": code("decision_reason"),
        "needs_person": needs_person,
        "counts_toward_problems": block.get("counts_toward_problems") is True,
        "switch_off_reason": off if off in OFF_REASONS else None,
        "failed_check": failed,
        "post_checks": {
            "verified": post.get("verified") is True,
            "whole_run_clean": post.get("whole_run_clean") is True,
        },
        "saves_without_person": (
            block.get("saves_without_person")
            if isinstance(block.get("saves_without_person"), int)
            else 0
        ),
    }


def saved_without_person(approval: Mapping[str, Any] | None, consistent_tx: str | None) -> bool:
    """True only when Flow decided ``no_person`` and every condition holds.

    The conditions are the outcome safety rule (design 8): Flow's validated
    block says ``no_person`` and doesn't need a person, R1 and R2 both passed,
    and the item's transaction outcome is a consistent ``VERIFIED``. A
    ``VERIFIED`` execution outcome with no transaction outcome never counts.
    """

    if not approval or consistent_tx != "VERIFIED":
        return False
    post = approval.get("post_checks") or {}
    return bool(
        approval.get("validated") is True
        and approval.get("mode") == "problems_only"
        and approval.get("decision") == "no_person"
        and approval.get("needs_person") is False
        and post.get("verified") is True
        and post.get("whole_run_clean") is True
    )


def parse_stored(value: Any) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = [
    "NOT_BUILT",
    "OFF_REASONS",
    "SwitchApprovalError",
    "SwitchEnvelope",
    "accept_switch",
    "blockers",
    "flow_switch_api",
    "item_approval",
    "local_requirements",
    "parse_stored",
    "read_switch",
    "saved_without_person",
    "store_dir",
    "stored_switch_ids",
    "turn_off",
]
