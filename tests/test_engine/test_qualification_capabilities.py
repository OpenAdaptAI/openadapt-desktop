from __future__ import annotations

import hashlib
import json
from base64 import b64encode
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from engine.qualification_capabilities import (
    collect_qualification_capabilities,
    current_signed_capability_observations,
    sign_qualification_capability_observation,
    verify_qualification_capability_observation,
)
from engine.qualification_lifecycle import retain_capability_observation


def _sha() -> str:
    return hashlib.sha256(b"exact run report").hexdigest()


def test_capabilities_are_derived_from_exact_run_evidence() -> None:
    observation = collect_qualification_capabilities(
        {
            "execution_target_kind": "web",
            "execution_profile": "standard",
            "outcome_envelope": {
                "profile": "standard",
                "evidence_classes": [
                    "authorization",
                    "identity",
                    "postcondition",
                    "effect_tier_1",
                ],
            },
            "results": [
                {
                    "step_id": "submit",
                    "ok": True,
                    "resolution": {"rung": "structural"},
                    "before_png": "steps/submit.before.png",
                    "delivery_receipt": {"operation": "dom_click"},
                    "identity": {
                        "status": "verified",
                        "signal_evidence": [
                            {"source": "session", "verdict": "verified"},
                            {"source": "application", "verdict": "verified"},
                            {"source": "workflow_state", "verdict": "verified"},
                        ],
                    },
                    "postconditions_ok": True,
                    "effect_evidence": [
                        {
                            "verification_tier": 1,
                            "final_verdict": "confirmed",
                        }
                    ],
                }
            ],
        },
        expected_target_kind="web",
        runtime_version="1.24.0",
        report_sha256=_sha(),
        action_kinds={"submit": "click"},
    )

    assert observation.observed_capabilities == [
        "actuation",
        "application_identity",
        "effect_verification",
        "governed_authorization",
        "identity_verification",
        "independent_system_of_record",
        "pixel_observation",
        "playwright_dom",
        "postcondition_verification",
        "session_continuity",
        "settled_state_detection",
        "structural_observation",
        "workflow_state_identity",
    ]


def test_wrong_substrate_cannot_satisfy_declared_capabilities() -> None:
    observation = collect_qualification_capabilities(
        {
            "execution_target_kind": "windows",
            "execution_profile": "standard",
            "outcome_envelope": {
                "profile": "standard",
                "evidence_classes": ["authorization", "effect_tier_1"],
            },
            "results": [
                {
                    "step_id": "submit",
                    "ok": True,
                    "resolution": {"rung": "structural"},
                    "postconditions_ok": True,
                    "effect_evidence": [{"verification_tier": 1}],
                }
            ],
        },
        expected_target_kind="citrix",
        runtime_version="1.24.0",
        report_sha256=_sha(),
        action_kinds={"submit": "click"},
    )

    assert observation.target_kind_matches is False
    assert observation.observed_capabilities == []


def test_retained_capability_receipt_is_typed_bounded_and_hash_bound(
    tmp_path: Path,
) -> None:
    observation = collect_qualification_capabilities(
        {
            "execution_target_kind": "web",
            "results": [
                {
                    "step_id": "submit",
                    "ok": True,
                    "resolution": {"rung": "structural"},
                    "patient_name": "Sensitive Person",
                }
            ],
        },
        expected_target_kind="web",
        runtime_version="1.24.0",
        report_sha256=_sha(),
        action_kinds={"submit": "click"},
    )

    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    signed = sign_qualification_capability_observation(
        observation,
        project_id="project-1",
        project_revision=1,
        project_contract_sha256="1" * 64,
        workflow_contract_sha256="2" * 64,
        environment_contract_sha256="3" * 64,
        environment_digest="4" * 64,
        case_id="representative",
        run_id="run-1",
        attestation_key_id="desktop-local-v1",
        private_key=private_raw,
    )
    reference = retain_capability_observation(
        tmp_path,
        case_id="representative",
        run_id="run-1",
        observation=signed.model_dump(mode="json"),
    )
    receipt_path = tmp_path / "qualification-evidence" / reference["relative_path"]
    body = receipt_path.read_text(encoding="utf-8")
    payload = json.loads(body)

    assert "Sensitive Person" not in body
    assert verify_qualification_capability_observation(
        signed,
        public_key_base64=b64encode(
            private.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).decode("ascii"),
    )
    assert payload["observations"] == [
        {"name": "actuation", "source": "actuation"},
        {"name": "playwright_dom", "source": "resolution"},
        {"name": "structural_observation", "source": "resolution"},
    ]
    assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() == reference["sha256"]


def test_current_capability_receipt_rejects_tampering(tmp_path: Path) -> None:
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    environment = SimpleNamespace(
        environment_digest="4" * 64,
        runtime_version="1.24.0",
        contract_sha256=lambda: "3" * 64,
    )
    project = SimpleNamespace(
        project_id="project-1",
        revision=1,
        environment=environment,
        trusted_runner_keys={"desktop-local-v1": public_key},
        contract_sha256=lambda: "1" * 64,
    )
    observation = collect_qualification_capabilities(
        {
            "execution_target_kind": "web",
            "results": [
                {
                    "step_id": "submit",
                    "ok": True,
                    "resolution": {"rung": "structural"},
                }
            ],
        },
        expected_target_kind="web",
        runtime_version="1.24.0",
        report_sha256=_sha(),
        action_kinds={"submit": "click"},
    )
    signed = sign_qualification_capability_observation(
        observation,
        project_id="project-1",
        project_revision=1,
        project_contract_sha256="1" * 64,
        workflow_contract_sha256="2" * 64,
        environment_contract_sha256="3" * 64,
        environment_digest="4" * 64,
        case_id="representative",
        run_id="run-1",
        attestation_key_id="desktop-local-v1",
        private_key=private_raw,
    )
    reference = retain_capability_observation(
        tmp_path,
        case_id="representative",
        run_id="run-1",
        observation=signed.model_dump(mode="json"),
    )
    receipt_path = tmp_path / "qualification-evidence" / reference["relative_path"]
    receipt_path.with_name("run-report-receipt.json").write_text(
        json.dumps(
            {
                "schema": "openadapt.qualification-run-receipt/v1",
                "run_id": "run-1",
                "report_sha256": _sha(),
            }
        ),
        encoding="utf-8",
    )

    current = current_signed_capability_observations(
        tmp_path,
        workflow_contract_sha256="2" * 64,
        project=project,
    )
    assert current == {"representative": signed}

    tampered = json.loads(receipt_path.read_text())
    tampered["observations"].append({"name": "session_continuity", "source": "identity"})
    receipt_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert (
        current_signed_capability_observations(
            tmp_path,
            workflow_contract_sha256="2" * 64,
            project=project,
        )
        == {}
    )
