"""Tests for the hosted push + report_break egress verbs."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from engine import hosted
from engine.backends.protocol import UploadResult
from engine.flow_bridge import FlowResult
from engine.hosted import PhiBoundaryError, report_break, zip_dir


class _StubBackend:
    name = "hosted_ingest"

    def __init__(self, result: UploadResult) -> None:
        self._result = result
        self.uploaded: Path | None = None
        self.metadata: dict | None = None

    def upload(self, archive_path: Path, metadata: dict) -> UploadResult:
        self.uploaded = archive_path
        self.metadata = metadata
        return self._result


class TestZipDir:
    def test_zips_recursively(self, tmp_path: Path) -> None:
        src = tmp_path / "rec"
        (src / "frames").mkdir(parents=True)
        (src / "meta.json").write_text("{}")
        (src / "frames" / "0001.png").write_bytes(b"x")
        out = zip_dir(src)
        assert out.suffix == ".zip"
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        assert "meta.json" in names
        assert "frames/0001.png" in names

    def test_refuses_symlink_members(self, tmp_path: Path) -> None:
        src = tmp_path / "rec"
        outside = tmp_path / "outside"
        src.mkdir()
        outside.write_bytes(b"raw-secret")
        (src / "escape").symlink_to(outside)

        with pytest.raises(ValueError, match="symlink"):
            zip_dir(src)


class TestPush:
    def test_push_success_persists_workflow_id(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from engine.db import IndexDB

        rec = tmp_path / "rec1"
        rec.mkdir()
        (rec / "meta.json").write_text("{}")

        db = IndexDB(tmp_path / "index.db")
        db.initialize()
        db.insert_bundle("bnd1", str(rec), capture_id="rec1")

        monkeypatch.setattr(
            hosted,
            "_push_via_flow",
            lambda *args, **kwargs: {
                "success": True,
                "workflow_id": "wf_1",
                "dashboard_url": "https://app/dashboard/workflows/wf_1",
                "error": "",
            },
        )
        result = hosted.push(
            rec,
            kind="recording",
            host="https://app",
            db=db,
            bundle_id="bnd1",
        )
        assert result["success"] is True
        assert result["workflow_id"] == "wf_1"
        assert db.get_bundle("bnd1")["workflow_id"] == "wf_1"
        db.close()

    def test_push_default_latest_recording(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recordings = tmp_path / "recordings"
        (recordings / "old").mkdir(parents=True)
        (recordings / "new").mkdir()
        (recordings / "new" / "meta.json").write_text("{}")
        # Make "new" the most recent.
        import os
        import time

        os.utime(recordings / "new", (time.time() + 10, time.time() + 10))

        selected: dict[str, Path] = {}

        def push_via_flow(path: Path, **_kwargs):
            selected["path"] = path
            return {
                "success": True,
                "workflow_id": "wf_x",
                "dashboard_url": "",
                "error": "",
            }

        monkeypatch.setattr(hosted, "_push_via_flow", push_via_flow)
        result = hosted.push(None, recordings_dir=recordings, host="https://app")
        assert result["success"] is True
        assert selected["path"] == recordings / "new"

    def test_push_missing_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            hosted.push(tmp_path / "nope")

    def test_direct_backend_bypass_fails_without_upload(self, tmp_path: Path) -> None:
        rec = tmp_path / "rec"
        rec.mkdir()
        backend = _StubBackend(UploadResult(success=True))

        result = hosted.push(rec, backend=backend, prefer_flow=False)

        assert result["success"] is False
        assert "Direct Desktop ingest is disabled" in result["error"]
        assert backend.uploaded is None

    def test_missing_flow_push_never_falls_back_to_direct_ingest(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rec = tmp_path / "rec"
        rec.mkdir()
        monkeypatch.setattr(
            hosted,
            "_push_via_flow",
            lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("flow")),
        )

        result = hosted.push(rec)

        assert result["success"] is False
        assert result["workflow_id"] == ""
        assert result["delivery_uncertain"] is True
        assert "Do not retry blindly" in result["error"]

    def test_flow_review_pause_is_not_reported_as_upload_success(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rec = tmp_path / "rec"
        rec.mkdir()
        derivative = tmp_path / "sanitized" / "artifact-abc"
        output = (
            f"Sanitized derivative created at {derivative}.\n"
            "Upload paused for local review; the original was not modified or uploaded.\n"
            f"openadapt-flow review-sanitized {derivative} --original {rec}\n"
        )
        monkeypatch.setattr(
            "engine.hosted.FlowBridge.push",
            lambda *args, **kwargs: FlowResult(ok=True, returncode=0, stdout=output),
        )

        result = hosted.push(rec)

        assert result["success"] is False
        assert result["pending_review"] is True
        assert result["sanitized_path"] == str(derivative)
        assert result["workflow_id"] == ""

    def test_desktop_credential_reaches_flow_only_through_environment(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rec = tmp_path / "rec"
        rec.mkdir()
        calls: list[dict] = []
        monkeypatch.setattr(
            hosted,
            "active_credential",
            lambda: {
                "host": "https://app.openadapt.ai",
                "token": "stored-secret",
                "org_id": "org-1",
            },
        )

        def fake_push(*_args, **kwargs):
            calls.append(kwargs)
            return FlowResult(
                ok=True,
                returncode=0,
                stdout=(
                    "Pushed. workflow_id=123e4567-e89b-12d3-a456-426614174000 "
                    "(name='Example', kind=recording, compile=ok).\n"
                ),
            )

        monkeypatch.setattr("engine.hosted.FlowBridge.push", fake_push)

        result = hosted.push(rec)

        assert result["success"] is True
        assert calls[0]["token"] is None
        assert calls[0]["env_overrides"] == {
            "OPENADAPT_INGEST_TOKEN": "stored-secret"
        }

    def test_credential_for_another_host_is_not_forwarded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rec = tmp_path / "rec"
        rec.mkdir()
        calls: list[dict] = []
        monkeypatch.setattr(
            hosted,
            "active_credential",
            lambda: {"host": "https://other.example", "token": "wrong-host-secret"},
        )
        monkeypatch.setattr(
            "engine.hosted.FlowBridge.push",
            lambda *_args, **kwargs: (
                calls.append(kwargs)
                or FlowResult(ok=False, returncode=1, stdout="Not logged in")
            ),
        )

        hosted.push(rec, host="https://app.openadapt.ai")

        assert calls[0]["env_overrides"] is None

    def test_flow_success_requires_and_parses_hosted_workflow_identity(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rec = tmp_path / "rec"
        rec.mkdir()
        workflow_id = "123e4567-e89b-12d3-a456-426614174000"
        output = (
            f"Pushed. workflow_id={workflow_id} (name='Example', kind=recording, compile=ok).\n"
            f"Dashboard: https://app.openadapt.ai/dashboard/workflows/{workflow_id}\n"
        )
        monkeypatch.setattr(
            "engine.hosted.FlowBridge.push",
            lambda *args, **kwargs: FlowResult(ok=True, returncode=0, stdout=output),
        )

        result = hosted.push(rec)

        assert result["success"] is True
        assert result["workflow_id"] == workflow_id
        assert result["dashboard_url"].endswith(workflow_id)

    def test_flow_exit_zero_without_identity_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rec = tmp_path / "rec"
        rec.mkdir()
        monkeypatch.setattr(
            "engine.hosted.FlowBridge.push",
            lambda *args, **kwargs: FlowResult(ok=True, returncode=0, stdout="Pushed."),
        )

        result = hosted.push(rec)

        assert result["success"] is False
        assert "without an authenticated hosted workflow identity" in result["error"]

    def test_flow_none_workflow_identity_is_not_success(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rec = tmp_path / "rec"
        rec.mkdir()
        monkeypatch.setattr(
            "engine.hosted.FlowBridge.push",
            lambda *args, **kwargs: FlowResult(
                ok=True,
                returncode=0,
                stdout="Pushed. workflow_id=None (name='Example', kind=recording, compile=?).",
            ),
        )

        result = hosted.push(rec)

        assert result["success"] is False
        assert result["workflow_id"] == ""

    def test_flow_failure_uses_bounded_stdout_and_marks_delivery_uncertain(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rec = tmp_path / "rec"
        rec.mkdir()
        monkeypatch.setattr(
            "engine.hosted.FlowBridge.push",
            lambda *args, **kwargs: FlowResult(
                ok=False,
                returncode=1,
                stdout="request outcome unknown",
                stderr="",
            ),
        )

        result = hosted.push(rec, token="secret-value")

        assert result["success"] is False
        assert result["delivery_uncertain"] is True
        assert result["error"] == "request outcome unknown"


class TestReportBreak:
    def _write_report(self, run_dir: Path, halt: dict) -> None:
        run_dir.mkdir(parents=True)
        (run_dir / "report.json").write_text(json.dumps({"halt": halt}))

    def test_no_halt_returns_error(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "report.json").write_text(json.dumps({"status": "ok"}))
        result = report_break(run_dir, token="oai_ingest_x")
        assert result["ok"] is False
        assert "No halt" in result["error"]

    def test_success(self, tmp_path: Path, monkeypatch) -> None:
        run_dir = tmp_path / "run"
        self._write_report(run_dir, {"reason": "drift", "step_intent": "click"})
        calls: list[dict] = []
        monkeypatch.setattr(
            "engine.hosted.FlowBridge.report_break",
            lambda *a, **k: (
                calls.append(k)
                or FlowResult(
                    ok=True,
                    returncode=0,
                    stdout=(
                        "Break reported (run_id=r1, halt_id=h1, status=halt).\n"
                        "Teach: https://app.openadapt.ai/dashboard/runs/r1/teach\n"
                    ),
                )
            ),
        )
        result = report_break(run_dir, workflow_id="wf_1", token="oai_ingest_x")
        assert result["ok"] is True
        assert result["halt_id"] == "h1"
        assert result["teach_url"].endswith("/teach")
        assert calls[0]["env_overrides"] == {
            "OPENADAPT_INGEST_TOKEN": "oai_ingest_x"
        }

    def test_422_local_fallback(self, tmp_path: Path, monkeypatch) -> None:
        run_dir = tmp_path / "run"
        self._write_report(run_dir, {"reason": "drift"})
        monkeypatch.setattr(
            "engine.hosted.FlowBridge.report_break",
            lambda *a, **k: FlowResult(
                ok=True,
                returncode=0,
                stdout="Break kept LOCAL-ONLY: server rejected PHI boundary\n",
            ),
        )
        result = report_break(
            run_dir, workflow_id="wf_1", token="oai_ingest_x", allow_local_fallback=True
        )
        assert result["ok"] is False
        assert result["local_teach"] is True

    def test_422_raises_without_fallback(self, tmp_path: Path, monkeypatch) -> None:
        run_dir = tmp_path / "run"
        self._write_report(run_dir, {"reason": "drift"})
        monkeypatch.setattr(
            "engine.hosted.FlowBridge.report_break",
            lambda *a, **k: FlowResult(
                ok=True,
                returncode=0,
                stdout="Break kept LOCAL-ONLY: server rejected PHI boundary\n",
            ),
        )
        with pytest.raises(PhiBoundaryError):
            report_break(
                run_dir, workflow_id="wf_1", token="oai_ingest_x", allow_local_fallback=False
            )

    def test_not_logged_in(self, tmp_path: Path, fake_keyring) -> None:
        run_dir = tmp_path / "run"
        self._write_report(run_dir, {"reason": "drift"})
        result = report_break(run_dir, workflow_id="wf_1")
        assert result["ok"] is False
        assert "Not logged in" in result["error"]

    def test_free_text_is_not_sent_by_desktop(self, tmp_path: Path, monkeypatch) -> None:
        run_dir = tmp_path / "run"
        secret = "patient Jane Doe has record 12345"
        self._write_report(run_dir, {"reason": secret, "step_intent": secret})
        calls: list[tuple[tuple, dict]] = []
        monkeypatch.setattr(
            "engine.hosted.FlowBridge.report_break",
            lambda *args, **kwargs: (
                calls.append((args, kwargs))
                or FlowResult(
                    ok=True,
                    returncode=0,
                    stdout="Break reported (run_id=r1, halt_id=h1, status=halt).\n",
                )
            ),
        )

        result = report_break(run_dir, workflow_id="wf_1", token="token")

        assert result["ok"] is True
        assert secret not in repr(calls)
