"""Tests for native release staging and integrity metadata."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

import scripts.verify_native_release_download as download_verifier
from scripts.native_release import (
    ENGINE_RELEASE_PROVENANCE,
    NATIVE_RELEASE_PROVENANCE,
    NATIVE_RELEASE_VERIFIER,
    VERIFIED_RELEASE_CHANNEL,
    VERIFIED_RELEASE_INDEX,
    VERSION_TRANSFORM_PATHS,
    expected_release_asset_names,
    installer_pointer_notes,
    native_release_tags,
    native_tag_tuple,
    native_version,
    native_version_at_ref,
    select_latest_native_release,
    set_native_version,
    stage_artifacts,
    superseded_notes,
    sync_native_version_from_engine,
    validate_engine_release,
    validate_engine_release_provenance,
    validate_git_version_advance,
    validate_git_version_transform,
    validate_native_tag_order,
    validate_release_attestation,
    validate_release_provenance,
    validate_release_set,
    validate_release_workflow_run,
    validate_sbom,
    validate_tag,
    validate_verified_release_channel,
    validate_verified_release_index,
    validate_website_release_manifest,
    verify_checksums,
    write_checksums,
    write_engine_release_provenance,
    write_release_provenance,
    write_verified_release_channel,
    write_verified_release_index,
    write_website_release_manifest,
)
from scripts.production_release import (
    CHANNEL_PREFIX,
    DESKTOP_REPOSITORY,
    PROMOTION_WORKFLOW,
    build_admission_state,
    production_channel_asset_name,
    validate_admission_state,
    verify_production_channel,
    write_admission_state,
    write_production_channel,
)
from scripts.verify_native_release_download import (
    verify as verify_download_inventory,
)
from scripts.verify_native_release_download import (
    verify_authenticated_channel,
)

ROOT = Path(__file__).resolve().parents[1]


def _native_workflow_ref(tag: str | None = None) -> str:
    tag = tag or f"desktop-v{native_version()}"
    return (
        "OpenAdaptAI/openadapt-desktop/.github/workflows/"
        f"native-release.yml@refs/tags/{tag}"
    )


def _engine_workflow_ref(tag: str | None = None) -> str:
    tag = tag or f"v{native_version()}"
    return (
        "OpenAdaptAI/openadapt-desktop/.github/workflows/"
        f"release.yml@refs/tags/{tag}"
    )


def _workflow(name: str) -> dict:
    payload = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())
    assert isinstance(payload, dict)
    return payload


def _job_steps(job: dict) -> dict[str, dict]:
    return {str(step.get("name") or step.get("uses")): step for step in job["steps"]}


def _workflow_uses(payload: dict) -> list[str]:
    return [
        step["uses"] for job in payload["jobs"].values() for step in job["steps"] if "uses" in step
    ]


def test_native_versions_are_synchronized() -> None:
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", native_version())


def test_node_dependencies_are_locked_for_cross_platform_tauri_builds() -> None:
    package = json.loads((ROOT / "package.json").read_text())
    lock = json.loads((ROOT / "package-lock.json").read_text())

    assert lock["lockfileVersion"] == 3
    assert lock["packages"][""]["version"] == package["version"]
    assert lock["packages"]["node_modules/@tauri-apps/cli"]["version"] == "2.11.4"
    assert lock["packages"]["node_modules/@tauri-apps/api"]["version"] == "2.11.1"


def test_native_workflows_are_pinned_and_preserve_candidate_boundary() -> None:
    build = _workflow("build.yml")
    release = _workflow("native-release.yml")
    uses = _workflow_uses(build) + _workflow_uses(release)
    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", use) for use in uses)

    trigger = release[True]
    assert set(trigger) == {"workflow_dispatch", "push"}
    assert trigger["workflow_dispatch"]["inputs"]["version"]["required"] is True
    assert trigger["push"]["tags"] == ["desktop-v*"]
    assert release["permissions"] == {"contents": "read"}
    assert release["concurrency"]["group"] == "native-release"
    assert release["concurrency"]["cancel-in-progress"] is False

    jobs = release["jobs"]
    dispatch = jobs["authorize-native-dispatch"]
    assert dispatch["permissions"] == {}
    dispatch_script = _job_steps(dispatch)[
        "Require the exact repository, main ref, and stable version input"
    ]["run"]
    assert '"${GITHUB_REF}" != "refs/heads/main"' in dispatch_script
    assert '"${GITHUB_REF_TYPE}" != "branch"' in dispatch_script
    tagger = jobs["create-native-tag"]
    assert tagger["needs"] == "authorize-native-dispatch"
    assert "needs.authorize-native-dispatch.result == 'success'" in tagger["if"]
    assert tagger["environment"] == "release-identity"
    tagger_steps = _job_steps(tagger)
    app_token = tagger_steps["Mint the release App token for this repository"]
    assert app_token["uses"] == (
        "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
    )
    assert app_token["with"]["app-id"] == "${{ vars.OPENADAPT_RELEASE_APP_ID }}"
    assert app_token["with"]["private-key"] == (
        "${{ secrets.OPENADAPT_RELEASE_APP_PRIVATE_KEY }}"
    )
    assert app_token["with"]["permission-contents"] == "write"
    assert app_token["with"]["permission-metadata"] == "read"
    tagger_checkouts = [
        step
        for step in tagger["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert tagger_checkouts[0]["with"]["persist-credentials"] is False
    assert tagger_checkouts[-1]["with"]["persist-credentials"] is False
    assert tagger_checkouts[-1]["with"]["token"] == "${{ steps.release_app.outputs.token }}"
    tag_write = tagger_steps["Create only the exact native candidate tag"]["run"]
    assert 'git tag --annotate "${NATIVE_TAG}" "${GITHUB_SHA}"' in tag_write
    assert 'push origin "refs/tags/${NATIVE_TAG}:refs/tags/${NATIVE_TAG}"' in tag_write
    assert "GIT_CONFIG_KEY_0=http.https://github.com/.extraheader" in tag_write
    assert 'GIT_CONFIG_VALUE_0="AUTHORIZATION: basic ${app_basic}"' in tag_write
    assert tagger_steps["Create only the exact native candidate tag"]["env"][
        "APP_TOKEN"
    ] == "${{ steps.release_app.outputs.token }}"
    assert "refs/heads/main:refs/heads/main" not in tag_write
    assert jobs["publish-native"]["environment"] == "native-release"
    assert jobs["publish-native"]["permissions"] == {
        "contents": "write",
        "attestations": "read",
    }
    publish_steps = _job_steps(jobs["publish-native"])
    publish_app = publish_steps["Mint the release App token for GitHub publication"]
    assert publish_app["with"]["permission-contents"] == "write"
    assert publish_app["with"]["permission-metadata"] == "read"
    assert publish_steps[
        "Create or safely resume the immutable public prerelease"
    ]["env"]["GH_TOKEN"] == "${{ steps.release_app.outputs.token }}"
    assert jobs["attest"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }
    assert jobs["verify-published-release"]["permissions"] == {
        "actions": "read",
        "attestations": "read",
        "contents": "read",
    }
    validate_steps = _job_steps(jobs["validate"])
    assert (
        validate_steps["actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"]["with"][
            "fetch-depth"
        ]
        == 0
    )
    validation = validate_steps[
        "Bind the event, ref, version, tag, and signed engine receipt"
    ]["run"]
    assert "validate-engine-provenance" in validation
    assert "refs/tags/desktop-v" in validation
    assert "github.event_name == 'push'" in jobs["validate"]["if"]

    verifier_steps = _job_steps(jobs["verify-published-release"])
    verifier_checkout = verifier_steps["actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"]
    assert verifier_checkout["with"] == {
        "ref": "${{ needs.validate.outputs.source_commit }}",
        "fetch-depth": 0,
    }
    assert jobs["verify-published-release"]["outputs"]["verifier_commit"]
    for name in (
        "point-engine-release",
        "mirror-installers-to-engine-release",
        "supersede-published-native",
    ):
        assert jobs[name]["environment"] == "native-release"
        checkout = _job_steps(jobs[name])[
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        ]
        assert checkout["with"]["ref"] == (
            "${{ needs.verify-published-release.outputs.verifier_commit }}"
        )
        assert checkout["with"]["fetch-depth"] == 0

    attest_steps = _job_steps(jobs["attest"])
    assert attest_steps["Attest the exact files named by SHA256SUMS"]["with"] == {
        "subject-checksums": "release-assets/SHA256SUMS"
    }
    assert attest_steps["Attest SHA256SUMS as the consumer trust root"]["with"] == {
        "subject-path": "release-assets/SHA256SUMS"
    }


def test_existing_native_prerelease_skips_rebuild_only_after_authentication() -> None:
    jobs = _workflow("native-release.yml")["jobs"]
    recovery = jobs["recover-published-native"]
    assert recovery["permissions"] == {
        "actions": "read",
        "attestations": "read",
        "contents": "read",
    }
    assert recovery["outputs"]["state"] == "${{ steps.recovery.outputs.state }}"
    steps = _job_steps(recovery)
    lookup = steps["Resolve the exact release without treating API failure as absence"]["run"]
    assert 'if [ "${status}" = "404" ]' in lookup
    assert "refusing a rebuild" in lookup
    public_proof = steps[
        "Authenticate a complete public set or request partial recovery"
    ]["run"]
    for contract in (
        "verify-checksums",
        "validate-set",
        "validate-sbom",
        "validate-website-manifest",
        "gh attestation verify",
        "validate-attestation",
    ):
        assert contract in public_proof

    artifact_download = steps["Download the original attested set for partial recovery"]
    assert artifact_download["if"] == "steps.published.outputs.state == 'partial'"
    assert artifact_download["with"]["name"] == (
        "native-release-${{ needs.validate.outputs.native_tag }}"
    )
    partial_proof = steps["Authenticate the original set and every published byte"]["run"]
    assert 'cmp "attested-assets/${name}" "published-assets/${name}"' in partial_proof
    assert "complete public release failed authentication" in partial_proof

    for name in ("build-macos", "build-windows", "build-linux"):
        assert set(jobs[name]["needs"]) == {"validate", "recover-published-native"}
        assert jobs[name]["if"] == (
            "needs.recover-published-native.outputs.state == 'absent'"
        )
    publisher = jobs["publish-native"]
    assert set(publisher["needs"]) == {
        "validate",
        "recover-published-native",
        "attest",
    }
    assert "recover-published-native.outputs.state == 'partial'" in publisher["if"]
    assert "needs.attest.result == 'success'" in publisher["if"]
    verifier = jobs["verify-published-release"]
    assert set(verifier["needs"]) == {
        "validate",
        "recover-published-native",
        "publish-native",
    }
    assert "always()" in verifier["if"]
    assert "recover-published-native.outputs.state == 'complete'" in verifier["if"]
    assert "needs.publish-native.result == 'success'" in verifier["if"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_REPOSITORY", "OpenAdaptAI/fork"),
        ("GITHUB_REF", "refs/heads/release"),
        ("GITHUB_REF_TYPE", "tag"),
        ("REQUESTED_VERSION", "1.2"),
    ],
)
def test_native_dispatch_guard_refuses_every_invalid_identity(
    field: str, value: str
) -> None:
    job = _workflow("native-release.yml")["jobs"]["authorize-native-dispatch"]
    script = job["steps"][0]["run"]
    env = os.environ | {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "OpenAdaptAI/openadapt-desktop",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_TYPE": "branch",
        "REQUESTED_VERSION": "1.2.3",
        field: value,
    }

    assert subprocess.run(["bash", "-c", script], env=env, check=False).returncode != 0


def test_complete_native_recovery_does_not_require_an_unexpired_actions_artifact() -> None:
    recovery = _workflow("native-release.yml")["jobs"]["recover-published-native"]
    steps = recovery["steps"]
    names = [str(step.get("name") or step.get("uses")) for step in steps]
    public_index = names.index(
        "Authenticate a complete public set or request partial recovery"
    )
    artifact_index = names.index("Download the original attested set for partial recovery")
    assert public_index < artifact_index

    public_proof = steps[public_index]["run"]
    assert 'echo "state=complete"' in public_proof
    assert "published-assets/SHA256SUMS" in public_proof
    assert "attested-assets" not in public_proof
    assert steps[artifact_index]["if"] == "steps.published.outputs.state == 'partial'"


def test_native_reruns_use_attempt_scoped_intermediate_artifacts() -> None:
    jobs = _workflow("native-release.yml")["jobs"]
    for name in ("build-macos", "build-windows", "build-linux"):
        upload = next(
            step
            for step in jobs[name]["steps"]
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        )
        assert "${{ github.run_attempt }}" in upload["with"]["name"]
    attest_steps = _job_steps(jobs["attest"])
    platform_download = attest_steps["Download all smoke-tested platform artifacts"]
    assert platform_download["with"]["pattern"] == (
        "native-*-attempt-${{ github.run_attempt }}"
    )
    exact_set_upload = attest_steps["Upload exact attested release set"]
    assert exact_set_upload["with"]["overwrite"] is True

    verifier = jobs["verify-published-release"]
    verified_upload = next(
        step
        for step in verifier["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert verified_upload["with"]["name"].endswith("-${{ github.run_attempt }}")


def test_engine_and_native_release_form_one_attested_acceptance_chain() -> None:
    engine = _workflow("release.yml")
    native = _workflow("native-release.yml")

    tagger = engine["jobs"]["create-release-tag"]
    assert tagger["environment"] == "release-identity"
    assert tagger["outputs"]["engine_commit"] == "${{ steps.candidate.outputs.engine_commit }}"
    engine_job = engine["jobs"]["publish-tagged-engine"]
    assert engine_job["environment"] == "pypi"
    assert engine_job["permissions"]["id-token"] == "write"
    assert engine_job["permissions"]["attestations"] == "write"
    engine_steps = _job_steps(engine_job)
    receipt = engine_steps["Write the exact-tag engine release receipt"]["run"]
    assert "write-engine-provenance" in receipt
    assert '--workflow-commit "${GITHUB_WORKFLOW_SHA}"' in receipt
    assert '"${ENGINE_COMMIT}^"' not in receipt
    assert engine_steps["Attest the engine release receipt"]["with"] == {
        "subject-path": ENGINE_RELEASE_PROVENANCE
    }
    published_receipt = engine_steps["Publish and reverify the engine release receipt"]["run"]
    assert 'gh release upload "${engine_tag}" "${subject}"' in published_receipt
    assert "validate-engine-provenance" in published_receipt
    assert "@refs/tags/${engine_tag}" in published_receipt

    validate = _job_steps(native["jobs"]["validate"])[
        "Bind the event, ref, version, tag, and signed engine receipt"
    ]["run"]
    assert "openadapt-desktop-engine-release-provenance.json" in validate
    assert "gh attestation verify" in validate
    assert "validate-engine-provenance" in validate
    assert 'if [ "${receipt_commit}" != "${engine_commit}" ]' in validate

    mirror_steps = _job_steps(native["jobs"]["mirror-installers-to-engine-release"])
    assert mirror_steps["Attest the verified release index"]["with"] == {
        "subject-path": VERIFIED_RELEASE_INDEX
    }
    channel = mirror_steps["Write the monotonic candidate channel descriptor"]["run"]
    assert "write-release-channel" in channel
    assert "--existing prior-channel/openadapt-desktop-channel.json" in channel
    assert "validate-release-channel" in channel
    assert mirror_steps["Attest the monotonic candidate channel descriptor"]["with"] == {
        "subject-path": VERIFIED_RELEASE_CHANNEL
    }
    publication = mirror_steps["Publish and reverify the candidate channel authority"]["run"]
    assert "channel_tag=desktop-channel" in publication
    assert "gh attestation verify" in publication
    assert "--clobber" in publication


def test_windows_installer_lifecycle_has_an_overall_fail_closed_timeout() -> None:
    build = (ROOT / ".github/workflows/build.yml").read_text()
    release = (ROOT / ".github/workflows/native-release.yml").read_text()

    build_smoke = build.split(
        "- name: Smoke-test Windows MSI and NSIS install, launch, and uninstall", 1
    )[1].split("\n      - name:", 1)[0]
    release_smoke = release.split(
        "- name: Smoke-test MSI and NSIS install, signature policy, launch, and uninstall", 1
    )[1].split("\n      - name:", 1)[0]
    assert "timeout-minutes: 15" in build_smoke
    assert "timeout-minutes: 15" in release_smoke


def test_validate_sbom_requires_cyclonedx_generator_and_named_components(
    tmp_path: Path,
) -> None:
    path = tmp_path / "release.cyclonedx.json"
    path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "version": 1,
                "metadata": {"tools": [{"vendor": "Anchore", "name": "Syft", "version": "1.44.0"}]},
                "components": [
                    {
                        "type": "library",
                        "name": "openadapt-flow",
                        "version": "1.22.0",
                        "purl": "pkg:pypi/openadapt-flow@1.22.0",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert validate_sbom(path) == 1

    empty = json.loads(path.read_text(encoding="utf-8"))
    empty["components"] = []
    path.write_text(json.dumps(empty), encoding="utf-8")
    with pytest.raises(ValueError, match="no detected components"):
        validate_sbom(path)


def test_dependabot_covers_every_desktop_release_ecosystem() -> None:
    config = (ROOT / ".github" / "dependabot.yml").read_text()

    for ecosystem in ("github-actions", "pip", "npm", "cargo"):
        assert f"package-ecosystem: {ecosystem}" in config
    assert 'directory: "/src-tauri"' in config


def test_security_workflows_cover_all_languages_and_pin_every_dependency() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    security_workflows = [
        workflow_dir / "codeql.yml",
        workflow_dir / "dependency-review.yml",
        workflow_dir / "secret-scan.yml",
    ]
    text = "\n".join(path.read_text() for path in security_workflows)
    uses = re.findall(r"^\s*(?:-\s+)?uses:\s+\S+@([^\s#]+)", text, flags=re.MULTILINE)

    assert uses
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in uses)
    for language in ("python", "javascript-typescript", "rust"):
        assert f'"{language}"' in (workflow_dir / "codeql.yml").read_text()
    dependency_audit = (workflow_dir / "dependency-review.yml").read_text()
    assert "macos-15-intel" in dependency_audit
    assert "uv export --quiet --locked --all-extras --no-emit-project" in dependency_audit
    assert "pip-audit==2.10.1" in dependency_audit
    assert "npm audit --audit-level=high --package-lock-only" in dependency_audit
    assert "cargo install cargo-audit --version 0.22.1 --locked" in dependency_audit
    assert "cargo audit --file src-tauri/Cargo.lock" in dependency_audit
    assert "schedule:" in dependency_audit
    secret_scan = (workflow_dir / "secret-scan.yml").read_text()
    assert "fetch-depth: 0" in secret_scan
    assert "GITLEAKS_VERSION: 8.30.1" in secret_scan
    assert (
        "GITLEAKS_SHA256: "
        "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb" in secret_scan
    )
    assert "sha256sum --check --strict" in secret_scan


def test_freshness_workflow_syncs_engine_releases_into_the_native_lane() -> None:
    freshness = _workflow("native-freshness.yml")
    uses = _workflow_uses(freshness)
    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", use) for use in uses)

    trigger = freshness[True]
    assert trigger["release"] == {"types": ["published"]}
    assert "workflow_dispatch" in trigger
    assert "push" not in trigger
    assert freshness["permissions"] == {"contents": "read"}
    assert set(freshness["jobs"]) == {"validate-native-version"}

    validation = freshness["jobs"]["validate-native-version"]
    assert "environment" not in validation
    validation_steps = _job_steps(validation)
    validation_checkout = validation_steps[
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    ]
    assert validation_checkout["with"] == {
        "ref": "main",
        "fetch-depth": 0,
    }
    scripts = "\n".join(step.get("run", "") for step in validation["steps"])
    assert "ADMIN_TOKEN" not in freshness
    assert "DOCS_PAT" not in freshness
    assert "HEAD:main" not in scripts
    assert "git tag" not in scripts
    assert "git push" not in scripts
    assert "gh pr create" not in scripts
    assert "gh release create" not in scripts
    resolve = validation_steps["Resolve the published engine release"]["run"]
    assert "native_version" in resolve
    assert 'if [ "${engine_commit}" != "$(git rev-parse origin/main)" ]' in resolve


def test_native_version_pr_guard_never_skips_and_judges_content() -> None:
    guard = _workflow("native-version-guard.yml")
    assert guard[True]["pull_request"]["types"] == [
        "opened",
        "reopened",
        "synchronize",
        "ready_for_review",
    ]
    assert guard[True]["pull_request"]["paths"] == list(VERSION_TRANSFORM_PATHS)
    job = guard["jobs"]["validate-advance"]
    assert guard["permissions"] == {"contents": "read"}

    # A branch name is author controlled, and GitHub counts a skipped job as a
    # satisfied required check. The job must run for every matching pull
    # request and refuse in a step instead.
    assert "if" not in job
    assert not any("if" in step for step in job["steps"])
    assert "github.head_ref" not in json.dumps(job)

    steps = _job_steps(job)
    checkout = steps["actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"]
    # The guard grades the head, so it must run the reviewed base scripts.
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert checkout["with"]["fetch-depth"] == 0

    fetch = steps["Fetch the exact pull request head"]["run"]
    assert "refs/pull/${PR_NUMBER}/head:refs/remotes/pull/${PR_NUMBER}/head" in fetch
    assert 'if [ "${observed}" != "${HEAD_SHA}" ]' in fetch

    step = steps["Require an exact strict advance from the current PR base"]
    script = step["run"]
    assert step["env"]["HEAD_REF"] == "${{ github.event.pull_request.head.ref }}"
    assert step["env"]["HEAD_REPOSITORY"] == "${{ github.event.pull_request.head.repo.full_name }}"

    # An ordinary dependency or feature change to the same five files keeps the
    # version, and must pass.
    assert 'if [ "${base_version}" = "${head_version}" ]' in script
    assert "version --ref" in script
    # A version change is reserved to this repository's automation branch.
    assert 'if [ "${HEAD_REPOSITORY}" != "${GITHUB_REPOSITORY}" ]' in script
    assert "native-version/v*) ;;" in script
    # One refusal for a fork head, one for an unreserved branch, one for a
    # stale base. No path through the step falls through to success.
    assert script.count("exit 1") == 3
    assert "validate-version-advance" in script
    assert "github.event.pull_request.base.sha" in json.dumps(job)
    assert "git fetch origin main:refs/remotes/origin/main" in script
    assert 'if [ "${BASE_SHA}" != "${current_base}" ]' in script


def test_supersession_edits_notes_only_and_never_deletes() -> None:
    release = (ROOT / ".github/workflows/native-release.yml").read_text()
    freshness = (ROOT / ".github/workflows/native-freshness.yml").read_text()

    # Draft creation never invalidates the currently published installer.
    assert "  supersede-published-native:" not in freshness
    assert "  supersede-published-native:" in release
    supersede_job = release.split("  supersede-published-native:", 1)[1]
    assert "needs: [verify-published-release, mirror-installers-to-engine-release]" in supersede_job
    assert "github.event" not in supersede_job
    # The supersede job runs only after channel promotion succeeds and retains
    # the protected publication environment.
    assert "environment: native-release" in supersede_job
    assert "contents: write" in supersede_job
    assert "SELECTED_TAG: ${{ needs.verify-published-release.outputs.native_tag }}" in supersede_job
    assert "native_release.py supersede-notes" in supersede_job
    assert "gh release edit" in supersede_job
    assert "gh release delete" not in release + freshness
    assert "delete-asset" not in release + freshness
    assert "--clobber" not in supersede_job


def test_updater_feed_is_disabled_until_signing_key_lifecycle_exists() -> None:
    config = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text())

    assert config["plugins"] == {"deep-link": {"desktop": {"schemes": ["openadapt"]}}}
    assert "updater" not in config["plugins"]
    assert config["bundle"]["targets"] == ["dmg", "msi", "nsis", "deb", "appimage"]
    # Target releases inherit APPLE_SIGNING_IDENTITY and keep hardened runtime.
    # The explicit ad-hoc overlay is only for unsigned CI artifacts.
    assert "signingIdentity" not in config["bundle"]["macOS"]
    assert config["bundle"]["macOS"]["entitlements"] == "Entitlements.plist"
    entitlements = (ROOT / "src-tauri" / config["bundle"]["macOS"]["entitlements"]).read_text()
    assert "com.apple.security.cs.disable-library-validation" in entitlements
    adhoc = json.loads((ROOT / "src-tauri/tauri.adhoc.conf.json").read_text())
    assert adhoc["bundle"]["macOS"] == {
        "signingIdentity": "-",
        "hardenedRuntime": False,
    }
    assert config["bundle"]["windows"]["tsp"] is True

    # With no `plugins.updater` config, Tauri hands the updater plugin JSON
    # `null`, its required Config fails to deserialize, and every launch on
    # every platform aborts with PluginInitialization("updater", ...) -- the
    # shipped v0.6.1 DMG bug (issue #26).  While the key above stays forbidden,
    # the plugin registration must stay behind the config-presence gate.
    main_rs = (ROOT / "src-tauri/src/main.rs").read_text()
    assert main_rs.count("tauri_plugin_updater::Builder::new().build()") == 1
    assert 'get("updater")' in main_rs
    guarded = main_rs.split("if updater_configured", 1)
    assert len(guarded) == 2
    assert "tauri_plugin_updater::Builder::new().build()" in guarded[1]


def test_installer_smoke_gates_on_a_real_launch() -> None:
    """Every installer smoke invocation must also prove the app launches.

    The structural install/uninstall lifecycle cannot see startup panics, so
    each smoke_test_native_installer.py call in the CI and release lanes must
    pass --launch-seconds (issue #26).
    """

    for workflow in ("build.yml", "native-release.yml"):
        text = (ROOT / ".github/workflows" / workflow).read_text()
        invocations = text.count("smoke_test_native_installer.py")
        assert invocations >= 3, workflow
        assert text.count("--launch-seconds") == invocations, workflow
        # Headless Linux launches need a display server and a session bus for
        # WebKitGTK and the ayatana tray indicator.
        assert "xvfb-run" in text, workflow
        assert "dbus-run-session" in text, workflow
        assert "WEBKIT_DISABLE_COMPOSITING_MODE" in text, workflow


def test_native_tag_is_distinct_from_python_release_channel() -> None:
    tag = f"desktop-v{native_version()}"
    assert validate_tag(tag) == tag
    with pytest.raises(ValueError, match="desktop-v"):
        validate_tag("v0.3.2")


def _write_source(path: Path, text: str) -> None:
    """Write UTF-8 with LF endings, so a fixture is identical on every platform."""

    path.write_bytes(text.encode("utf-8"))


def _write_native_version_fixture(root: Path, version: str) -> None:
    (root / "src-tauri").mkdir()
    _write_source(
        root / "package.json",
        json.dumps({"name": "openadapt-desktop", "version": version}, indent=2) + "\n",
    )
    _write_source(
        root / "package-lock.json",
        json.dumps(
            {
                "name": "openadapt-desktop",
                "version": version,
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "openadapt-desktop", "version": version},
                    "node_modules/left-pad": {"version": "1.3.0"},
                },
            },
            indent=2,
        )
        + "\n",
    )
    _write_source(
        root / "src-tauri/tauri.conf.json",
        json.dumps({"productName": "OpenAdapt Desktop", "version": version}, indent=2) + "\n",
    )
    _write_source(
        root / "src-tauri/Cargo.toml",
        f'[package]\nname = "openadapt-desktop"\nversion = "{version}"\nedition = "2021"\n'
        '\n[dependencies]\nserde = { version = "1.0" }\n',
    )
    _write_source(
        root / "src-tauri/Cargo.lock",
        'version = 4\n\n[[package]]\nname = "openadapt-desktop"\n'
        f'version = "{version}"\ndependencies = []\n'
        '\n[[package]]\nname = "serde"\nversion = "1.0.200"\n',
    )


def test_set_native_version_synchronizes_every_source_and_lockfile(tmp_path: Path) -> None:
    _write_native_version_fixture(tmp_path, "0.1.1")

    versions = set_native_version("0.5.0", tmp_path)

    assert set(versions.values()) == {"0.5.0"}
    assert native_version(tmp_path) == "0.5.0"
    lock = json.loads((tmp_path / "package-lock.json").read_text())
    assert lock["version"] == "0.5.0"
    assert lock["packages"][""]["version"] == "0.5.0"
    assert lock["packages"]["node_modules/left-pad"]["version"] == "1.3.0"
    cargo_lock = (tmp_path / "src-tauri/Cargo.lock").read_text()
    assert 'name = "openadapt-desktop"\nversion = "0.5.0"' in cargo_lock
    assert 'name = "serde"\nversion = "1.0.200"' in cargo_lock
    cargo_toml = (tmp_path / "src-tauri/Cargo.toml").read_text()
    assert 'version = "0.5.0"' in cargo_toml
    assert 'serde = { version = "1.0" }' in cargo_toml
    assert validate_tag("desktop-v0.5.0", tmp_path) == "desktop-v0.5.0"


def test_set_native_version_writes_utf8_lf_bytes_on_every_platform(tmp_path: Path) -> None:
    """The transform must be byte-deterministic, not platform-dependent.

    `validate_git_version_transform` reconstructs this transform and compares
    the result with Git blobs, which store LF. Text-mode writes rewrite every
    newline as CRLF on Windows and decode with the locale default, so the
    comparison fails there for reasons that have nothing to do with the tag.
    """

    _write_native_version_fixture(tmp_path, "0.1.1")

    set_native_version("0.5.0", tmp_path)

    for relative in (
        "package.json",
        "package-lock.json",
        "src-tauri/Cargo.toml",
        "src-tauri/Cargo.lock",
        "src-tauri/tauri.conf.json",
    ):
        raw = (tmp_path / relative).read_bytes()
        assert b"\r\n" not in raw, relative
        assert raw.decode("utf-8")


def test_sync_native_version_from_engine_uses_python_release_version(tmp_path: Path) -> None:
    _write_native_version_fixture(tmp_path, "0.1.1")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "openadapt-desktop"\nversion = "0.5.0"\n',
        encoding="utf-8",
    )

    versions = sync_native_version_from_engine(tmp_path)

    assert set(versions.values()) == {"0.5.0"}
    assert native_version(tmp_path) == "0.5.0"


def test_set_native_version_rejects_non_semver_input(tmp_path: Path) -> None:
    _write_native_version_fixture(tmp_path, "0.1.1")
    for bad in ("v0.5.0", "0.5", "0.5.0-rc.1", "0.5.0;rm -rf /"):
        with pytest.raises(ValueError, match="X.Y.Z"):
            set_native_version(bad, tmp_path)
    assert native_version(tmp_path) == "0.1.1"


def _git(tmp_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_git_version_transform_requires_the_exact_reconstructed_tree(tmp_path: Path) -> None:
    _write_native_version_fixture(tmp_path, "0.1.1")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.com")
    # Store the fixture bytes verbatim. Git for Windows defaults to
    # core.autocrlf=true, which would rewrite the blobs the transform is
    # compared against.
    _git(tmp_path, "config", "core.autocrlf", "false")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")

    set_native_version("0.5.0", tmp_path)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "exact version transform")
    exact = _git(tmp_path, "rev-parse", "HEAD")
    assert validate_git_version_transform(base, exact, "0.5.0", root=tmp_path) == 5

    package = json.loads((tmp_path / "package.json").read_text())
    package["scripts"] = {"postinstall": "run-unreviewed-code"}
    _write_source(tmp_path / "package.json", json.dumps(package, indent=2) + "\n")
    _git(tmp_path, "add", "package.json")
    _git(tmp_path, "commit", "-qm", "tamper inside allowed file")
    tampered = _git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="deterministic set-version output"):
        validate_git_version_transform(base, tampered, "0.5.0", root=tmp_path)

    (tmp_path / "unexpected.py").write_text("print('not a version change')\n")
    _git(tmp_path, "add", "unexpected.py")
    _git(tmp_path, "commit", "-qm", "tamper outside allowed files")
    unexpected = _git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="outside the version transformation"):
        validate_git_version_transform(base, unexpected, "0.5.0", root=tmp_path)


def test_git_version_advance_refuses_a_stale_or_equal_target(tmp_path: Path) -> None:
    _write_native_version_fixture(tmp_path, "1.4.0")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.com")
    # Store the fixture bytes verbatim. Git for Windows defaults to
    # core.autocrlf=true, which would rewrite the blobs the transform is
    # compared against.
    _git(tmp_path, "config", "core.autocrlf", "false")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")

    set_native_version("1.5.0", tmp_path)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "advance")
    candidate = _git(tmp_path, "rev-parse", "HEAD")
    assert validate_git_version_advance(base, candidate, "1.5.0", root=tmp_path) == 5

    for stale in ("1.4.0", "1.3.9"):
        with pytest.raises(ValueError, match="does not advance protected base"):
            validate_git_version_advance(base, candidate, stale, root=tmp_path)


def test_native_tag_tuple_orders_versions_and_rejects_foreign_tags() -> None:
    assert native_tag_tuple("desktop-v0.10.2") == (0, 10, 2)
    assert native_tag_tuple("desktop-v0.9.9") < native_tag_tuple("desktop-v0.10.0")
    for bad in ("v0.5.0", "desktop-v0.5", "desktop-v0.5.0-rc.1", "desktop-0.5.0"):
        with pytest.raises(ValueError):
            native_tag_tuple(bad)


def test_superseded_notes_prepends_marker_and_preserves_body() -> None:
    body = "<!-- installer-release -->\n\n# Native Release Candidates\n\nDetails.\n"

    updated = superseded_notes(body, "desktop-v0.5.0", "OpenAdaptAI/openadapt-desktop")

    assert updated is not None
    assert updated.startswith("<!-- openadapt-superseded-by: desktop-v0.5.0 -->\n")
    assert "**Superseded by [desktop-v0.5.0]" in updated
    assert "do not use" in updated
    assert "releases/tag/desktop-v0.5.0" in updated
    assert updated.endswith(body)


def test_superseded_notes_is_idempotent_and_upgrades_to_newer_pointer() -> None:
    body = "<!-- installer-release -->\n\nDetails.\n"
    once = superseded_notes(body, "desktop-v0.5.0", "OpenAdaptAI/openadapt-desktop")
    assert once is not None

    assert superseded_notes(once, "desktop-v0.5.0", "OpenAdaptAI/openadapt-desktop") is None
    assert superseded_notes(once, "desktop-v0.4.0", "OpenAdaptAI/openadapt-desktop") is None

    upgraded = superseded_notes(once, "desktop-v0.6.0", "OpenAdaptAI/openadapt-desktop")
    assert upgraded is not None
    assert upgraded.count("openadapt-superseded-by") == 1
    assert "desktop-v0.6.0" in upgraded
    assert upgraded.endswith(body)


def test_installer_pointer_prepends_block_and_preserves_engine_notes() -> None:
    body = "## v0.5.0 (2026-07-26)\n\n### Bug Fixes\n\n- Something.\n"

    updated = installer_pointer_notes(body, "desktop-v0.5.0", "OpenAdaptAI/openadapt-desktop")

    assert updated is not None
    assert updated.startswith("<!-- openadapt-installer-pointer:start -->\n")
    assert "<!-- openadapt-installer-pointer:end -->" in updated
    # Names the canonical native tag, the formats, and the verification path.
    assert "releases/tag/desktop-v0.5.0" in updated
    assert "SHA256SUMS" in updated
    assert "gh attestation verify" in updated
    assert "RELEASES.md" in updated
    # The installers are now mirrored here, so the block must not claim the
    # engine release has none -- and it must still lead with the signing state
    # so the copy on "Latest" cannot read as a maturity promotion.
    assert "this release has no installer" not in updated.lower()
    assert "attached" in updated.lower()
    assert "macOS requires Developer ID plus notarization" in updated
    assert "Windows requires\n> timestamped Authenticode" in updated
    assert "Linux DEB and AppImage bytes require GitHub" in updated
    # The original engine notes survive verbatim.
    assert updated.endswith(body)


def test_installer_pointer_is_idempotent_and_retargets_a_newer_native_tag() -> None:
    body = "## v0.5.0\n\nNotes.\n"
    once = installer_pointer_notes(body, "desktop-v0.5.0", "OpenAdaptAI/openadapt-desktop")
    assert once is not None

    # Re-running the same publish must not append a second block.
    assert installer_pointer_notes(once, "desktop-v0.5.0", "OpenAdaptAI/openadapt-desktop") is None

    retargeted = installer_pointer_notes(once, "desktop-v0.6.0", "OpenAdaptAI/openadapt-desktop")
    assert retargeted is not None
    assert retargeted.count("openadapt-installer-pointer:start") == 1
    assert retargeted.count("openadapt-installer-pointer:end") == 1
    assert "desktop-v0.5.0" not in retargeted
    assert retargeted.endswith(body)


def test_installer_pointer_refuses_a_malformed_or_truncated_block() -> None:
    with pytest.raises(ValueError):
        installer_pointer_notes("x", "v0.5.0", "OpenAdaptAI/openadapt-desktop")

    truncated = "<!-- openadapt-installer-pointer:start -->\nhalf a block\n"
    with pytest.raises(ValueError):
        installer_pointer_notes(truncated, "desktop-v0.5.0", "OpenAdaptAI/openadapt-desktop")


def test_native_release_workflow_points_latest_at_the_published_installers() -> None:
    workflow = _workflow("native-release.yml")
    job = workflow["jobs"]["point-engine-release"]
    assert set(job["needs"]) == {
        "verify-published-release",
        "mirror-installers-to-engine-release",
    }
    assert job["permissions"] == {"contents": "write"}
    steps = _job_steps(job)
    assert steps["actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"]["with"] == {
        "name": "${{ needs.verify-published-release.outputs.verified_artifact }}",
        "path": "mirror",
    }
    script = steps["Prepend an idempotent installer pointer to the engine release"]["run"]
    assert "validate-engine-release" in script
    assert "installer-pointer-notes" in script
    assert "gh release edit" in script


def test_native_release_workflow_mirrors_installers_onto_the_engine_release() -> None:
    """/releases/latest must carry installer BYTES, not only a link.

    A notes-only pointer still shows a visitor a wheel and an sdist. This job
    copies the attested set onto vX.Y.Z. Three invariants make that safe, and
    all three are asserted here because losing any one of them silently turns a
    convenience copy into a maturity overstatement.
    """
    workflow = _workflow("native-release.yml")
    job = workflow["jobs"]["mirror-installers-to-engine-release"]
    assert job["needs"] == "verify-published-release"
    assert job["permissions"] == {
        "actions": "read",
        "attestations": "write",
        "contents": "write",
        "id-token": "write",
    }
    steps = _job_steps(job)
    assert steps["actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"]["with"] == {
        "name": "${{ needs.verify-published-release.outputs.verified_artifact }}",
        "path": "mirror",
    }
    mirror = steps["Mirror and re-download the exact verified asset set"]["run"]
    assert "validate-engine-release" in mirror
    assert "verify-checksums" in mirror
    assert "OpenAdapt-Desktop-desktop-v*.cyclonedx.json" in mirror
    assert "cmp mirror/SHA256SUMS remote-mirror/SHA256SUMS" in mirror
    assert 'gh release upload "${engine_tag}" mirror/* --clobber' in mirror
    assert "gh release edit" not in mirror
    assert "--prerelease" not in mirror
    assert steps["Attest the verified release index"]["with"] == {
        "subject-path": VERIFIED_RELEASE_INDEX
    }


@pytest.mark.parametrize(
    ("platform", "signing", "files", "expected_suffixes"),
    [
        ("macos", "adhoc", ["dmg/App_0.1.1_aarch64.dmg"], [".dmg"]),
        (
            "windows",
            "unsigned",
            ["msi/App_0.1.1_x64_en-US.msi", "nsis/App_0.1.1_x64-setup.exe"],
            [".msi", "-nsis-setup.exe"],
        ),
        (
            "linux",
            "unsigned",
            ["deb/app_0.1.1_amd64.deb", "appimage/App_0.1.1_amd64.AppImage"],
            [".deb", ".AppImage"],
        ),
    ],
)
def test_stage_artifacts_renames_and_labels_candidate(
    tmp_path: Path,
    platform: str,
    signing: str,
    files: list[str],
    expected_suffixes: list[str],
) -> None:
    bundle = tmp_path / "bundle"
    for relative in files:
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())

    output = tmp_path / "staged"
    staged = stage_artifacts(
        bundle_root=bundle,
        output=output,
        platform=platform,
        architecture="x86_64",
        signing=signing,
    )
    asset_names = [path.name for path in staged if path.suffix != ".json"]
    assert len(asset_names) == len(expected_suffixes)
    current_version = native_version()
    assert all(f"Candidate-v{current_version}" in name for name in asset_names)
    assert all(any(name.endswith(suffix) for name in asset_names) for suffix in expected_suffixes)

    metadata_path = next(path for path in staged if path.suffix == ".json")
    metadata = json.loads(metadata_path.read_text())
    assert metadata["native_version"] == current_version
    assert metadata["lifecycle"] == "Candidate"
    assert metadata["surface"] == "installed desktop pairing and authoring companion"
    assert metadata["verification_scope"] == (
        "cross-platform install/uninstall, self-contained Flow runtime, "
        "browser provision, and protocol-handler packaging"
    )
    assert metadata["limitations"] == [
        (
            "The first browser workflow downloads the Chromium revision pinned by the "
            "bundled Playwright runtime unless PLAYWRIGHT_BROWSERS_PATH points at an "
            "approved offline prebundle."
        ),
        "Installer verification does not replace qualification of a complete real workflow.",
    ]
    assert metadata["artifacts"] == asset_names


def test_stage_rejects_missing_duplicate_and_wrong_signing(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "dmg").mkdir(parents=True)
    (bundle / "dmg" / "one.dmg").write_bytes(b"one")
    (bundle / "dmg" / "two.dmg").write_bytes(b"two")

    with pytest.raises(ValueError, match="exactly one"):
        stage_artifacts(
            bundle_root=bundle,
            output=tmp_path / "duplicate",
            platform="macos",
            architecture="arm64",
            signing="adhoc",
        )
    with pytest.raises(ValueError, match="invalid signing mode"):
        stage_artifacts(
            bundle_root=bundle,
            output=tmp_path / "wrong-mode",
            platform="macos",
            architecture="arm64",
            signing="unsigned",
        )


def test_checksum_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"alpha")
    (tmp_path / "b.bin").write_bytes(b"candidate")
    manifest = tmp_path / "SHA256SUMS"

    entries = write_checksums(tmp_path, manifest)
    assert entries == sorted(entries, key=lambda entry: entry[1])
    assert (
        dict((name, digest) for digest, name in entries)["a.bin"]
        == hashlib.sha256(b"alpha").hexdigest()
    )
    assert verify_checksums(tmp_path, manifest) == 2

    (tmp_path / "a.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_checksums(tmp_path, manifest)


def test_checksum_verification_rejects_an_unlisted_release_asset(tmp_path: Path) -> None:
    (tmp_path / "installer.bin").write_bytes(b"installer")
    manifest = tmp_path / "SHA256SUMS"
    write_checksums(tmp_path, manifest)

    (tmp_path / "unlisted.bin").write_bytes(b"not attested")

    with pytest.raises(ValueError, match="exact release file set"):
        verify_checksums(tmp_path, manifest)


def _release(tag: str, *, marked: bool = True, draft: bool = False) -> dict:
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": True,
        "body": "<!-- installer-release -->\n" if marked else "Candidate installer",
    }


def test_release_selection_uses_semver_and_ignores_event_order() -> None:
    releases = [
        _release("desktop-v1.9.9"),
        _release("desktop-v1.11.0"),
        _release("desktop-v1.10.7"),
        _release("desktop-v9.0.0", marked=False),
        _release("desktop-v8.0.0", draft=True),
    ]

    # A late publish event for 1.9.9 must still select 1.11.0 for every write.
    assert select_latest_native_release(releases)["tag_name"] == "desktop-v1.11.0"


def _ls_remote(*tags: str) -> str:
    return "".join(f"{'a' * 40}\trefs/tags/{tag}\n" for tag in tags)


def test_release_order_refuses_a_tag_below_the_immutable_tag_namespace() -> None:
    tags = native_release_tags(_ls_remote("desktop-v1.11.0", "v1.11.0"))
    assert tags == ["desktop-v1.11.0"]

    with pytest.raises(ValueError, match="is below existing native tag"):
        validate_native_tag_order("desktop-v1.10.9", tags)
    assert validate_native_tag_order("desktop-v1.11.1", tags) == "desktop-v1.11.1"
    # The tag write is idempotent, so a re-run legitimately presents the tag
    # that already leads. That is still monotonic.
    assert validate_native_tag_order("desktop-v1.11.0", tags) == "desktop-v1.11.0"
    assert validate_native_tag_order("desktop-v0.1.0", []) == "desktop-v0.1.0"


def test_release_order_ignores_mutable_release_metadata() -> None:
    """A release that loses its marker or prerelease flag stays in the order.

    ``_published_native_releases`` drops such a release. The tag namespace
    cannot, because ``gh release edit`` never touches a Git tag.
    """

    tags = native_release_tags(_ls_remote("desktop-v1.11.0"))
    unmarked = [_release("desktop-v1.11.0", marked=False)]

    with pytest.raises(ValueError, match="no published marked native prerelease"):
        select_latest_native_release(unmarked)
    with pytest.raises(ValueError, match="is below existing native tag"):
        validate_native_tag_order("desktop-v1.0.0", tags)


def test_native_tag_namespace_parsing_fails_closed() -> None:
    # An annotated tag adds a peeled line for the same tag.
    peeled = f"{'b' * 40}\trefs/tags/desktop-v2.0.0^{{}}\n"
    assert native_release_tags(_ls_remote("desktop-v2.0.0") + peeled) == ["desktop-v2.0.0"]

    with pytest.raises(ValueError, match="invalid line"):
        native_release_tags("not-an-object-id\trefs/tags/desktop-v1.0.0\n")
    with pytest.raises(ValueError, match="not restricted to tags"):
        native_release_tags(f"{'a' * 40}\trefs/heads/main\n")
    with pytest.raises(ValueError, match="native release tag"):
        native_release_tags(_ls_remote("desktop-v1.2"))
    with pytest.raises(ValueError, match="list of tag names"):
        validate_native_tag_order("desktop-v1.0.0", {"desktop-v2.0.0": True})


def test_release_order_reads_the_tag_namespace_in_every_workflow() -> None:
    body = (ROOT / ".github" / "workflows" / "native-release.yml").read_text()
    order = body.split("validate-release-order", 1)[1].split("\n\n", 1)[0]
    assert '--candidate-tag "${native_tag}"' in order
    assert "--tags remote-native-tags.txt" in order
    assert "git ls-remote --tags origin > remote-native-tags.txt" in body
    assert "--releases" not in order


def test_marked_native_release_with_malformed_tag_fails_closed() -> None:
    with pytest.raises(ValueError, match="native release tag"):
        select_latest_native_release([_release("desktop-v1.2")])


def test_release_provenance_rejects_modified_workflow_or_source(tmp_path: Path) -> None:
    tag = f"desktop-v{native_version()}"
    common = {
        "repository": "OpenAdaptAI/openadapt-desktop",
        "tag": tag,
        "source_commit": "a" * 40,
        "workflow_ref": (
            "OpenAdaptAI/openadapt-desktop/.github/workflows/"
            f"native-release.yml@refs/tags/{tag}"
        ),
        "workflow_commit": "a" * 40,
        "run_id": 123456,
        "run_attempt": 1,
        "runner_environment": "github-hosted",
        "engine_tag": f"v{native_version()}",
        "engine_commit": "b" * 40,
        "engine_release_id": 654321,
        "engine_release_url": (
            f"https://github.com/OpenAdaptAI/openadapt-desktop/releases/tag/v{native_version()}"
        ),
    }
    with pytest.raises(ValueError, match="workflow ref"):
        write_release_provenance(
            tmp_path / NATIVE_RELEASE_PROVENANCE,
            **{**common, "workflow_ref": "OpenAdaptAI/openadapt-desktop/evil.yml@main"},
        )
    with pytest.raises(ValueError, match="workflow commit"):
        write_release_provenance(
            tmp_path / NATIVE_RELEASE_PROVENANCE,
            **{**common, "workflow_commit": "b" * 40},
        )
    with pytest.raises(ValueError, match="GitHub-hosted"):
        write_release_provenance(
            tmp_path / NATIVE_RELEASE_PROVENANCE,
            **{**common, "runner_environment": "self-hosted"},
        )


def _attestation_record(provenance: dict, checksums: dict[str, str]) -> dict:
    repository = provenance["repository"]
    commit = provenance["source_commit"]
    repository_url = f"https://github.com/{repository}"
    workflow_ref = f"refs/tags/{provenance['source_tag']}"
    workflow_uri = f"{repository_url}/.github/workflows/native-release.yml@{workflow_ref}"
    invocation = (
        f"{repository_url}/actions/runs/{provenance['run_id']}/attempts/{provenance['run_attempt']}"
    )
    return {
        "verificationResult": {
            "signature": {
                "certificate": {
                    "subjectAlternativeName": workflow_uri,
                    "githubWorkflowTrigger": "push",
                    "githubWorkflowSHA": commit,
                    "githubWorkflowName": "Native Installer Release",
                    "githubWorkflowRepository": repository,
                    "githubWorkflowRef": workflow_ref,
                    "buildSignerURI": workflow_uri,
                    "buildSignerDigest": commit,
                    "runnerEnvironment": "github-hosted",
                    "sourceRepositoryURI": repository_url,
                    "sourceRepositoryDigest": commit,
                    "sourceRepositoryRef": workflow_ref,
                    "buildConfigURI": workflow_uri,
                    "buildConfigDigest": commit,
                    "buildTrigger": "push",
                    "runInvocationURI": invocation,
                }
            },
            "statement": {
                "subject": [
                    {"name": name, "digest": {"sha256": digest}}
                    for name, digest in sorted(checksums.items())
                ],
                "predicate": {
                    "buildDefinition": {
                        "buildType": "https://actions.github.io/buildtypes/workflow/v1",
                        "externalParameters": {
                            "workflow": {
                                "path": ".github/workflows/native-release.yml",
                                "ref": workflow_ref,
                                "repository": repository_url,
                            }
                        },
                        "internalParameters": {
                            "github": {
                                "event_name": "push",
                                "runner_environment": "github-hosted",
                            }
                        },
                        "resolvedDependencies": [
                            {
                                "digest": {"gitCommit": commit},
                                "uri": f"git+{repository_url}@{workflow_ref}",
                            }
                        ],
                    },
                    "runDetails": {
                        "builder": {"id": workflow_uri},
                        "metadata": {"invocationId": invocation},
                    },
                },
            },
        }
    }


def test_release_provenance_attestation_and_workflow_run_bind_exact_identity(
    tmp_path: Path,
) -> None:
    release, _manifest, checksums_path = _stage_complete_release(tmp_path)
    tag = f"desktop-v{native_version()}"
    commit = "a" * 40
    repository = "OpenAdaptAI/openadapt-desktop"
    provenance_path = release / NATIVE_RELEASE_PROVENANCE
    provenance = validate_release_provenance(
        provenance_path,
        repository=repository,
        tag=tag,
        source_commit=commit,
    )
    attestation = tmp_path / "attestation.json"
    attestation.write_text(
        json.dumps([_attestation_record(provenance, dict(_read_checksum_lines(checksums_path)))]),
        encoding="utf-8",
    )

    assert (
        validate_release_attestation(
            attestation,
            directory=release,
            checksums=checksums_path,
            provenance_path=provenance_path,
            repository=repository,
            tag=tag,
            source_commit=commit,
        )
        == provenance
    )

    workflow_run = tmp_path / "workflow-run.json"
    required_jobs = [
        "Validate the exact tagged native source",
        "macOS arm64",
        "macOS x86_64",
        "Windows x86_64",
        "Linux x86_64 (GitHub-attested bytes)",
        "Checksum and attest exact release bytes",
        "Publish the verified candidate prerelease",
    ]
    workflow_payload = {
        "databaseId": 123456,
        "attempt": 1,
        "conclusion": "success",
        "event": "push",
        "headBranch": tag,
        "headSha": commit,
        "name": "Native Installer Release",
        "status": "completed",
        "workflowName": "Native Installer Release",
        "url": "https://github.com/OpenAdaptAI/openadapt-desktop/actions/runs/123456",
        "jobs": [
            {"name": name, "status": "completed", "conclusion": "success"} for name in required_jobs
        ],
    }
    workflow_run.write_text(json.dumps(workflow_payload), encoding="utf-8")
    assert validate_release_workflow_run(workflow_run, provenance=provenance) == 7


def _read_checksum_lines(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        assert separator
        entries.append((name, digest))
    return entries


@pytest.mark.parametrize(
    ("target", "field", "value", "message"),
    [
        ("certificate", "githubWorkflowSHA", "b" * 40, "githubWorkflowSHA differs"),
        ("certificate", "runnerEnvironment", "self-hosted", "runnerEnvironment differs"),
        ("workflow", "path", ".github/workflows/evil.yml", "workflow identity differs"),
    ],
)
def test_release_attestation_rejects_modified_signed_claims(
    tmp_path: Path, target: str, field: str, value: str, message: str
) -> None:
    release, _manifest, checksums_path = _stage_complete_release(tmp_path)
    tag = f"desktop-v{native_version()}"
    commit = "a" * 40
    repository = "OpenAdaptAI/openadapt-desktop"
    provenance_path = release / NATIVE_RELEASE_PROVENANCE
    provenance = validate_release_provenance(
        provenance_path,
        repository=repository,
        tag=tag,
        source_commit=commit,
    )
    record = _attestation_record(provenance, dict(_read_checksum_lines(checksums_path)))
    statement = record["verificationResult"]["statement"]
    if target == "certificate":
        record["verificationResult"]["signature"]["certificate"][field] = value
    else:
        statement["predicate"]["buildDefinition"]["externalParameters"]["workflow"][field] = value
    attestation = tmp_path / "attestation.json"
    attestation.write_text(json.dumps([record]), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        validate_release_attestation(
            attestation,
            directory=release,
            checksums=checksums_path,
            provenance_path=provenance_path,
            repository=repository,
            tag=tag,
            source_commit=commit,
        )


def test_release_attestation_rejects_a_forged_subject_inventory(tmp_path: Path) -> None:
    release, _manifest, checksums_path = _stage_complete_release(tmp_path)
    tag = f"desktop-v{native_version()}"
    commit = "a" * 40
    repository = "OpenAdaptAI/openadapt-desktop"
    provenance_path = release / NATIVE_RELEASE_PROVENANCE
    provenance = validate_release_provenance(
        provenance_path,
        repository=repository,
        tag=tag,
        source_commit=commit,
    )
    record = _attestation_record(provenance, dict(_read_checksum_lines(checksums_path)))
    record["verificationResult"]["statement"]["subject"][0]["digest"]["sha256"] = "f" * 64
    attestation = tmp_path / "attestation.json"
    attestation.write_text(json.dumps([record]), encoding="utf-8")

    with pytest.raises(ValueError, match="subjects differ"):
        validate_release_attestation(
            attestation,
            directory=release,
            checksums=checksums_path,
            provenance_path=provenance_path,
            repository=repository,
            tag=tag,
            source_commit=commit,
        )


def test_workflow_run_rejects_missing_protected_publish_success(tmp_path: Path) -> None:
    release, _manifest, _checksums = _stage_complete_release(tmp_path)
    provenance = validate_release_provenance(
        release / NATIVE_RELEASE_PROVENANCE,
        repository="OpenAdaptAI/openadapt-desktop",
        tag=f"desktop-v{native_version()}",
        source_commit="a" * 40,
    )
    path = tmp_path / "failed-run.json"
    path.write_text(
        json.dumps(
            {
                "databaseId": 123456,
                "attempt": 1,
                "conclusion": "success",
                "event": "push",
                "headBranch": provenance["source_tag"],
                "headSha": provenance["source_commit"],
                "name": "Native Installer Release",
                "status": "completed",
                "workflowName": "Native Installer Release",
                "url": ("https://github.com/OpenAdaptAI/openadapt-desktop/actions/runs/123456"),
                "jobs": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="required job"):
        validate_release_workflow_run(path, provenance=provenance)


def test_validate_release_set_requires_every_platform_and_no_extra_files(tmp_path: Path) -> None:
    specifications = [
        ("macos", "arm64", "developer-id-notarized", ["dmg/app-arm.dmg"]),
        ("macos", "x86_64", "developer-id-notarized", ["dmg/app-intel.dmg"]),
        (
            "windows",
            "x86_64",
            "authenticode",
            ["msi/app.msi", "nsis/app-setup.exe"],
        ),
        (
            "linux",
            "x86_64",
            "github-attested",
            ["deb/app.deb", "appimage/app.AppImage"],
        ),
    ]
    release = tmp_path / "release"
    for index, (platform, architecture, signing, files) in enumerate(specifications):
        bundle = tmp_path / f"bundle-{index}"
        for relative in files:
            path = bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode())
        stage = tmp_path / f"stage-{index}"
        staged = stage_artifacts(
            bundle_root=bundle,
            output=stage,
            platform=platform,
            architecture=architecture,
            signing=signing,
        )
        release.mkdir(exist_ok=True)
        for path in staged:
            path.rename(release / path.name)

    assert validate_release_set(release) == 10
    (release / "unexpected.bin").write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="release assets differ"):
        validate_release_set(release)


def test_validate_release_set_refuses_nonproduction_trust_mode(tmp_path: Path) -> None:
    release, _manifest, _checksums = _stage_complete_release(tmp_path)
    linux_metadata = next(release.glob("*-linux-*-metadata.json"))
    payload = json.loads(linux_metadata.read_text(encoding="utf-8"))
    payload["signing"] = "unsigned"
    linux_metadata.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="production release requires linux trust mode"):
        validate_release_set(release)


def _stage_complete_release(tmp_path: Path) -> tuple[Path, Path, Path]:
    specifications = [
        ("macos", "arm64", "developer-id-notarized", ["dmg/app-arm.dmg"]),
        ("macos", "x86_64", "developer-id-notarized", ["dmg/app-intel.dmg"]),
        ("windows", "x86_64", "authenticode", ["msi/app.msi", "nsis/app-setup.exe"]),
        (
            "linux",
            "x86_64",
            "github-attested",
            ["deb/app.deb", "appimage/app.AppImage"],
        ),
    ]
    release = tmp_path / "release"
    release.mkdir(parents=True)
    for index, (platform, architecture, signing, files) in enumerate(specifications):
        bundle = tmp_path / f"bundle-{index}"
        for relative in files:
            path = bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode())
        for path in stage_artifacts(
            bundle_root=bundle,
            output=tmp_path / f"stage-{index}",
            platform=platform,
            architecture=architecture,
            signing=signing,
        ):
            path.rename(release / path.name)
    tag = f"desktop-v{native_version()}"
    sbom = release / f"OpenAdapt-Desktop-{tag}.cyclonedx.json"
    sbom.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "version": 1,
                "metadata": {"tools": [{"name": "Syft"}]},
                "components": [{"name": "openadapt-flow"}],
            }
        ),
        encoding="utf-8",
    )
    output = write_website_release_manifest(release, tag=tag, sbom=sbom)
    (release / NATIVE_RELEASE_VERIFIER).write_text(
        "#!/usr/bin/env python3\nprint('fixture verifier')\n",
        encoding="utf-8",
    )
    source_commit = "a" * 40
    write_release_provenance(
        release / NATIVE_RELEASE_PROVENANCE,
        repository="OpenAdaptAI/openadapt-desktop",
        tag=tag,
        source_commit=source_commit,
        workflow_ref=_native_workflow_ref(tag),
        workflow_commit=source_commit,
        run_id=123456,
        run_attempt=1,
        runner_environment="github-hosted",
        engine_tag=f"v{native_version()}",
        engine_commit="b" * 40,
        engine_release_id=654321,
        engine_release_url=(
            f"https://github.com/OpenAdaptAI/openadapt-desktop/releases/tag/v{native_version()}"
        ),
    )
    checksums = release / "SHA256SUMS"
    write_checksums(release, checksums)
    return release, output, checksums


def _engine_release_file(tmp_path: Path, *, version: str | None = None) -> Path:
    version = version or native_version()
    path = tmp_path / "engine-release.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "databaseId": 654321,
                "isDraft": False,
                "isPrerelease": False,
                "publishedAt": "2026-08-18T12:00:00Z",
                "tagName": f"v{version}",
                "url": (
                    f"https://github.com/OpenAdaptAI/openadapt-desktop/releases/tag/v{version}"
                ),
            }
        ),
        encoding="utf-8",
    )
    return path


def test_engine_release_requires_exact_published_identity(tmp_path: Path) -> None:
    release = _engine_release_file(tmp_path)
    validated = validate_engine_release(
        release,
        repository="OpenAdaptAI/openadapt-desktop",
        engine_tag=f"v{native_version()}",
        engine_commit="b" * 40,
    )
    assert validated["databaseId"] == 654321

    payload = json.loads(release.read_text(encoding="utf-8"))
    payload["isPrerelease"] = True
    release.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="exact published engine release"):
        validate_engine_release(
            release,
            repository="OpenAdaptAI/openadapt-desktop",
            engine_tag=f"v{native_version()}",
            engine_commit="b" * 40,
        )


def test_engine_release_provenance_binds_tag_workflow_and_exact_artifacts(
    tmp_path: Path,
) -> None:
    version = native_version()
    artifacts = tmp_path / "engine-assets"
    artifacts.mkdir()
    (artifacts / f"openadapt_desktop-{version}-py3-none-any.whl").write_bytes(b"wheel")
    (artifacts / f"openadapt_desktop-{version}.tar.gz").write_bytes(b"sdist")
    release = _engine_release_file(tmp_path)
    receipt = write_engine_release_provenance(
        tmp_path / ENGINE_RELEASE_PROVENANCE,
        directory=artifacts,
        release_path=release,
        repository="OpenAdaptAI/openadapt-desktop",
        engine_tag=f"v{version}",
        engine_commit="b" * 40,
        workflow_ref=_engine_workflow_ref(),
        workflow_commit="b" * 40,
        run_id=123,
        run_attempt=1,
        runner_environment="github-hosted",
    )
    validated = validate_engine_release_provenance(
        receipt,
        repository="OpenAdaptAI/openadapt-desktop",
        engine_tag=f"v{version}",
        engine_commit="b" * 40,
        release_path=release,
        directory=artifacts,
    )
    assert validated["workflow_ref"] == _engine_workflow_ref()
    assert {asset["name"] for asset in validated["assets"]} == {
        f"openadapt_desktop-{version}-py3-none-any.whl",
        f"openadapt_desktop-{version}.tar.gz",
    }

    assert validated["workflow_commit"] == "b" * 40

    with pytest.raises(ValueError, match="must equal the reviewed tag commit"):
        write_engine_release_provenance(
            tmp_path / "wrong-source" / ENGINE_RELEASE_PROVENANCE,
            directory=artifacts,
            release_path=release,
            repository="OpenAdaptAI/openadapt-desktop",
            engine_tag=f"v{version}",
            engine_commit="b" * 40,
            workflow_ref=_engine_workflow_ref(),
            workflow_commit="a" * 40,
            run_id=123,
            run_attempt=1,
            runner_environment="github-hosted",
        )

    (artifacts / f"openadapt_desktop-{version}.tar.gz").write_bytes(b"changed")
    with pytest.raises(ValueError, match="differ from provenance"):
        validate_engine_release_provenance(
            receipt,
            repository="OpenAdaptAI/openadapt-desktop",
            engine_tag=f"v{version}",
            engine_commit="b" * 40,
            release_path=release,
            directory=artifacts,
        )


def test_engine_release_provenance_refuses_main_workflow_identity(
    tmp_path: Path,
) -> None:
    version = native_version()
    artifacts = tmp_path / "engine-assets"
    artifacts.mkdir()
    (artifacts / f"openadapt_desktop-{version}-py3-none-any.whl").write_bytes(b"wheel")
    (artifacts / f"openadapt_desktop-{version}.tar.gz").write_bytes(b"sdist")
    with pytest.raises(ValueError, match="workflow ref"):
        write_engine_release_provenance(
            tmp_path / ENGINE_RELEASE_PROVENANCE,
            directory=artifacts,
            release_path=_engine_release_file(tmp_path),
            repository="OpenAdaptAI/openadapt-desktop",
            engine_tag=f"v{version}",
            engine_commit="b" * 40,
            workflow_ref=(
                "OpenAdaptAI/openadapt-desktop/.github/workflows/release.yml@refs/heads/main"
            ),
            workflow_commit="b" * 40,
            run_id=123,
            run_attempt=1,
            runner_environment="github-hosted",
        )


def test_verified_release_index_is_closed_bound_and_monotonic(tmp_path: Path) -> None:
    release, _manifest, checksums = _stage_complete_release(tmp_path / "assets")
    engine_release = _engine_release_file(tmp_path)
    index = write_verified_release_index(
        tmp_path / VERIFIED_RELEASE_INDEX,
        directory=release,
        checksums=checksums,
        provenance_path=release / NATIVE_RELEASE_PROVENANCE,
        repository="OpenAdaptAI/openadapt-desktop",
        tag=f"desktop-v{native_version()}",
        source_commit="a" * 40,
        engine_release_path=engine_release,
    )
    validated = validate_verified_release_index(index)
    assert validated["checksums"]["sha256"] == hashlib.sha256(checksums.read_bytes()).hexdigest()
    assert {asset["name"] for asset in validated["assets"]} == {
        name for name, _digest in _read_checksum_lines(checksums)
    }

    incomplete = json.loads(index.read_text(encoding="utf-8"))
    incomplete["assets"].pop()
    incomplete_path = tmp_path / "incomplete" / VERIFIED_RELEASE_INDEX
    incomplete_path.parent.mkdir()
    incomplete_path.write_text(json.dumps(incomplete), encoding="utf-8")
    with pytest.raises(ValueError, match="complete native asset set"):
        validate_verified_release_index(incomplete_path)

    expanded = json.loads(index.read_text(encoding="utf-8"))
    expanded["assets"].append({"name": "unexpected.txt", "sha256": "0" * 64})
    expanded["assets"].sort(key=lambda asset: asset["name"])
    expanded_path = tmp_path / "expanded" / VERIFIED_RELEASE_INDEX
    expanded_path.parent.mkdir()
    expanded_path.write_text(json.dumps(expanded), encoding="utf-8")
    with pytest.raises(ValueError, match="complete native asset set"):
        validate_verified_release_index(expanded_path)

    changed = json.loads(index.read_text(encoding="utf-8"))
    changed["assets"][0]["sha256"] = "0" * 64
    changed_path = tmp_path / "changed" / VERIFIED_RELEASE_INDEX
    changed_path.parent.mkdir()
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot rewrite an existing native version"):
        write_verified_release_index(
            tmp_path / "retry" / VERIFIED_RELEASE_INDEX,
            directory=release,
            checksums=checksums,
            provenance_path=release / NATIVE_RELEASE_PROVENANCE,
            repository="OpenAdaptAI/openadapt-desktop",
            tag=f"desktop-v{native_version()}",
            source_commit="a" * 40,
            engine_release_path=engine_release,
            existing=changed_path,
        )

    rollback = json.loads(index.read_text(encoding="utf-8"))
    rollback["native_tag"] = "desktop-v99.0.0"
    rollback["native_version"] = "99.0.0"
    rollback["engine_tag"] = "v99.0.0"
    rollback["engine_release_url"] = (
        "https://github.com/OpenAdaptAI/openadapt-desktop/releases/tag/v99.0.0"
    )
    for asset in rollback["assets"]:
        asset["name"] = asset["name"].replace(native_version(), "99.0.0")
    rollback["assets"].sort(key=lambda asset: asset["name"])
    rollback_path = tmp_path / "rollback" / VERIFIED_RELEASE_INDEX
    rollback_path.parent.mkdir()
    rollback_path.write_text(json.dumps(rollback), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot move backwards"):
        write_verified_release_index(
            tmp_path / "retry-rollback" / VERIFIED_RELEASE_INDEX,
            directory=release,
            checksums=checksums,
            provenance_path=release / NATIVE_RELEASE_PROVENANCE,
            repository="OpenAdaptAI/openadapt-desktop",
            tag=f"desktop-v{native_version()}",
            source_commit="a" * 40,
            engine_release_path=engine_release,
            existing=rollback_path,
        )


def test_candidate_release_channel_is_hash_bound_and_strictly_monotonic(
    tmp_path: Path,
) -> None:
    release, _manifest, checksums = _stage_complete_release(tmp_path / "assets")
    index = write_verified_release_index(
        tmp_path / VERIFIED_RELEASE_INDEX,
        directory=release,
        checksums=checksums,
        provenance_path=release / NATIVE_RELEASE_PROVENANCE,
        repository="OpenAdaptAI/openadapt-desktop",
        tag=f"desktop-v{native_version()}",
        source_commit="a" * 40,
        engine_release_path=_engine_release_file(tmp_path),
    )
    channel = write_verified_release_channel(
        tmp_path / VERIFIED_RELEASE_CHANNEL,
        index_path=index,
        repository="OpenAdaptAI/openadapt-desktop",
        workflow_ref=_native_workflow_ref(),
        workflow_commit="a" * 40,
        run_id=123456,
        run_attempt=2,
    )
    validated = validate_verified_release_channel(channel)
    assert validated["verified_index"]["sha256"] == hashlib.sha256(index.read_bytes()).hexdigest()
    assert validated["checksums"]["sha256"] == hashlib.sha256(checksums.read_bytes()).hexdigest()
    assert validated["promotion"]["workflow_ref"] == _native_workflow_ref()

    with pytest.raises(ValueError, match="must equal the native source commit"):
        write_verified_release_channel(
            tmp_path / "wrong-source" / VERIFIED_RELEASE_CHANNEL,
            index_path=index,
            repository="OpenAdaptAI/openadapt-desktop",
            workflow_ref=_native_workflow_ref(),
            workflow_commit="c" * 40,
            run_id=123456,
            run_attempt=2,
        )

    with pytest.raises(ValueError, match="strictly advance"):
        write_verified_release_channel(
            tmp_path / "retry" / VERIFIED_RELEASE_CHANNEL,
            index_path=index,
            repository="OpenAdaptAI/openadapt-desktop",
            workflow_ref=_native_workflow_ref(),
            workflow_commit="a" * 40,
            run_id=123457,
            run_attempt=1,
            existing=channel,
        )


def test_candidate_channel_accepts_the_legacy_selector_for_one_way_migration(
    tmp_path: Path,
) -> None:
    release, _manifest, checksums = _stage_complete_release(tmp_path / "assets")
    index = write_verified_release_index(
        tmp_path / VERIFIED_RELEASE_INDEX,
        directory=release,
        checksums=checksums,
        provenance_path=release / NATIVE_RELEASE_PROVENANCE,
        repository=DESKTOP_REPOSITORY,
        tag=f"desktop-v{native_version()}",
        source_commit="a" * 40,
        engine_release_path=_engine_release_file(tmp_path),
    )
    (tmp_path / "current").mkdir()
    channel = write_verified_release_channel(
        tmp_path / "current" / VERIFIED_RELEASE_CHANNEL,
        index_path=index,
        repository=DESKTOP_REPOSITORY,
        workflow_ref=_native_workflow_ref(),
        workflow_commit="a" * 40,
        run_id=123456,
        run_attempt=2,
    )
    legacy_value = json.loads(channel.read_text(encoding="utf-8"))
    legacy_value["schema"] = "openadapt.desktop-release-channel/v1"
    legacy_value["channel"] = "stable-native"
    legacy_value["promotion"]["workflow_ref"] = (
        f"{DESKTOP_REPOSITORY}/.github/workflows/native-release.yml@refs/heads/main"
    )
    legacy_value["promotion"]["event"] = "workflow_dispatch"
    legacy = tmp_path / "legacy" / VERIFIED_RELEASE_CHANNEL
    legacy.parent.mkdir()
    legacy.write_text(json.dumps(legacy_value), encoding="utf-8")

    assert validate_verified_release_channel(legacy)["schema"].endswith("/v1")
    assert download_verifier.validate_channel(legacy)["channel"] == "stable-native"


def test_release_channel_refuses_main_origin_promotion(tmp_path: Path) -> None:
    release, _manifest, checksums = _stage_complete_release(tmp_path / "assets")
    index = write_verified_release_index(
        tmp_path / VERIFIED_RELEASE_INDEX,
        directory=release,
        checksums=checksums,
        provenance_path=release / NATIVE_RELEASE_PROVENANCE,
        repository="OpenAdaptAI/openadapt-desktop",
        tag=f"desktop-v{native_version()}",
        source_commit="a" * 40,
        engine_release_path=_engine_release_file(tmp_path),
    )
    with pytest.raises(ValueError, match="workflow ref"):
        write_verified_release_channel(
            tmp_path / VERIFIED_RELEASE_CHANNEL,
            index_path=index,
            repository="OpenAdaptAI/openadapt-desktop",
            workflow_ref=(
                "OpenAdaptAI/openadapt-desktop/.github/workflows/"
                "native-release.yml@refs/heads/main"
            ),
            workflow_commit="a" * 40,
            run_id=123456,
            run_attempt=1,
        )


def test_public_download_verifier_refuses_an_expanded_inventory(tmp_path: Path) -> None:
    asset = tmp_path / "installer.bin"
    asset.write_bytes(b"installer")
    checksums = tmp_path / "SHA256SUMS"
    checksums.write_text(
        f"{hashlib.sha256(asset.read_bytes()).hexdigest()}  {asset.name}\n",
        encoding="utf-8",
    )
    assert verify_download_inventory(tmp_path, checksums) == 1

    (tmp_path / "extra.bin").write_bytes(b"not signed")
    with pytest.raises(ValueError, match="do not equal"):
        verify_download_inventory(tmp_path, checksums)


def test_public_download_verifier_authenticates_complete_channel_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release, _manifest, checksums = _stage_complete_release(tmp_path / "assets")
    repository = "OpenAdaptAI/openadapt-desktop"
    index = write_verified_release_index(
        tmp_path / VERIFIED_RELEASE_INDEX,
        directory=release,
        checksums=checksums,
        provenance_path=release / NATIVE_RELEASE_PROVENANCE,
        repository=repository,
        tag=f"desktop-v{native_version()}",
        source_commit="a" * 40,
        engine_release_path=_engine_release_file(tmp_path),
    )
    channel = write_verified_release_channel(
        tmp_path / VERIFIED_RELEASE_CHANNEL,
        index_path=index,
        repository=repository,
        workflow_ref=_native_workflow_ref(),
        workflow_commit="a" * 40,
        run_id=123456,
        run_attempt=2,
    )
    commands: list[list[str]] = []

    def _record(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "verified", "")

    monkeypatch.setattr(download_verifier.subprocess, "run", _record)

    assert verify_authenticated_channel(
        channel_path=channel,
        index_path=index,
        directory=release,
        checksums=checksums,
        minimum_version=native_version(),
        repository=repository,
    ) == len(_read_checksum_lines(checksums))
    assert len(commands) == 3
    assert all(command[:3] == ["gh", "attestation", "verify"] for command in commands)
    assert all("--deny-self-hosted-runners" in command for command in commands)
    tag_identity = (
        "https://github.com/OpenAdaptAI/openadapt-desktop/"
        ".github/workflows/native-release.yml@"
        f"refs/tags/desktop-v{native_version()}"
    )
    assert commands[0][commands[0].index("--cert-identity") + 1] == tag_identity
    assert commands[1][commands[1].index("--cert-identity") + 1] == tag_identity
    assert commands[2][commands[2].index("--cert-identity") + 1] == tag_identity
    # `gh attestation verify` refuses the command outright when two flags from
    # this group are set, so a second one would make every call fail.
    exclusive = {"--cert-identity", "--cert-identity-regex", "--signer-repo", "--signer-workflow"}
    for command in commands:
        assert exclusive.intersection(command) == {"--cert-identity"}
        issuer = command[command.index("--cert-oidc-issuer") + 1]
        assert issuer == "https://token.actions.githubusercontent.com"

    with pytest.raises(ValueError, match="below the trusted minimum"):
        verify_authenticated_channel(
            channel_path=channel,
            index_path=index,
            directory=release,
            checksums=checksums,
            minimum_version="999.0.0",
            repository=repository,
        )


def test_public_download_verifier_uses_the_authenticated_checksum_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local writer cannot swap SHA256SUMS after it is authenticated.

    The installer digests must come from the exact bytes the attestation check
    hashed, not from a later read of the file on disk.
    """

    release, _manifest, checksums = _stage_complete_release(tmp_path / "assets")
    repository = "OpenAdaptAI/openadapt-desktop"
    index = write_verified_release_index(
        tmp_path / VERIFIED_RELEASE_INDEX,
        directory=release,
        checksums=checksums,
        provenance_path=release / NATIVE_RELEASE_PROVENANCE,
        repository=repository,
        tag=f"desktop-v{native_version()}",
        source_commit="a" * 40,
        engine_release_path=_engine_release_file(tmp_path),
    )
    channel = write_verified_release_channel(
        tmp_path / VERIFIED_RELEASE_CHANNEL,
        index_path=index,
        repository=repository,
        workflow_ref=_native_workflow_ref(),
        workflow_commit="a" * 40,
        run_id=123456,
        run_attempt=2,
    )
    monkeypatch.setattr(
        download_verifier.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "verified", ""),
    )
    expected = _read_checksum_lines(checksums)
    real_verify = download_verifier.verify

    def _swap_then_verify(directory: Path, manifest: Path, **kwargs: object) -> int:
        # Replace SHA256SUMS in the window between authentication and use.
        manifest.write_text(f"{'0' * 64}  OpenAdapt-Desktop-attacker.dmg\n", encoding="utf-8")
        return real_verify(directory, manifest, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(download_verifier, "verify", _swap_then_verify)

    assert download_verifier.verify_authenticated_channel(
        channel_path=channel,
        index_path=index,
        directory=release,
        checksums=checksums,
        repository=repository,
    ) == len(expected)


def test_every_workflow_checksum_manifest_uses_the_exact_name() -> None:
    """`verify_checksums` accepts only a manifest named exactly SHA256SUMS.

    The native installer jobs are skipped on pull requests and run on `main`
    pushes, so a manifest named anything else fails after merge rather than in
    review. Check the contract as text instead.
    """

    checked = 0
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            if "--output" not in line and "--manifest" not in line:
                continue
            value = line.removesuffix("\\").strip().split()[-1]
            if "SHA256SUMS" in value:
                assert value.endswith("SHA256SUMS"), f"{workflow.name}: {line.strip()}"
                checked += 1
    assert checked >= 2


def test_release_workflow_attestation_checks_use_one_exact_identity_flag() -> None:
    """Every workflow attestation check must be a command `gh` can run.

    `gh attestation verify` treats --cert-identity, --cert-identity-regex,
    --signer-repo, and --signer-workflow as one mutually exclusive group and
    refuses the command when two are set. A release that cannot run its own
    verification step cannot publish, so this contract is checked as text.
    """

    workflow = (ROOT / ".github" / "workflows" / "native-release.yml").read_text(encoding="utf-8")
    invocations = workflow.count("gh attestation verify")
    assert invocations >= 1
    for excluded in ("--signer-workflow", "--signer-repo", "--cert-identity-regex"):
        assert excluded not in workflow
    assert workflow.count("--cert-identity ") == invocations
    assert (
        workflow.count('--cert-oidc-issuer "https://token.actions.githubusercontent.com"')
        == invocations
    )
    assert workflow.count("--deny-self-hosted-runners") == invocations


def test_public_download_verifier_rejects_index_or_checksum_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release, _manifest, checksums = _stage_complete_release(tmp_path / "assets")
    index = write_verified_release_index(
        tmp_path / VERIFIED_RELEASE_INDEX,
        directory=release,
        checksums=checksums,
        provenance_path=release / NATIVE_RELEASE_PROVENANCE,
        repository="OpenAdaptAI/openadapt-desktop",
        tag=f"desktop-v{native_version()}",
        source_commit="a" * 40,
        engine_release_path=_engine_release_file(tmp_path),
    )
    channel = write_verified_release_channel(
        tmp_path / VERIFIED_RELEASE_CHANNEL,
        index_path=index,
        repository="OpenAdaptAI/openadapt-desktop",
        workflow_ref=_native_workflow_ref(),
        workflow_commit="a" * 40,
        run_id=123456,
        run_attempt=2,
    )
    monkeypatch.setattr(
        download_verifier.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "verified", ""),
    )

    index.write_text(index.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="index digest differs"):
        verify_authenticated_channel(
            channel_path=channel,
            index_path=index,
            directory=release,
            checksums=checksums,
        )

    index.write_text(index.read_text(encoding="utf-8").rstrip() + "\n", encoding="utf-8")
    checksums.write_text(checksums.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA256SUMS digest differs"):
        verify_authenticated_channel(
            channel_path=channel,
            index_path=index,
            directory=release,
            checksums=checksums,
        )


def test_public_download_verifier_rejects_a_channel_changed_during_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release, _manifest, checksums = _stage_complete_release(tmp_path / "assets")
    index = write_verified_release_index(
        tmp_path / VERIFIED_RELEASE_INDEX,
        directory=release,
        checksums=checksums,
        provenance_path=release / NATIVE_RELEASE_PROVENANCE,
        repository="OpenAdaptAI/openadapt-desktop",
        tag=f"desktop-v{native_version()}",
        source_commit="a" * 40,
        engine_release_path=_engine_release_file(tmp_path),
    )
    channel = write_verified_release_channel(
        tmp_path / VERIFIED_RELEASE_CHANNEL,
        index_path=index,
        repository="OpenAdaptAI/openadapt-desktop",
        workflow_ref=_native_workflow_ref(),
        workflow_commit="a" * 40,
        run_id=123456,
        run_attempt=2,
    )

    def _mutate(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        subject = Path(command[3])
        subject.write_text(subject.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "verified", "")

    monkeypatch.setattr(download_verifier.subprocess, "run", _mutate)
    with pytest.raises(ValueError, match="changed during attestation"):
        verify_authenticated_channel(
            channel_path=channel,
            index_path=index,
            directory=release,
            checksums=checksums,
        )


def test_public_download_verifier_checks_the_retained_prior_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release, _manifest, checksums = _stage_complete_release(tmp_path / "assets")
    index = write_verified_release_index(
        tmp_path / VERIFIED_RELEASE_INDEX,
        directory=release,
        checksums=checksums,
        provenance_path=release / NATIVE_RELEASE_PROVENANCE,
        repository="OpenAdaptAI/openadapt-desktop",
        tag=f"desktop-v{native_version()}",
        source_commit="a" * 40,
        engine_release_path=_engine_release_file(tmp_path),
    )
    channel = write_verified_release_channel(
        tmp_path / VERIFIED_RELEASE_CHANNEL,
        index_path=index,
        repository="OpenAdaptAI/openadapt-desktop",
        workflow_ref=_native_workflow_ref(),
        workflow_commit="a" * 40,
        run_id=123456,
        run_attempt=2,
    )
    current = json.loads(channel.read_text(encoding="utf-8"))
    prior = dict(current)
    prior["native_version"] = "0.1.0"
    prior["native_tag"] = "desktop-v0.1.0"
    prior["engine_tag"] = "v0.1.0"
    prior["native_release_url"] = (
        "https://github.com/OpenAdaptAI/openadapt-desktop/releases/tag/desktop-v0.1.0"
    )
    prior["engine_release_url"] = (
        "https://github.com/OpenAdaptAI/openadapt-desktop/releases/tag/v0.1.0"
    )
    prior["promotion"] = dict(prior["promotion"])
    prior["promotion"]["workflow_ref"] = _native_workflow_ref("desktop-v0.1.0")
    for field in ("verified_index", "checksums"):
        prior[field] = dict(prior[field])
        prior[field]["url"] = prior[field]["url"].replace(f"/v{native_version()}/", "/v0.1.0/")
    prior["previous"] = None
    prior_path = tmp_path / "prior" / VERIFIED_RELEASE_CHANNEL
    prior_path.parent.mkdir()
    prior_path.write_text(json.dumps(prior, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    current["previous"] = {
        "native_version": "0.1.0",
        "sha256": hashlib.sha256(prior_path.read_bytes()).hexdigest(),
    }
    channel.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        download_verifier.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "verified", ""),
    )

    assert verify_authenticated_channel(
        channel_path=channel,
        index_path=index,
        directory=release,
        checksums=checksums,
        previous_channel=prior_path,
    )
    prior_path.write_text(prior_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not extend"):
        verify_authenticated_channel(
            channel_path=channel,
            index_path=index,
            directory=release,
            checksums=checksums,
            previous_channel=prior_path,
        )


def test_website_release_manifest_is_an_honest_index_of_staged_bytes(tmp_path: Path) -> None:
    _, output, checksums = _stage_complete_release(tmp_path)
    assert validate_website_release_manifest(output, checksums=checksums) == 6
    manifest = json.loads(output.read_text())
    assert {asset["signing"] for asset in manifest["artifacts"]} == {
        "developer-id-notarized",
        "authenticode",
        "github-attested",
    }
    assert manifest["verification"]["github_artifact_attestation"] == "required"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha256_manifest", "checksums.txt"),
        ("github_artifact_attestation", "optional"),
        ("installer_smoke", "install only"),
        ("unexpected", "accepted"),
    ],
)
def test_website_release_manifest_rejects_modified_verification_contract(
    tmp_path: Path, field: str, value: str
) -> None:
    release, output, checksums = _stage_complete_release(tmp_path)
    manifest = json.loads(output.read_text())
    manifest["verification"][field] = value
    output.write_text(json.dumps(manifest), encoding="utf-8")
    write_checksums(release, checksums)
    with pytest.raises(ValueError, match="invalid verification contract"):
        validate_website_release_manifest(output, checksums=checksums)


def test_website_release_manifest_rejects_modified_sbom_format(tmp_path: Path) -> None:
    release, output, checksums = _stage_complete_release(tmp_path)
    manifest = json.loads(output.read_text())
    manifest["sbom"]["format"] = "SPDX"
    output.write_text(json.dumps(manifest), encoding="utf-8")
    write_checksums(release, checksums)
    with pytest.raises(ValueError, match="invalid SBOM format"):
        validate_website_release_manifest(output, checksums=checksums)


def test_website_release_manifest_rejects_duplicate_and_nonexistent_assets(
    tmp_path: Path,
) -> None:
    _, output, checksums = _stage_complete_release(tmp_path)
    manifest = json.loads(output.read_text())
    manifest["artifacts"][1] = dict(manifest["artifacts"][0])
    output.write_text(json.dumps(manifest), encoding="utf-8")
    write_checksums(output.parent, checksums)
    with pytest.raises(ValueError, match="invalid artifact digest|incomplete"):
        validate_website_release_manifest(output, checksums=checksums)

    _, output, checksums = _stage_complete_release(tmp_path / "nonexistent")
    manifest = json.loads(output.read_text())
    manifest["artifacts"][0]["name"] = "OpenAdapt-Desktop-does-not-exist.dmg"
    output.write_text(json.dumps(manifest), encoding="utf-8")
    write_checksums(output.parent, checksums)
    with pytest.raises(ValueError, match="invalid artifact digest"):
        validate_website_release_manifest(output, checksums=checksums)


def test_website_release_manifest_rejects_hash_metadata_and_byte_tampering(
    tmp_path: Path,
) -> None:
    _, output, checksums = _stage_complete_release(tmp_path)
    manifest = json.loads(output.read_text())
    manifest["artifacts"][0]["sha256"] = "0" * 64
    output.write_text(json.dumps(manifest), encoding="utf-8")
    write_checksums(output.parent, checksums)
    with pytest.raises(ValueError, match="digest differs"):
        validate_website_release_manifest(output, checksums=checksums)

    _, output, checksums = _stage_complete_release(tmp_path / "metadata")
    manifest = json.loads(output.read_text())
    manifest["artifacts"][0]["architecture"] = "mips64"
    output.write_text(json.dumps(manifest), encoding="utf-8")
    write_checksums(output.parent, checksums)
    with pytest.raises(ValueError, match="platform metadata"):
        validate_website_release_manifest(output, checksums=checksums)

    release, output, checksums = _stage_complete_release(tmp_path / "bytes")
    manifest = json.loads(output.read_text())
    (release / manifest["artifacts"][0]["name"]).write_bytes(b"tampered")
    write_checksums(release, checksums)
    with pytest.raises(ValueError, match="digest differs"):
        validate_website_release_manifest(output, checksums=checksums)


def test_website_release_manifest_rejects_sbom_and_checksum_tampering(tmp_path: Path) -> None:
    release, output, checksums = _stage_complete_release(tmp_path)
    manifest = json.loads(output.read_text())
    manifest["sbom"]["sha256"] = "f" * 64
    output.write_text(json.dumps(manifest), encoding="utf-8")
    write_checksums(release, checksums)
    with pytest.raises(ValueError, match="SBOM digest"):
        validate_website_release_manifest(output, checksums=checksums)

    release, output, checksums = _stage_complete_release(tmp_path / "checksum")
    lines = checksums.read_text().splitlines()
    checksums.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exact release file set"):
        validate_website_release_manifest(output, checksums=checksums)

    release, output, checksums = _stage_complete_release(tmp_path / "missing-sbom")
    manifest = json.loads(output.read_text())
    (release / manifest["sbom"]["name"]).unlink()
    with pytest.raises(ValueError, match="missing SBOM"):
        validate_website_release_manifest(output, checksums=checksums)


def test_website_release_manifest_rejects_forged_metadata_checksum(tmp_path: Path) -> None:
    _, output, checksums = _stage_complete_release(tmp_path)
    lines = checksums.read_text(encoding="utf-8").splitlines()
    metadata_index = next(
        index for index, line in enumerate(lines) if line.endswith("-metadata.json")
    )
    _digest, separator, name = lines[metadata_index].partition("  ")
    assert separator and name
    lines[metadata_index] = f"{'0' * 64}  {name}"
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA256SUMS digest differs"):
        validate_website_release_manifest(output, checksums=checksums)


def test_missing_existing_document_is_an_error_not_a_skipped_check(tmp_path: Path) -> None:
    """A passed but absent ``--existing`` must fail, never drop the check.

    The release workflow used to end its download with ``|| true``, so a failed
    download produced no file and the monotonicity comparison disappeared.
    """

    release, _manifest, checksums = _stage_complete_release(tmp_path / "assets")
    absent = tmp_path / "absent" / VERIFIED_RELEASE_INDEX

    with pytest.raises(ValueError, match="missing or is not a regular file"):
        write_verified_release_index(
            tmp_path / VERIFIED_RELEASE_INDEX,
            directory=release,
            checksums=checksums,
            provenance_path=release / NATIVE_RELEASE_PROVENANCE,
            repository="OpenAdaptAI/openadapt-desktop",
            tag=f"desktop-v{native_version()}",
            source_commit="a" * 40,
            engine_release_path=_engine_release_file(tmp_path),
            existing=absent,
        )

    index = write_verified_release_index(
        tmp_path / VERIFIED_RELEASE_INDEX,
        directory=release,
        checksums=checksums,
        provenance_path=release / NATIVE_RELEASE_PROVENANCE,
        repository="OpenAdaptAI/openadapt-desktop",
        tag=f"desktop-v{native_version()}",
        source_commit="a" * 40,
        engine_release_path=_engine_release_file(tmp_path),
    )
    with pytest.raises(ValueError, match="missing or is not a regular file"):
        write_verified_release_channel(
            tmp_path / VERIFIED_RELEASE_CHANNEL,
            index_path=index,
            repository="OpenAdaptAI/openadapt-desktop",
            workflow_ref=_native_workflow_ref(),
            workflow_commit="a" * 40,
            run_id=123456,
            run_attempt=1,
            existing=tmp_path / "absent" / VERIFIED_RELEASE_CHANNEL,
        )


def test_release_workflow_never_swallows_the_existing_index_download() -> None:
    body = (ROOT / ".github/workflows/native-release.yml").read_text()
    step = body.split("Write the monotonic verified release index", 1)[1].split(
        "- name: Attest", 1
    )[0]

    assert "|| true" not in step
    assert 'gh release view "${ENGINE_TAG}" --json assets' in step
    assert "grep -qxF 'openadapt-desktop-verified-release.json'" in step


def test_native_publish_only_resumes_identical_bytes_and_never_clobbers() -> None:
    """A failed attempt can fill missing assets, but cannot replace one byte."""

    body = (ROOT / ".github/workflows/native-release.yml").read_text()
    step = body.split("Create or safely resume the immutable public prerelease", 1)[1].split(
        "\n  verify-published-release:", 1
    )[0]

    assert 'git ls-remote --exit-code --tags origin "refs/tags/${NATIVE_TAG}"' in step
    assert 'gh release view "${NATIVE_TAG}"' in step
    assert 'cmp "release-assets/${name}" "existing-release-assets/${name}"' in step
    assert "existing release has an unexpected asset" in step
    assert "--clobber" not in step
    assert "--draft" not in step
    assert "--prerelease" in step


def test_standalone_verifier_states_the_same_asset_contract() -> None:
    """The shipped verifier duplicates the asset contract; keep them equal."""

    for version in ("0.1.0", "1.11.0", "10.20.30", native_version()):
        assert download_verifier.expected_asset_names(version) == expected_release_asset_names(
            version
        )
    for invalid in ("1.2", "v1.2.3", "1.2.3-rc1"):
        with pytest.raises(ValueError):
            expected_release_asset_names(invalid)
        with pytest.raises(ValueError):
            download_verifier.expected_asset_names(invalid)


def test_download_verification_reads_through_the_checked_path(tmp_path: Path) -> None:
    """Hash the member the inventory check proved regular, not a fresh lookup."""

    directory = tmp_path / "download"
    directory.mkdir()
    (directory / "installer.bin").write_bytes(b"installer")
    write_checksums(directory, directory / "SHA256SUMS")
    assert verify_download_inventory(directory, directory / "SHA256SUMS") == 1

    secret = tmp_path / "outside.bin"
    secret.write_bytes(b"installer")
    (directory / "installer.bin").unlink()
    (directory / "installer.bin").symlink_to(secret)

    # A link never reaches the hash loop. This is the whole defence on every
    # platform, so assert it before the platform-specific open behaviour.
    with pytest.raises(ValueError, match="link or non-regular file"):
        verify_download_inventory(directory, directory / "SHA256SUMS")

    link = directory / "installer.bin"
    if hasattr(os, "O_NOFOLLOW"):
        # POSIX refuses the open itself, so the hash can never follow a link
        # that appears between the check and the read.
        with pytest.raises(OSError):
            download_verifier._read_bytes(link)
    else:
        # Windows has no O_NOFOLLOW and no equivalent. The open succeeds there
        # by design, and the explicit is_symlink check above is the defence.
        assert download_verifier._read_bytes(link) == b"installer"


def test_git_helpers_refuse_an_option_shaped_ref(tmp_path: Path) -> None:
    """A ref that begins with ``-`` must never reach Git as an option."""

    stolen = tmp_path / "stolen.txt"
    for injected in (f"--output={stolen}", "-h", ""):
        with pytest.raises(ValueError, match="invalid Git ref|does not name exactly one commit"):
            validate_git_version_transform(injected, "HEAD", native_version())
        with pytest.raises(ValueError, match="invalid Git ref|does not name exactly one commit"):
            native_version_at_ref(injected)
    assert not stolen.exists()

    with pytest.raises(ValueError, match="does not name exactly one commit"):
        native_version_at_ref("refs/heads/no-such-branch-for-this-test")


def test_native_version_at_ref_reads_git_objects_not_the_working_tree() -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert native_version_at_ref(head) == native_version()
    assert native_version_at_ref("HEAD") == native_version()


def _production_admission_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, dict]:
    version = native_version()
    release, _manifest, checksums = _stage_complete_release(tmp_path / "native")
    engine_release = _engine_release_file(tmp_path)
    index = write_verified_release_index(
        tmp_path / VERIFIED_RELEASE_INDEX,
        directory=release,
        checksums=checksums,
        provenance_path=release / NATIVE_RELEASE_PROVENANCE,
        repository=DESKTOP_REPOSITORY,
        tag=f"desktop-v{version}",
        source_commit="a" * 40,
        engine_release_path=engine_release,
    )
    engine_directory = tmp_path / "engine-files"
    engine_directory.mkdir()
    (engine_directory / f"openadapt_desktop-{version}-py3-none-any.whl").write_bytes(b"wheel")
    (engine_directory / f"openadapt_desktop-{version}.tar.gz").write_bytes(b"sdist")
    engine_provenance = write_engine_release_provenance(
        tmp_path / ENGINE_RELEASE_PROVENANCE,
        directory=engine_directory,
        release_path=engine_release,
        repository=DESKTOP_REPOSITORY,
        engine_tag=f"v{version}",
        engine_commit="b" * 40,
        workflow_ref=(
            f"{DESKTOP_REPOSITORY}/.github/workflows/release.yml@refs/tags/v{version}"
        ),
        workflow_commit="b" * 40,
        run_id=100,
        run_attempt=1,
        runner_environment="github-hosted",
    )
    receipt = json.loads(engine_provenance.read_text(encoding="utf-8"))
    index_value = json.loads(index.read_text(encoding="utf-8"))
    artifacts: list[dict] = []
    for number, asset in enumerate(receipt["assets"], start=1):
        kind = "wheel" if asset["name"].endswith(".whl") else "sdist"
        artifacts.append(
            {
                "name": asset["name"],
                "kind": kind,
                "authority": "pypi",
                "url": f"https://files.pythonhosted.org/packages/{number}/{asset['name']}",
                "sha256": "sha256:" + asset["sha256"],
                "size_bytes": 10 + number,
            }
        )
    next_asset_id = 1000
    suffix_to_kind = {
        ".dmg": "macos-installer",
        ".msi": "windows-installer",
        "-nsis-setup.exe": "windows-installer",
        ".deb": "linux-installer",
        ".AppImage": "linux-installer",
    }
    for asset in index_value["assets"]:
        kind = next(
            (
                artifact_kind
                for suffix, artifact_kind in suffix_to_kind.items()
                if asset["name"].endswith(suffix)
            ),
            None,
        )
        if kind is None:
            continue
        artifacts.append(
            {
                "name": asset["name"],
                "kind": kind,
                "authority": "github_release",
                "url": (
                    "https://api.github.com/repos/OpenAdaptAI/openadapt-desktop/"
                    f"releases/assets/{next_asset_id}"
                ),
                "sha256": "sha256:" + asset["sha256"],
                "size_bytes": next_asset_id,
            }
        )
        next_asset_id += 1
    artifacts.sort(key=lambda item: (item["kind"], item["name"]))
    admission = {
        "admission_id": "production:desktop:1",
        "target": "desktop",
        "claim_scope": "qualified_native_workflow_desktop_release",
        "release_identity": {
            "schema_version": "openadapt.monotonic-production-release/v1",
            "channel": "production",
            "sequence": 1,
            "previous_admission_sha256": None,
        },
        "policy_revision": 1,
        "release": {
            "kind": "public_package",
            "version": version,
            "tag": f"v{version}",
            "source_commit": "a" * 40,
            "immutable_release_url": (
                "https://github.com/OpenAdaptAI/openadapt-desktop/commit/" + "a" * 40
            ),
            "artifacts": artifacts,
        },
        "acceptance_evidence": {
            "summary_url": "https://example.test/summary.json",
            "summary_sha256": "sha256:" + "1" * 64,
            "attestation_bundle_url": "https://example.test/bundle.json",
            "attestation_bundle_sha256": "sha256:" + "2" * 64,
            "authority_source_commit": "c" * 40,
        },
        "issued_at": "2026-08-20T12:00:00Z",
        "expires_at": "2026-09-19T12:00:00Z",
        "revoked_at": None,
    }
    lifecycle = tmp_path / "lifecycle"
    (lifecycle / "scripts").mkdir(parents=True)
    (lifecycle / "production-lifecycle-policy.json").write_text("{}\n", encoding="utf-8")
    (lifecycle / "production-lifecycle-admissions.json").write_text(
        json.dumps({"admissions": [admission]}) + "\n", encoding="utf-8"
    )
    (lifecycle / "repository-lifecycle.yml").write_text(
        "lifecycle:\n  production: []\n", encoding="utf-8"
    )
    (lifecycle / "scripts" / "validate_production_lifecycle.py").write_text(
        "# canonical validator fixture\n", encoding="utf-8"
    )
    state_value = build_admission_state(
        lifecycle,
        central_source_commit="c" * 40,
        validate_files=lambda _root: {"desktop": admission["admission_id"]},
    )
    state = write_admission_state(tmp_path / "admission-state.json", state_value)
    return state, index, engine_provenance, engine_release, engine_directory, admission


def test_canonical_state_keeps_no_admission_as_no_production(tmp_path: Path) -> None:
    lifecycle = tmp_path / "lifecycle"
    (lifecycle / "scripts").mkdir(parents=True)
    (lifecycle / "production-lifecycle-policy.json").write_text("{}\n", encoding="utf-8")
    (lifecycle / "production-lifecycle-admissions.json").write_text(
        '{"admissions": []}\n', encoding="utf-8"
    )
    (lifecycle / "repository-lifecycle.yml").write_text("lifecycle: {}\n", encoding="utf-8")
    (lifecycle / "scripts" / "validate_production_lifecycle.py").write_text(
        "# canonical validator fixture\n", encoding="utf-8"
    )
    state_value = build_admission_state(
        lifecycle,
        central_source_commit="c" * 40,
        validate_files=lambda _root: {},
    )
    state = write_admission_state(tmp_path / "state.json", state_value)
    assert validate_admission_state(state)["active_admission"] is None
    assert state_value["active_admission_sha256"] is None


def test_production_channel_is_an_exact_derived_cache(tmp_path: Path) -> None:
    state, index, receipt, engine_release, engine_directory, admission = (
        _production_admission_fixture(tmp_path)
    )
    channel = write_production_channel(
        tmp_path / "cache",
        state_path=state,
        index_path=index,
        engine_provenance_path=receipt,
        engine_release_path=engine_release,
        engine_directory=engine_directory,
        repository=DESKTOP_REPOSITORY,
        workflow_ref=f"{DESKTOP_REPOSITORY}/{PROMOTION_WORKFLOW}@refs/heads/main",
        workflow_commit="d" * 40,
        run_id=200,
        run_attempt=1,
    )
    assert channel.name == production_channel_asset_name(admission, "c" * 40)
    assert channel.name.startswith(CHANNEL_PREFIX)
    validated = verify_production_channel(
        channel,
        state_path=state,
        index_path=index,
        engine_provenance_path=receipt,
        engine_release_path=engine_release,
        engine_directory=engine_directory,
    )
    assert validated["cache_role"] == "derived-only"
    assert validated["admission"]["admission_id"] == admission["admission_id"]
    assert validated["release"]["artifacts"] == admission["release"]["artifacts"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "99.0.0"),
        ("tag", "v99.0.0"),
        ("source_commit", "f" * 40),
    ],
)
def test_production_channel_refuses_an_unadmitted_candidate_identity(
    tmp_path: Path, field: str, value: str
) -> None:
    state, index, receipt, engine_release, engine_directory, _admission = (
        _production_admission_fixture(tmp_path)
    )
    state_value = json.loads(state.read_text(encoding="utf-8"))
    state_value["active_admission"]["release"][field] = value
    canonical = json.dumps(
        state_value["active_admission"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    state_value["active_admission_sha256"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    changed = tmp_path / "changed-state.json"
    changed.write_text(json.dumps(state_value), encoding="utf-8")
    with pytest.raises(ValueError, match=f"{field} differs"):
        write_production_channel(
            tmp_path / "cache",
            state_path=changed,
            index_path=index,
            engine_provenance_path=receipt,
            engine_release_path=engine_release,
            engine_directory=engine_directory,
            repository=DESKTOP_REPOSITORY,
            workflow_ref=f"{DESKTOP_REPOSITORY}/{PROMOTION_WORKFLOW}@refs/heads/main",
            workflow_commit="d" * 40,
            run_id=200,
            run_attempt=1,
        )


def test_production_channel_refuses_an_unadmitted_artifact(tmp_path: Path) -> None:
    state, index, receipt, engine_release, engine_directory, _admission = (
        _production_admission_fixture(tmp_path)
    )
    state_value = json.loads(state.read_text(encoding="utf-8"))
    state_value["active_admission"]["release"]["artifacts"][0]["sha256"] = (
        "sha256:" + "f" * 64
    )
    canonical = json.dumps(
        state_value["active_admission"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    state_value["active_admission_sha256"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    changed = tmp_path / "changed-artifact-state.json"
    changed.write_text(json.dumps(state_value), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact inventory differs"):
        write_production_channel(
            tmp_path / "cache",
            state_path=changed,
            index_path=index,
            engine_provenance_path=receipt,
            engine_release_path=engine_release,
            engine_directory=engine_directory,
            repository=DESKTOP_REPOSITORY,
            workflow_ref=f"{DESKTOP_REPOSITORY}/{PROMOTION_WORKFLOW}@refs/heads/main",
            workflow_commit="d" * 40,
            run_id=200,
            run_attempt=1,
        )


def test_production_channel_refuses_current_default_drift(tmp_path: Path) -> None:
    state, index, receipt, engine_release, engine_directory, _admission = (
        _production_admission_fixture(tmp_path)
    )
    channel = write_production_channel(
        tmp_path / "cache",
        state_path=state,
        index_path=index,
        engine_provenance_path=receipt,
        engine_release_path=engine_release,
        engine_directory=engine_directory,
        repository=DESKTOP_REPOSITORY,
        workflow_ref=f"{DESKTOP_REPOSITORY}/{PROMOTION_WORKFLOW}@refs/heads/main",
        workflow_commit="d" * 40,
        run_id=200,
        run_attempt=1,
    )
    state_value = json.loads(state.read_text(encoding="utf-8"))
    state_value["canonical_source_commit"] = "e" * 40
    drifted = tmp_path / "drifted-state.json"
    drifted.write_text(json.dumps(state_value), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical main"):
        verify_production_channel(
            channel,
            state_path=drifted,
            index_path=index,
            engine_provenance_path=receipt,
            engine_release_path=engine_release,
            engine_directory=engine_directory,
        )


def test_production_workflow_keeps_normal_publication_unadmitted() -> None:
    production = _workflow("production-channel.yml")
    trigger = production[True]
    assert set(trigger) == {"workflow_dispatch", "release", "schedule"}
    assert production["permissions"] == {"contents": "read"}
    assert production["concurrency"] == {
        "group": "production-channel",
        "cancel-in-progress": False,
    }
    inspect = production["jobs"]["inspect"]
    promote = production["jobs"]["promote"]
    assert "if" not in inspect
    assert inspect["permissions"] == {"attestations": "read", "contents": "read"}
    assert promote["environment"] == "production-release"
    assert promote["permissions"] == {
        "attestations": "write",
        "contents": "read",
        "id-token": "write",
    }
    promote_steps = _job_steps(promote)
    release_app = promote_steps["Mint the release App token for GitHub publication"]
    assert release_app["uses"] == (
        "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
    )
    assert release_app["with"] == {
        "app-id": "${{ vars.OPENADAPT_RELEASE_APP_ID }}",
        "private-key": "${{ secrets.OPENADAPT_RELEASE_APP_PRIVATE_KEY }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-contents": "write",
        "permission-metadata": "read",
    }
    publication = promote_steps["Publish once and verify the derived cache"]
    assert publication["env"]["GH_TOKEN"] == "${{ steps.release_app.outputs.token }}"
    publication_script = publication["run"]
    assert 'gh release upload "${PRODUCTION_CHANNEL_TAG}" "${CACHE_PATH}"' in publication_script
    assert 'gh release create "${PRODUCTION_CHANNEL_TAG}"' in publication_script
    assert "github.token" not in publication_script
    production_text = (ROOT / ".github/workflows/production-channel.yml").read_text()
    assert "repository: OpenAdaptAI/.github" in production_text
    assert "ref: ${{ steps.refs.outputs.central_commit }}" in production_text
    assert "scripts/production_release.py state" in production_text
    assert "--clobber" not in production_text
    assert "active central Production admission" in production_text
    assert '"${GITHUB_REF}" != refs/heads/main' in production_text
    assert "${observed}-${digest#sha256:}" in production_text
    assert production_text.count("status --porcelain --untracked-files=all") == 2
    assert "native-release.yml@refs/tags/desktop-v${VERSION}" in production_text
    assert "release.yml@refs/tags/v${VERSION}" in production_text
    assert "native-release.yml@refs/heads/main" not in production_text
    assert "release.yml@refs/heads/main" not in production_text

    normal_release_text = (ROOT / ".github/workflows/release.yml").read_text()
    native_release_text = (ROOT / ".github/workflows/native-release.yml").read_text()
    for text in (normal_release_text, native_release_text):
        assert "desktop-production-channel" not in text
        assert "write-channel" not in text
    assert "unadmitted release candidate" in normal_release_text
    assert "unadmitted release candidate" in native_release_text
