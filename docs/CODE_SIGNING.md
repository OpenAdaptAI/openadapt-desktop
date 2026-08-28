# Code Signing Runbook (founder activation)

This guide adds the required platform trust to an OpenAdapt Desktop package
release. It lists what to buy, which secrets to add, and what each public
surface can then truthfully say. Code signing does not create a Production
admission.

The regular build workflow can still produce explicit ad-hoc or unsigned CI
artifacts. The Production package workflow is stricter. It fails closed unless
macOS has the complete Developer ID and notarization set and Windows has one
complete Authenticode method. It also fails closed on a partial set.

## How signing is wired (read once)

- `scripts/native_signing.py preflight --platform <macos|windows|linux>` inspects
  the `native-release` environment secrets and emits two values to the workflow:
  - `mode`: the verified signing state (`adhoc` or `unsigned` for local and CI
    builds, or `developer-id-notarized` or `authenticode` for a package release).
  - `method`: how a signed Windows artifact is produced (`pfx` or
    `trusted-signing`); `pfx`, `adhoc`, or `unsigned` otherwise.
- Production filenames are stable and contain the version, platform, and
  architecture. They do not use a signing-state token. Each exact file is bound
  to one of the four public platform-verification JSON records. That record is
  the source for the signing, notarization, and provenance state.
- The launch-smoke test (`scripts/smoke_test_native_installer.py`) already
  installs, launches, and **verifies the signature** for the active mode:
  `codesign`/`spctl`/`stapler` on macOS, `Get-AuthenticodeSignature` on Windows.
  The package release refuses ad-hoc or unsigned results. The regular build lane
  can still test explicitly labeled ad-hoc and unsigned packages.
- The macOS engine is a PyInstaller one-file sidecar. Developer ID jobs pass
  `APPLE_SIGNING_IDENTITY` into both PyInstaller and Tauri so the embedded
  Python libraries and final launcher share one Team ID under hardened runtime.
  Identity-less CI builds use `tauri.adhoc.conf.json` without hardened runtime.
  The installed-app smoke executes bundled Flow after Tauri's final
  signing pass; a bundle that is signed but cannot load its engine fails.

All secrets below live in the protected **`native-release`** GitHub Actions
environment (Settings → Environments → `native-release` → *Environment secrets*),
or via the CLI:

```bash
gh secret set APPLE_TEAM_ID --env native-release --repo OpenAdaptAI/openadapt-desktop
```

---

## 1. macOS: Developer ID and notarization ($99/yr)

**Buy:** [Apple Developer Program](https://developer.apple.com/programs/).
**US$99 / year** (individual or organization). An organization membership needs
a D-U-N-S number and takes a few days to verify; the individual tier activates
immediately.

**Produce the certificate:**
1. In the Apple Developer portal, create a **Developer ID Application**
   certificate. Do not use "Apple Distribution", which is for the App Store.
2. Download it, open in Keychain Access, and **export** the certificate *with its
   private key* as a `.p12`, setting an export password.
3. Base64-encode it for the secret:
   `base64 -i DeveloperID.p12 | pbcopy` (macOS). The encoded string is
   `APPLE_CERTIFICATE`.
4. Create an **app-specific password** at <https://account.apple.com> →
   Sign-In and Security → App-Specific Passwords. This is `APPLE_PASSWORD`
   (used only for notarization, not your Apple ID login password).
5. Read the **Team ID** from Membership details (10 characters) → `APPLE_TEAM_ID`.

**Add these six secrets** to the `native-release` environment:

| Secret | Value |
| --- | --- |
| `APPLE_CERTIFICATE` | base64 of the Developer ID Application `.p12` |
| `APPLE_CERTIFICATE_PASSWORD` | the `.p12` export password |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Your Org (TEAMID)` |
| `APPLE_ID` | your Apple Developer account email |
| `APPLE_PASSWORD` | the app-specific password from step 4 |
| `APPLE_TEAM_ID` | the 10-character Team ID |

During the next `stage` operation, the macOS jobs import the cert into an
ephemeral keychain, build a **Developer ID signed** DMG, submit it to Apple's
notary service, staple the ticket, and require `spctl` acceptance and a stapled
ticket. The arm64 and x86_64 DMGs use the stable names in
[DESKTOP_RELEASES.md](DESKTOP_RELEASES.md).

---

## 2. Windows: Authenticode

Since June 2023 the CA/Browser Forum requires the private key of every publicly
trusted OV/EV code-signing certificate to live on FIPS-140 hardware, so a
classic "download a `.pfx` and sign in CI" flow is no longer available for
public trust. There are two supported paths; **option A is recommended for a
startup** because it is the cheapest legitimate option and needs no hardware
token or CI HSM plumbing.

### Option A: Azure Trusted Signing (recommended, ~US$9.99/mo)

**Buy:** an Azure subscription + a **Trusted Signing** (a.k.a. *Azure Artifact
Signing*, formerly *Azure Code Signing*) account. **Basic plan ≈ US$9.99 /
month** for up to 5,000 signatures (then $0.005 each). Microsoft operates the
publicly trusted CA and mints a fresh, short-lived, timestamped certificate per
signature. This gives SmartScreen reputation without a USB token.

**Eligibility note (important for a young startup):** organization onboarding
historically required the legal entity to be **3+ years old**. If the company is
younger, sign up under the **individual developer** tier (identity-validated via
Microsoft Entra Verified ID) until the org-onboarding path is available to newer
entities. Confirm current eligibility on the
[Trusted Signing docs](https://learn.microsoft.com/azure/trusted-signing/)
before purchasing.

**Set up:**
1. Create a Trusted Signing account and a **certificate profile** (choose
   *Public Trust*). Note the account **endpoint** region URI (e.g.
   `https://eus.codesigning.azure.net/`), the **account name**, and the
   **certificate profile name**.
2. Create an Entra **service principal** (app registration + client secret) and
   grant it the **Trusted Signing Certificate Profile Signer** role on the
   account. Record its tenant ID, client ID, and client secret.

**Add these six secrets** to `native-release`:

| Secret | Value |
| --- | --- |
| `AZURE_TENANT_ID` | service principal tenant ID |
| `AZURE_CLIENT_ID` | service principal application (client) ID |
| `AZURE_CLIENT_SECRET` | service principal client secret |
| `TRUSTED_SIGNING_ENDPOINT` | account region URI, e.g. `https://eus.codesigning.azure.net/` |
| `TRUSTED_SIGNING_ACCOUNT` | Trusted Signing account name |
| `TRUSTED_SIGNING_CERTIFICATE_PROFILE` | certificate profile name |

The workflow then installs `trusted-signing-cli`, points Tauri's Windows
`signCommand` at it, and produces a publicly trusted, timestamped Authenticode
MSI and NSIS installer. The smoke test requires the signature status to be
**Valid**. It does not pin a thumbprint because Trusted Signing rotates
certificates per signature.

### Option B: importable PFX or EV certificate (only if you already have one)

Use this **only** for an enterprise-internal certificate whose `.pfx` you
control, or a legacy exportable certificate. For public trust, EV certificates
run **~US$249 to US$700 / year** (Sectigo EV ≈ $279/yr via resellers; DigiCert EV
≈ $560 to $700/yr) and require FIPS hardware. The key can use a USB token, which
breaks unattended CI, or a cloud HSM such as **DigiCert KeyLocker**. KeyLocker
adds cost, and its key is *not* exportable to a `.pfx`. It is **not** compatible
with the `pfx` path below and needs a separate KeyLocker `signtool` integration.
Given the price and the hardware constraint, prefer Option A.

If you do have an importable `.pfx`:

| Secret | Value |
| --- | --- |
| `WINDOWS_CERTIFICATE` | base64 of the code-signing `.pfx` |
| `WINDOWS_CERTIFICATE_PASSWORD` | the `.pfx` password |
| `WINDOWS_CERTIFICATE_THUMBPRINT` | 40-hex SHA-1 thumbprint of the signing cert |

> Configure **either** the Azure set **or** the PFX set, never both. The
> preflight rejects an ambiguous mix.

---

## 3. Linux: exact-byte GitHub attestation

DEB and AppImage do not share one native trust format. A detached GPG signature
would also require a separate authenticated public-key channel. The package
release therefore uses GitHub's OIDC artifact attestation as the Linux trust
boundary. The release job attests the exact DEB and AppImage bytes. It then
runs `gh attestation verify` and records the verified result in the Linux
platform-verification JSON file.

This state is named `github-attested`. It is not called native-signed. It needs
no founder-managed signing secret. GitHub issues the short-lived OIDC identity
for the pinned release workflow. The verifier also binds the reviewed main
commit, run ID, run attempt, and GitHub-hosted runner. `SHA256SUMS` binds all 13
other public release files for offline hash checks.

This workflow control is not sufficient without repository controls. Configure
a no-bypass pull-request ruleset for `main`. Desktop package releases use only
`refs/tags/v*`. The `OpenAdapt policy: release tag creation` ruleset permits
only Release App integration `4730708` to create a tag. The
`OpenAdapt policy: immutable release tags` ruleset prevents every actor from
updating or deleting it.

Managed FFmpeg uses a separate Support release contract. Its tag family is
`refs/tags/ffmpeg-runtime-v*`. The corresponding rulesets are
`OpenAdapt policy: FFmpeg runtime tag creation` and
`OpenAdapt policy: immutable FFmpeg runtime tags`. That authority does not
grant permission to create a Desktop `v*` tag.

---

## What each surface can truthfully claim

Only claim a state after the corresponding secret set is live and a trusted
release has built. The exact platform-verification JSON file is the source for
the state.

| Surface | With no secrets | After macOS Developer ID | After Windows Authenticode | Linux OIDC attestation |
| --- | --- | --- | --- | --- |
| /download page | No new package release; the release gate stops. | "**Signed and notarized by Apple** on macOS. It opens without a Gatekeeper override." | "**Signed with a trusted Authenticode certificate** on Windows." | "Linux DEB and AppImage downloads have **GitHub OIDC attestations over the exact bytes**." |
| Trust center | Existing historical candidate artifacts keep their recorded signing state. | Add: "macOS DMGs pass Apple notarization (`spctl` accepted, ticket stapled)." | Add: "Windows installers carry a valid, timestamped Authenticode signature." | Add: "Linux packages pass `gh attestation verify` against this repository." |
| README honesty note | Describe only the latest published artifact set. | Update the note after the first trusted release. | Update the note after the first trusted release. | Update the note after the first trusted release. |

The README signing note and [DESKTOP_RELEASES.md](DESKTOP_RELEASES.md) both point
here. Update their per-platform wording when the first trusted release ships,
not when the secrets are only present. A public surface must read the admitted
platform-verification record. It must not infer trust from a filename.

## Verify a signed release locally

```bash
# First authenticate the Linux artifacts and their reviewed-main workflow.
gh attestation verify OpenAdapt-Desktop-vX.Y.Z-linux-x86_64.AppImage \
  OpenAdapt-Desktop-vX.Y.Z-linux-x86_64.deb \
  --repo OpenAdaptAI/openadapt-desktop \
  --cert-identity "https://github.com/OpenAdaptAI/openadapt-desktop/.github/workflows/release.yml@refs/heads/main" \
  --cert-oidc-issuer "https://token.actions.githubusercontent.com" \
  --deny-self-hosted-runners

# Then check every published digest.
sha256sum -c SHA256SUMS

# macOS: notarization accepted + ticket stapled
spctl --assess --type open --context context:primary-signature -v <asset>.dmg
xcrun stapler validate <asset>.dmg

# Windows (PowerShell): valid, timestamped, publicly trusted Authenticode chain
Get-AuthenticodeSignature <asset>.msi |
  Format-List Status, SignerCertificate, TimeStamperCertificate
```
