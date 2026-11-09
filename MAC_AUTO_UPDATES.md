# Signed Mac automatic updates

The packaged Mac app now uses the same Update Now / Later controls as Windows.
Startup checks run in the background. Each Mac reads only the manifest for its
native Python architecture, including Intel Python running under Rosetta.

Existing Mac builds with manual updates must be replaced with this updater-enabled
build once. After that, publish a higher semantic version for the app to offer it.
The app must be in a writable installation folder, outside App Translocation;
use Applications or the user's Applications folder. The installer does not request
administrator privileges or remove quarantine attributes.

## Release assets

Publish tag `vVERSION` and attach the matching signed artifacts together:

- `GRAILD.exe` and `graild-update.json` for Windows.
- `GRAILD-VERSION-macOS-arm64.zip` and `graild-update-macos-arm64.json`.
- `GRAILD-VERSION-macOS-x86_64.zip` and `graild-update-macos-x86_64.json`.

Both Mac manifests use the existing pinned publisher key. Sign locally after
native compilation, Developer ID signing/notarization if available, and final ZIP
creation. The signing private key is never sent to GitHub Actions.

```powershell
$releaseKeyPath = Join-Path $env:LOCALAPPDATA 'GRAILD-Publisher\keys\graild-beta-2026.dpapi'
& "$env:LOCALAPPDATA\Python\pythoncore-3.13-64\python.exe" release_tools.py manifest --private-key $releaseKeyPath --key-id graild-beta-2026 --repository DavidDEdwards/GRAILD-Releases --channel beta --platform macos --architecture arm64 --version VERSION --asset dist/releases/vVERSION/GRAILD-VERSION-macOS-arm64.zip --notes release-notes/vVERSION.md --output dist/releases/vVERSION/graild-update-macos-arm64.json
```

Repeat with `x86_64` for Intel. Never reuse manifests for changed ZIP bytes.

## Installation and recovery

The app verifies the Ed25519 signature, release/version/architecture, ZIP length,
archive SHA-256 and signed bundle fingerprint before accepting a download. Paths,
symbolic links and decompression size are checked before extraction. A separate
copy of the current signed app runs the network-free helper outside the target.
The helper checks app metadata, code signatures, architecture and bundle contents,
then waits for all target app processes to exit before swapping app directories.

The old app is kept beside the installation as `.graild-backup-JOB.app`.
The new app must acknowledge its signed version and health nonce after its native
window loads. Missing startup confirmation restores the verified old app. User
profiles, sign-in data, music and caches are not part of the replaced bundle.

Jobs and recovery records are under `~/Library/Caches/GRAILD/updates/jobs/JOB`.
If the helper is interrupted during the swap, close GRAIL'D and run its preserved
helper from Terminal:

```bash
"$HOME/Library/Caches/GRAILD/updates/jobs/JOB/runner.app/Contents/MacOS/GRAILD" --mac-update-helper --recover --job "$HOME/Library/Caches/GRAILD/updates/jobs/JOB"
```

Recovery refuses modified/unrecognized installations and backups. It never
replaces an already healthy update or silently overwrites an independent install.
Ad-hoc signing is supported by publisher signatures, but does not replace Apple
Developer ID distribution signing, Gatekeeper approval, or notarization.

## Validation scope

The native test jobs exercise real `ditto` extraction/copying, bundle directory
swaps, file modes, rollback, interrupted recovery and process identity on both
architectures. Startup success/failure is injected in transaction tests. The native
builder separately verifies the actual packaged app's code signature, architecture
and app/ZIP fingerprints. A live published-version-to-version UI update exercise
and interactive runtime acceptance remain manual release checks.
