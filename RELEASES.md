# Release policy

OpenAdapt Desktop publishes one version as one package release. The canonical
contract is [Desktop package releases](docs/DESKTOP_RELEASES.md).

## One release identity

The release tag is `vX.Y.Z`. The GitHub Release and the PyPI distributions use
the same version, source commit, and admitted artifact bytes. The GitHub Release
contains exactly 14 public assets:

- six native installers;
- one wheel and one source distribution;
- four platform-verification JSON files;
- one CycloneDX JSON SBOM;
- `SHA256SUMS`.

The package uses no `desktop-v*` release lane, installer mirror, moving channel,
or release-note pointer. Historical tags and releases remain historical records.
They do not select the current release.

## Stage, admit, publish

The `Production package release` workflow has two manual operations.

The `stage` operation runs from current reviewed `main`. It builds each file
once, runs the platform checks, creates one App-authored draft, uploads the 14
files, and verifies the remote bytes. The prospective tag does not exist during
this operation. A rerun can continue only with the same draft ID and the same
bytes.

The central trust workflow issues a signed, expiring, and revocable admission
for that exact release and draft. The `publish` operation does not rebuild. It
downloads the draft files, verifies the active admission at each effect
boundary, creates the annotated tag, publishes the admitted wheel and source
distribution to PyPI, and then makes the same GitHub draft public. The public
Release becomes immutable.

An exact release enters Production only while its release admission is active.
GitHub's Latest pointer and a PyPI version do not create Production state.

## Authority

The release workflow separates the dispatcher, mutation App, and public author.
It uses only the `v*` tag family and the two Desktop rulesets named in
[Desktop package releases](docs/DESKTOP_RELEASES.md). Immutable GitHub Releases
must be enabled.

Managed FFmpeg uses a separate Support release and the `ffmpeg-runtime-v*` tag
family. It has its own inventory, source, build, notice, provenance, draft, and
ruleset contract. It is not part of the 14-asset Desktop admission.

## Verification

Check all files in an empty directory:

```bash
sha256sum -c SHA256SUMS
```

Then read the platform-verification JSON file for the selected installer. See
[Code signing](docs/CODE_SIGNING.md) for the native checks and signer setup.
