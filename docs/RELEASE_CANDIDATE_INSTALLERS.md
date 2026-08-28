# Desktop installers

OpenAdapt Desktop ships its native installers in the same admitted `vX.Y.Z`
package release as the Python wheel and source distribution.

The current contract is [Desktop package releases](DESKTOP_RELEASES.md). It
defines the 14 public files, stable names, platform-verification metadata,
draft-first assembly, central admission, immutable publication, and local
verification steps.

The former `desktop-v*` candidate lane, mirror, channel file, and filename-based
signing state are not current release mechanisms. Historical releases remain on
GitHub as records. Consumers must not use them to select the current Desktop
package.

For Apple Developer ID, notarization, Windows Authenticode, and Linux OIDC
provenance, see [Code signing](CODE_SIGNING.md).
