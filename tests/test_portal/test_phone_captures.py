"""The phone screenshots are generated, complete, and match their provenance."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from scripts import capture_portal_scenarios as capture

ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "openadapt_desktop" / "portal" / "shell" / "app.js").read_text(encoding="utf-8")
ASSETS = ROOT / "docs" / "assets" / "mobile-decision"


def test_every_receipt_the_shell_can_word_has_a_capture() -> None:
    worded = set(re.findall(r'^  "([a-z_]+/[a-z_]+)": \{', APP, re.MULTILINE))
    assert worded, "RECEIPT_COPY keys were not found"
    captured = {
        f"{r['body']['state']}/{r['body']['reason_code']}"
        for r in capture.RESULTS.values()
        if r["body"] and "state" in r["body"]
    }
    assert worded <= captured, sorted(worded - captured)


def test_every_result_example_names_a_real_scenario_and_allowed_action() -> None:
    for result, scenario, action in capture.RESULT_EXAMPLES:
        assert result in capture.RESULTS
        assert action in capture.SCENARIOS[scenario]["task"]["allowed_actions"], (result, action)


def test_fixtures_carry_no_free_text_receipt() -> None:
    """Flow's receipt has no message field; a fixture must not invent one."""
    for result in capture.RESULTS.values():
        body = result["body"] or {}
        assert "message" not in body


def test_docs_assets_match_their_provenance() -> None:
    provenance = json.loads((ASSETS / "provenance.json").read_text(encoding="utf-8"))
    listed = {entry["file"]: entry for entry in provenance["captures"]}
    on_disk = {path.name for path in ASSETS.glob("*.png")}
    assert set(listed) == on_disk
    for name, entry in listed.items():
        digest = hashlib.sha256((ASSETS / name).read_bytes()).hexdigest()
        assert digest == entry["sha256"], name
        assert entry["kind"] in {"shell", "preview"}
        assert entry["color_scheme"] in {"light", "dark"}
    assert provenance["source_frame"]["sha256"] == capture.PUBLIC_FRAME_SHA256
    # Every reviewed screen exists in both schemes.
    for name in capture.DOCS_SET:
        assert f"{name}.png" in listed and f"{name}-dark.png" in listed, name


def test_previews_are_labelled_as_previews() -> None:
    provenance = json.loads((ASSETS / "provenance.json").read_text(encoding="utf-8"))
    for entry in provenance["captures"]:
        if entry["source_output"].split("/", 1)[1].startswith("preview-"):
            assert entry["kind"] == "preview"
            assert "preview" in entry["state"].lower()


def test_save_fixtures_are_what_flow_serves() -> None:
    """A save approval is Flow's signed task with exactly its two answers."""
    for name, detail in capture.SAVES.items():
        task = detail["task"]
        assert task["task_kind"] == "save_approval", name
        assert task["allowed_actions"] == ["verify_and_resume", "reject"], name
        assert set(detail["presentation"]["decline_reasons"]) <= {
            "wrong_values",
            "wrong_record",
            "screen_problem",
        }
    for result in capture.SAVE_RESULTS.values():
        assert "message" not in (result["body"] or {})


def test_save_approval_screens_are_real_shell_states() -> None:
    """Saves are served by the portal now: no screen of them is a preview."""
    provenance = json.loads((ASSETS / "provenance.json").read_text(encoding="utf-8"))
    for entry in provenance["captures"]:
        if entry["file"].startswith("save-"):
            assert entry["kind"] == "shell", entry["file"]
