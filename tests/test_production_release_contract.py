"""Tests for the closed Desktop Production release contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.production_release_contract import (
    ARTIFACT_INVENTORY_SCHEMA,
    IMMUTABLE_RELEASES_DOMAIN,
    PLATFORM_VERIFICATION_MEDIA_TYPE,
    PLATFORM_VERIFICATION_SCHEMA,
    REPOSITORY,
    REPOSITORY_ID,
    admission_reference_digest,
    artifact_inventory_digest,
    artifact_specs,
    build_artifact_inventory,
    build_platform_verification,
    build_publication_staging,
    build_tag_binding,
    expected_asset_names,
    immutable_releases_digest,
    normalize_tag_rulesets,
    staging_digest,
    tag_binding_bytes,
    tag_ref_state_digest,
    tag_rulesets_digest,
    validate_bound_release,
    validate_immutable_releases_response,
    validate_platform_verification,
    validate_publication_staging,
    validate_publication_staging_bytes,
    validate_tag_binding_bytes,
    validate_tag_object,
    validate_tag_ref,
    validate_tag_ref_state,
    validate_tag_rulesets,
)

VERSION = "1.2.3"
SOURCE_COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64


def _materialize_release(directory: Path) -> None:
    directory.mkdir()
    for index, spec in enumerate(artifact_specs(VERSION), start=1):
        (directory / spec.name).write_bytes(f"artifact-{index}".encode())


def _artifact(platform: str, architecture: str) -> list[dict[str, object]]:
    values = []
    for spec in artifact_specs(VERSION):
        if spec.kind.startswith(f"{platform}-") and f"-{platform}-{architecture}" in spec.name:
            values.append(
                {
                    "name": spec.name,
                    "kind": spec.kind,
                    "sha256": DIGEST,
                    "size_bytes": 100,
                    "media_type": spec.media_type,
                }
            )
    return sorted(values, key=lambda item: (item["kind"], item["name"]))


def _document(platform: str, architecture: str) -> dict:
    artifacts = _artifact(platform, architecture)
    common = {
        "schema_version": PLATFORM_VERIFICATION_SCHEMA,
        "release": {
            "repository": REPOSITORY,
            "repository_id": REPOSITORY_ID,
            "source_commit": SOURCE_COMMIT,
            "version": VERSION,
            "tag": f"v{VERSION}",
        },
        "platform": platform,
        "architecture": architecture,
        "artifacts": artifacts,
        "build": {
            "workflow": ".github/workflows/release.yml",
            "workflow_ref": (
                "OpenAdaptAI/openadapt-desktop/.github/workflows/release.yml@refs/heads/main"
            ),
            "workflow_commit": SOURCE_COMMIT,
            "event": "workflow_dispatch",
            "run_id": 123,
            "run_attempt": 1,
            "runner_environment": "github-hosted",
            "install_verified": True,
            "launch_verified": True,
            "uninstall_verified": True,
            "embedded_flow_version": "2.0.0",
        },
    }
    if platform == "macos":
        verification = {
            "method": "apple-developer-id-notarization",
            "signature": {
                "status": "valid",
                "team_id": "ABCDE12345",
                "signer_identity_sha256": DIGEST,
                "designated_requirement_sha256": DIGEST,
                "hardened_runtime": True,
            },
            "notarization": {
                "status": "accepted",
                "ticket_stapled": True,
                "ticket_validated": True,
                "gatekeeper_assessment": "accepted",
            },
        }
    elif platform == "windows":
        verification = {
            "method": "authenticode",
            "file_digest_algorithm": "sha256",
            "signatures": [
                {
                    "artifact_name": artifact["name"],
                    "status": "valid",
                    "signer_certificate_sha256": DIGEST,
                    "signer_subject_sha256": DIGEST,
                    "timestamp_certificate_sha256": DIGEST,
                    "timestamp_subject_sha256": DIGEST,
                }
                for artifact in artifacts
            ],
        }
    else:
        verification = {
            "method": "github-oidc-attestation",
            "oidc_issuer": "https://token.actions.githubusercontent.com",
            "certificate_identity": (
                "https://github.com/OpenAdaptAI/openadapt-desktop/.github/workflows/"
                "release.yml@refs/heads/main"
            ),
            "predicate_type": "https://slsa.dev/provenance/v1",
            "build_type": "https://actions.github.io/buildtypes/workflow/v1",
            "subjects": [
                {"name": artifact["name"], "sha256": artifact["sha256"]} for artifact in artifacts
            ],
        }
    return {**common, "verification": verification}


def _raw_rulesets() -> tuple[dict, dict]:
    common = {
        "target": "tag",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
    }
    creation = {
        **common,
        "id": 100,
        "name": "OpenAdapt policy: release tag creation",
        "bypass_actors": [
            {
                "actor_id": 4730708,
                "actor_type": "Integration",
                "bypass_mode": "always",
            }
        ],
        "rules": [{"type": "creation"}],
    }
    immutability = {
        **common,
        "id": 101,
        "name": "OpenAdapt policy: immutable release tags",
        "bypass_actors": [],
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "update",
                "parameters": {"update_allows_fetch_and_merge": False},
            },
        ],
    }
    return creation, immutability


def _draft_api(release: Path) -> dict:
    inventory = build_artifact_inventory(release, version=VERSION)
    return {
        "id": 200,
        "tag_name": f"v{VERSION}",
        "target_commitish": SOURCE_COMMIT,
        "draft": True,
        "prerelease": False,
        "immutable": False,
        "author": {"id": 321543906, "login": "openadapt-release[bot]"},
        "assets": [
            {
                "id": 300 + index,
                "name": artifact["name"],
                "state": "uploaded",
                "size": artifact["size_bytes"],
                "digest": artifact["sha256"],
                "uploader": {
                    "id": 321543906,
                    "login": "openadapt-release[bot]",
                },
            }
            for index, artifact in enumerate(inventory["artifacts"])
        ],
    }


def _admission_reference() -> dict:
    object_digest = "sha256:" + "c" * 64
    value = {
        "schema_version": "openadapt.production-evidence-object-reference/v2",
        "repository": "OpenAdaptAI/.github",
        "repository_id": "858454062",
        "repository_owner_id": "132681217",
        "registry_source_commit": "d" * 40,
        "registry_revision": 10,
        "registry_head_sha256": "sha256:" + "e" * 64,
        "registry_entry_sha256": "",
        "kind": "qualification-release",
        "object_media_type": ("application/vnd.openadapt.qualification-release+json;version=1"),
        "object_path": (
            "production-evidence/objects/sha256/cc/" + "c" * 64 + ".qualification-release.json"
        ),
        "object_schema_version": "openadapt.qualification-release/v1",
        "object_sha256": object_digest,
        "semantic_identity_sha256": "sha256:" + "f" * 64,
        "size_bytes": 100,
        "subject_sha256": None,
    }
    entry = {
        key: value[key]
        for key in {
            "kind",
            "object_media_type",
            "object_path",
            "object_schema_version",
            "object_sha256",
            "semantic_identity_sha256",
            "size_bytes",
            "subject_sha256",
        }
    }
    canonical = json.dumps(
        entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    value["registry_entry_sha256"] = (
        "sha256:"
        + hashlib.sha256(
            b"OpenAdapt production evidence registry entry v1\0" + canonical
        ).hexdigest()
    )
    return value


def test_exact_production_asset_profile_has_fourteen_stable_names() -> None:
    specs = artifact_specs(VERSION)

    assert len(specs) == 14
    assert len(expected_asset_names(VERSION)) == 14
    assert [item.kind for item in specs] == sorted(item.kind for item in specs)
    assert all(
        word not in item.name.lower()
        for item in specs
        for word in ("beta", "candidate", "adhoc", "unsigned")
    )
    assert {item.kind for item in specs if item.media_type == PLATFORM_VERIFICATION_MEDIA_TYPE} == {
        "verification-metadata-linux-x86-64",
        "verification-metadata-macos-arm64",
        "verification-metadata-macos-x86-64",
        "verification-metadata-windows-x86-64",
    }


def test_artifact_inventory_binds_exact_bytes_and_destinations(tmp_path: Path) -> None:
    release = tmp_path / "release"
    _materialize_release(release)

    inventory = build_artifact_inventory(release, version=VERSION)

    assert inventory["schema_version"] == ARTIFACT_INVENTORY_SCHEMA
    assert inventory["target"] == "desktop"
    assert inventory["claim_scope"] == "production_desktop"
    assert len(inventory["artifacts"]) == 14
    for artifact in inventory["artifacts"]:
        assert set(artifact) == {
            "name",
            "kind",
            "sha256",
            "size_bytes",
            "media_type",
            "publish_destinations",
        }
        if artifact["kind"] in {"python-wheel", "python-sdist"}:
            assert artifact["publish_destinations"] == ["github-release", "pypi"]
        else:
            assert artifact["publish_destinations"] == ["github-release"]

    (release / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected"):
        build_artifact_inventory(release, version=VERSION)


@pytest.mark.parametrize(
    ("platform", "architecture"),
    [
        ("linux", "x86_64"),
        ("macos", "arm64"),
        ("macos", "x86_64"),
        ("windows", "x86_64"),
    ],
)
def test_platform_verification_schema_binds_native_evidence_without_secrets(
    platform: str,
    architecture: str,
) -> None:
    document = _document(platform, architecture)

    assert validate_platform_verification(document, version=VERSION) == document
    serialized = json.dumps(document, sort_keys=True).lower()
    for secret_field in (
        "private_key",
        "certificate_password",
        "apple_password",
        "client_secret",
        "pfx",
    ):
        assert secret_field not in serialized


def test_platform_verification_builder_hashes_exact_stable_artifacts(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    _materialize_release(release)
    expected = _document("windows", "x86_64")

    document = build_platform_verification(
        release,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        platform="windows",
        architecture="x86_64",
        workflow_commit=SOURCE_COMMIT,
        run_id=123,
        run_attempt=1,
        embedded_flow_version="2.0.0",
        verification=expected["verification"],
    )

    assert [item["name"] for item in document["artifacts"]] == [
        item["name"] for item in expected["artifacts"]
    ]
    assert all(item["sha256"] != DIGEST for item in document["artifacts"])
    assert document["verification"] == expected["verification"]


def test_platform_verification_rejects_unbound_or_false_evidence() -> None:
    macos = _document("macos", "arm64")
    macos["verification"]["notarization"]["ticket_stapled"] = False
    with pytest.raises(ValueError, match="notarization"):
        validate_platform_verification(macos, version=VERSION)

    windows = _document("windows", "x86_64")
    windows["verification"]["signatures"].pop()
    with pytest.raises(ValueError, match="exact artifact set"):
        validate_platform_verification(windows, version=VERSION)

    linux = _document("linux", "x86_64")
    linux["verification"]["certificate_identity"] = (
        "https://github.com/attacker/repository/workflow.yml@refs/heads/main"
    )
    with pytest.raises(ValueError, match="provenance identity"):
        validate_platform_verification(linux, version=VERSION)

    invalid_run = _document("linux", "x86_64")
    invalid_run["build"]["run_id"] = True
    with pytest.raises(ValueError, match="run_id"):
        validate_platform_verification(invalid_run, version=VERSION)

    invalid_size = _document("windows", "x86_64")
    invalid_size["artifacts"][0]["size_bytes"] = True
    with pytest.raises(ValueError, match="artifact binding"):
        validate_platform_verification(invalid_size, version=VERSION)


def test_platform_verification_is_closed() -> None:
    document = _document("macos", "arm64")
    document["credential"] = "must never be accepted"

    with pytest.raises(ValueError, match="contain exactly"):
        validate_platform_verification(document, version=VERSION)


def test_immutable_releases_response_and_domain_digest_are_exact() -> None:
    response = {"enabled": True, "enforced_by_owner": False}

    assert validate_immutable_releases_response(response) == response
    assert IMMUTABLE_RELEASES_DOMAIN.endswith(b"\0")
    assert immutable_releases_digest(response) == (
        "sha256:07649aafb167237fecc138f5e93b48ddce5a69f4060da7130e3c78e59fd48581"
    )

    with pytest.raises(ValueError, match="contain exactly"):
        validate_immutable_releases_response({"enabled": True})
    with pytest.raises(ValueError, match="contain exactly"):
        validate_immutable_releases_response(
            {"enabled": True, "enforced_by_owner": False, "extra": False}
        )
    with pytest.raises(ValueError, match="must be enabled"):
        validate_immutable_releases_response({"enabled": False, "enforced_by_owner": True})
    with pytest.raises(ValueError, match="must be boolean"):
        validate_immutable_releases_response({"enabled": True, "enforced_by_owner": "false"})


def test_prospective_tag_ref_state_must_be_absent_and_exact() -> None:
    state = {"ref": f"refs/tags/v{VERSION}", "exists": False}

    assert validate_tag_ref_state(state, tag=f"v{VERSION}") == state
    assert tag_ref_state_digest(state, tag=f"v{VERSION}") == (
        "sha256:561dd8fb56e1742c02468559b8810b6957687423ed0d2d30dd97b8994df0aaf3"
    )

    with pytest.raises(ValueError, match="already exists"):
        validate_tag_ref_state(
            {"ref": f"refs/tags/v{VERSION}", "exists": True},
            tag=f"v{VERSION}",
        )
    with pytest.raises(ValueError, match="differs"):
        validate_tag_ref_state(
            {"ref": "refs/tags/v9.9.9", "exists": False},
            tag=f"v{VERSION}",
        )


def test_tag_rulesets_normalize_exact_live_authority() -> None:
    creation, immutability = _raw_rulesets()

    rulesets = normalize_tag_rulesets(creation, immutability)

    assert validate_tag_rulesets(rulesets) == rulesets
    assert rulesets[0]["bypass_actors"][0]["actor_id"] == "4730708"
    assert rulesets[1]["bypass_actors"] == []
    assert tag_rulesets_digest(rulesets).startswith("sha256:")

    creation["bypass_actors"][0]["actor_id"] = 321543906
    with pytest.raises(ValueError, match="policy differs"):
        normalize_tag_rulesets(creation, immutability)


def test_publication_staging_binds_the_same_complete_app_draft(tmp_path: Path) -> None:
    release = tmp_path / "release"
    _materialize_release(release)
    creation, immutability = _raw_rulesets()
    rulesets = normalize_tag_rulesets(creation, immutability)
    tag_state = {"ref": f"refs/tags/v{VERSION}", "exists": False}

    staging = build_publication_staging(
        _draft_api(release),
        directory=release,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        immutable_releases={"enabled": True, "enforced_by_owner": False},
        tag_rulesets=rulesets,
        tag_ref_state=tag_state,
        observed_at="2026-08-27T12:00:00Z",
    )

    assert staging["schema_version"] == ("openadapt.production-release-staging-evidence/v1")
    assert staging["draft_release_id"] == "200"
    assert staging["draft"] is True
    assert staging["prerelease"] is False
    assert staging["release_app_id"] == "4730708"
    assert staging["release_app_installation_id"] == "156835568"
    assert staging["release_app_bot_user_id"] == "321543906"
    assert staging["tag_ref_state"] == tag_state
    assert len(staging["assets"]) == 14
    assert staging["assets"] == sorted(
        staging["assets"], key=lambda item: (item["name"], item["asset_id"])
    )
    assert staging_digest(staging).startswith("sha256:")


def test_publication_staging_rejects_a_changed_draft_or_tag(tmp_path: Path) -> None:
    release = tmp_path / "release"
    _materialize_release(release)
    creation, immutability = _raw_rulesets()
    rulesets = normalize_tag_rulesets(creation, immutability)
    draft = _draft_api(release)
    draft["assets"][0]["uploader"]["id"] = 999

    with pytest.raises(ValueError, match="release App"):
        build_publication_staging(
            draft,
            directory=release,
            version=VERSION,
            source_commit=SOURCE_COMMIT,
            immutable_releases={"enabled": True, "enforced_by_owner": True},
            tag_rulesets=rulesets,
            tag_ref_state={"ref": f"refs/tags/v{VERSION}", "exists": False},
            observed_at="2026-08-27T12:00:00Z",
        )

    with pytest.raises(ValueError, match="already exists"):
        build_publication_staging(
            _draft_api(release),
            directory=release,
            version=VERSION,
            source_commit=SOURCE_COMMIT,
            immutable_releases={"enabled": True, "enforced_by_owner": True},
            tag_rulesets=rulesets,
            tag_ref_state={"ref": f"refs/tags/v{VERSION}", "exists": True},
            observed_at="2026-08-27T12:00:00Z",
        )


def test_central_staging_output_is_revalidated_and_rehashed(tmp_path: Path) -> None:
    release = tmp_path / "release"
    _materialize_release(release)
    creation, immutability = _raw_rulesets()
    staging = build_publication_staging(
        _draft_api(release),
        directory=release,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        immutable_releases={"enabled": True, "enforced_by_owner": False},
        tag_rulesets=normalize_tag_rulesets(creation, immutability),
        tag_ref_state={"ref": f"refs/tags/v{VERSION}", "exists": False},
        observed_at="2026-08-27T12:00:00Z",
    )
    digest = staging_digest(staging)
    raw = json.dumps(
        staging,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert (
        validate_publication_staging_bytes(
            raw,
            version=VERSION,
            expected_source_commit=SOURCE_COMMIT,
            expected_draft_release_id="200",
            expected_sha256=digest,
        )
        == staging
    )

    with pytest.raises(ValueError, match="compact canonical"):
        validate_publication_staging_bytes(
            raw + b"\n",
            version=VERSION,
            expected_source_commit=SOURCE_COMMIT,
            expected_draft_release_id="200",
            expected_sha256=digest,
        )
    with pytest.raises(ValueError, match="digest differs"):
        validate_publication_staging_bytes(
            raw,
            version=VERSION,
            expected_source_commit=SOURCE_COMMIT,
            expected_draft_release_id="200",
            expected_sha256="sha256:" + "0" * 64,
        )


def test_central_staging_rejects_unknown_fields_and_noncanonical_assets(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    _materialize_release(release)
    creation, immutability = _raw_rulesets()
    staging = build_publication_staging(
        _draft_api(release),
        directory=release,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        immutable_releases={"enabled": True, "enforced_by_owner": True},
        tag_rulesets=normalize_tag_rulesets(creation, immutability),
        tag_ref_state={"ref": f"refs/tags/v{VERSION}", "exists": False},
        observed_at="2026-08-27T12:00:00Z",
    )

    staging["unexpected"] = True
    with pytest.raises(ValueError, match="contain exactly"):
        validate_publication_staging(staging, version=VERSION)
    staging.pop("unexpected")

    staging["assets"].reverse()
    with pytest.raises(ValueError, match="canonically sorted"):
        validate_publication_staging(staging, version=VERSION)


def test_platform_verification_asset_hashes_exact_bytes_before_parsing(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    _materialize_release(release)
    metadata = release / f"OpenAdapt-Desktop-v{VERSION}-linux-x86_64-verification.json"
    metadata.write_text('{"same":"meaning"}\n', encoding="utf-8")
    draft = _draft_api(release)

    parsed = json.loads(metadata.read_bytes())
    metadata.write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    creation, immutability = _raw_rulesets()
    with pytest.raises(ValueError, match="bytes differ"):
        build_publication_staging(
            draft,
            directory=release,
            version=VERSION,
            source_commit=SOURCE_COMMIT,
            immutable_releases={"enabled": True, "enforced_by_owner": False},
            tag_rulesets=normalize_tag_rulesets(creation, immutability),
            tag_ref_state={"ref": f"refs/tags/v{VERSION}", "exists": False},
            observed_at="2026-08-27T12:00:00Z",
        )


def test_bound_release_reuses_admitted_draft_id_and_exact_bytes(tmp_path: Path) -> None:
    release = tmp_path / "release"
    _materialize_release(release)
    creation, immutability = _raw_rulesets()
    draft = _draft_api(release)
    staging = build_publication_staging(
        draft,
        directory=release,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        immutable_releases={"enabled": True, "enforced_by_owner": False},
        tag_rulesets=normalize_tag_rulesets(creation, immutability),
        tag_ref_state={"ref": f"refs/tags/v{VERSION}", "exists": False},
        observed_at="2026-08-27T12:00:00Z",
    )

    assert (
        validate_bound_release(
            draft,
            directory=release,
            version=VERSION,
            publication_staging=staging,
            phase="draft",
        )
        == draft
    )

    published = json.loads(json.dumps(draft))
    published["draft"] = False
    published["immutable"] = True
    assert (
        validate_bound_release(
            published,
            directory=release,
            version=VERSION,
            publication_staging=staging,
            phase="published",
        )
        == published
    )

    replacement = json.loads(json.dumps(draft))
    replacement["id"] = 201
    with pytest.raises(ValueError, match="identity differs"):
        validate_bound_release(
            replacement,
            directory=release,
            version=VERSION,
            publication_staging=staging,
            phase="draft",
        )


def test_annotated_tag_binding_is_exact_canonical_json_plus_one_lf(
    tmp_path: Path,
) -> None:
    reference = _admission_reference()
    release = tmp_path / "release"
    _materialize_release(release)
    inventory = build_artifact_inventory(release, version=VERSION)
    inventory_digest = artifact_inventory_digest(inventory, version=VERSION)

    binding = build_tag_binding(
        reference,
        inventory,
        version=VERSION,
        verified_artifact_inventory_sha256=inventory_digest,
    )
    raw = tag_binding_bytes(binding)

    assert binding["schema_version"] == "openadapt.production-release-tag-binding/v1"
    assert binding["admission_reference"] == reference
    assert binding["admission_reference_sha256"] == admission_reference_digest(reference)
    assert (
        raw
        == (
            json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    )
    assert validate_tag_binding_bytes(raw) == binding

    for changed in (
        b"prefix" + raw,
        raw + b"\n",
        raw.rstrip(b"\n"),
        json.dumps(binding, indent=2, sort_keys=True).encode() + b"\n",
    ):
        with pytest.raises(ValueError):
            validate_tag_binding_bytes(changed)

    with pytest.raises(ValueError, match="differs from local"):
        build_tag_binding(
            reference,
            inventory,
            version=VERSION,
            verified_artifact_inventory_sha256="sha256:" + "0" * 64,
        )


def test_tag_object_and_ref_bind_the_exact_canonical_annotation(tmp_path: Path) -> None:
    release = tmp_path / "release"
    _materialize_release(release)
    inventory = build_artifact_inventory(release, version=VERSION)
    binding = build_tag_binding(
        _admission_reference(),
        inventory,
        version=VERSION,
        verified_artifact_inventory_sha256=artifact_inventory_digest(inventory, version=VERSION),
    )
    tag_object_sha = "2" * 40
    tag_object = {
        "sha": tag_object_sha,
        "tag": f"v{VERSION}",
        "message": tag_binding_bytes(binding).decode(),
        "object": {"type": "commit", "sha": SOURCE_COMMIT},
    }

    assert (
        validate_tag_object(
            tag_object,
            expected_tag=f"v{VERSION}",
            expected_commit=SOURCE_COMMIT,
            expected_binding=binding,
        )
        == tag_object_sha
    )
    assert (
        validate_tag_ref(
            {
                "ref": f"refs/tags/v{VERSION}",
                "object": {"type": "tag", "sha": tag_object_sha},
            },
            expected_tag=f"v{VERSION}",
            expected_tag_object_sha=tag_object_sha,
        )
        is None
    )

    tag_object["message"] = tag_object["message"].rstrip("\n")
    with pytest.raises(ValueError, match="one LF"):
        validate_tag_object(
            tag_object,
            expected_tag=f"v{VERSION}",
            expected_commit=SOURCE_COMMIT,
            expected_binding=binding,
        )


def test_platform_verification_json_schema_is_present_and_closed() -> None:
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "schemas"
            / "desktop-platform-verification.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == (PLATFORM_VERIFICATION_SCHEMA)
