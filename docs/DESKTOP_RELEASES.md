# Desktop package releases

OpenAdapt Desktop publishes one package release for each `vX.Y.Z` version. The
GitHub Release contains the Python distributions and every native installer for
that version. The wheel and source distribution publish to PyPI from the same
verified bytes.

An exact Desktop release enters Production only while its signed central
release admission is active. The admission can expire or be revoked. GitHub's
"Latest" pointer does not grant Production status.

## Public assets

Each release has exactly 14 public assets:

| Kind | Files |
| --- | --- |
| Linux | x86_64 AppImage and DEB |
| macOS | arm64 DMG and x86_64 DMG |
| Windows | x86_64 MSI and NSIS setup executable |
| Python | wheel and source distribution |
| Platform verification | Linux x86_64, macOS arm64, macOS x86_64, and Windows x86_64 JSON |
| Release evidence | CycloneDX JSON SBOM and `SHA256SUMS` |

Release filenames contain the version, platform, and architecture. They do not
contain `Beta`, `Candidate`, `adhoc`, or `unsigned`.

The four platform-verification files use the closed
`openadapt.desktop-platform-verification/v1` schema. They bind the exact source
commit, workflow run, embedded Flow version, artifact names, byte counts, and
SHA-256 digests. They also record these platform checks:

- macOS: Developer ID signature, hardened runtime, notarization, stapled ticket,
  and Gatekeeper acceptance.
- Windows: valid timestamped Authenticode for the MSI and NSIS files.
- Linux: GitHub OIDC provenance for the exact DEB and AppImage bytes.

The metadata includes hashes of public signer facts. It does not contain a
private key, certificate archive, password, token, Apple account, or Azure
service-principal value.

## Release sequence

The `Production package release` workflow has two manual operations.

The `stage` operation runs from current, reviewed `main`. It requires successful
source checks, builds every package once, runs the install, launch, embedded
Flow, and uninstall checks, and creates the exact artifact inventory. The
Release App then creates one draft for the prospective `vX.Y.Z` tag while that
tag is absent. It uploads all 14 assets and downloads them again to check every
byte. The draft ID, assets, immutable-release setting, tag rulesets, and second
tag-absence observation become staging evidence for the central admission.

The `publish` operation accepts the exact central admission reference. It
downloads the existing draft assets and builds nothing. The pinned central
verifier checks the signed, expiring, revocable admission against the release
source, version, tag, draft, and artifact inventory. The Release App then
creates the annotated `vX.Y.Z` tag with the exact admission binding. A second
fresh check verifies the immutable tag and unchanged draft before PyPI receives
the admitted wheel and source distribution. A third fresh check runs before the
Release App makes the same draft public. The workflow then checks the immutable
public Release and all 14 remote assets.

A rerun can continue only from the same draft ID and the same bytes. It cannot
replace a published asset, move a release tag, rebuild a candidate, or create a
second Release for the admitted version.

## GitHub authority

The release workflow separates the principals:

- Manual dispatcher: GitHub actor ID `774615`, on protected `main`.
- Release mutation App: integration ID `4730708`, installation ID `156835568`.
- Release and asset author: bot user ID `321543906`.

The App needs `Administration: read`, `Contents: write`, and `Metadata: read`
for this repository. Desktop package tags use two active rulesets for
`refs/tags/v*`:

- `OpenAdapt policy: release tag creation` allows the Release App to create the
  tag once.
- `OpenAdapt policy: immutable release tags` blocks update, deletion, and
  non-fast-forward changes for every actor.

Immutable GitHub Releases must be enabled. The workflow validates the exact
GitHub API response, including the `enforced_by_owner` value, before it stages
or publishes a release.

## Local verification

Download all 14 files from one immutable `vX.Y.Z` Release into an empty
directory. Check the manifest first:

```bash
sha256sum -c SHA256SUMS
```

Inspect the verification file for your platform. On macOS, also run Gatekeeper
and stapler checks. On Windows, inspect `Get-AuthenticodeSignature`. Linux users
can verify GitHub provenance against
`.github/workflows/release.yml@refs/heads/main`.

## Managed FFmpeg Support release

The managed FFmpeg runtime is a separate Support artifact in this repository.
It is outside the Desktop Production admission and has its own exact source,
build configuration, notices, inventory, provenance, release evidence, and tag
policy. Its tags use `ffmpeg-runtime-v*` and the two FFmpeg-specific rulesets in
[CODE_SIGNING.md](CODE_SIGNING.md). The Desktop package release uses only
`vX.Y.Z`.
