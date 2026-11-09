# GRAIL'D v0.9.2 macOS support

Status: platform compatibility, native Mac builds, and signed Mac update support are implemented for source version 0.9.2. Native builds run on GitHub-hosted Macs; interactive runtime acceptance and Developer ID notarization remain separate from compilation. Existing signed 0.8.0/0.8.1 release files were not changed.

## Changes

- Native Cocoa/WKWebView UI via pywebview; no Windows WebView2 installation on a Mac.
- A separate entry point avoids loading the unused legacy Tkinter frontend.
- Persistent profiles, preferences, Discord session ciphertext and Canary cache live in `~/Library/Application Support/GRAILD`.
- Session and leaderboard tokens are encrypted with a Fernet key kept in the user's macOS login Keychain. The Apple Keychain backend is selected explicitly; no plaintext credential fallback. Windows retains its existing DPAPI and AppData behavior.
- Keychain may ask the user for permission. A locked/unavailable Keychain returns a sign-in error rather than saving unprotected tokens. Use a stable Developer ID signing identity for releases; changed/ad-hoc identities can cause new Keychain permission prompts.
- Local music remains in user-selected folders. The app bundles pygame, FFmpeg, Python, webview resources, local CSS and fonts.
- Mac builds do not check for updates automatically at startup. Users can still start a check manually in Settings and install a signed update. Windows automatic checks remain enabled. The Mac updater uses architecture-specific publisher-signed manifests and ZIPs, with independent bundle replacement, startup confirmation and rollback; see MAC_AUTO_UPDATES.md.

Accounts and caches are local to each computer. This change does not create cross-device profile synchronization. Friends sign in/create their own accounts on their Mac; the build does not include the publisher's session.

## Build on a Mac

Use native Python 3.13 on each target architecture. An Apple Silicon build is `arm64`; an Intel build is `x86_64`. Avoid running the Apple Silicon build through Rosetta. This is not a universal2 bundle: native Python, pygame and FFmpeg must match the selected architecture. Test each artifact on its actual target architecture and record the minimum macOS version verified.

Copy the source project to the Mac, then from its directory:

```bash
python3.13 -m venv .venv-mac
source .venv-mac/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m unittest test_mac_support -v
python mac_launcher.py
```

First test the development app: choose a local music folder, complete Discord sign-in in the browser, play both modes, and browse Canary. Quit and reopen to check saved login and preferences.

Build the app:

```bash
python build_macos.py
```

This creates one of:

- `dist/releases/v0.9.2/macOS-arm64/GRAILD.app` and `GRAILD-0.9.2-macOS-arm64.zip`
- `dist/releases/v0.9.2/macOS-x86_64/GRAILD.app` and `GRAILD-0.9.2-macOS-x86_64.zip`

The builder checks FFmpeg architecture, builds in a unique staging folder, verifies bundle code signing, and refuses to overwrite an existing output directory. Without a Developer ID identity, PyInstaller uses ad-hoc signing for local testing. **An ad-hoc test build is not a notarized distribution release.** No release is uploaded automatically.

## Developer ID signing and notarization

For distribution, an Apple Developer account and a Developer ID Application certificate installed in the build Mac's Keychain are needed. Keep certificate private keys and Apple credentials outside the project. Use Xcode Command Line Tools for `codesign`, `ditto`, `lipo`, and `xcrun`.

Before the build, set your actual installed signing identity:

```bash
export GRAILD_MAC_SIGNING_IDENTITY='Developer ID Application: YOUR NAME (TEAMID)'
python build_macos.py
```

Archive an existing output folder before rebuilding. Do not overwrite a running installation. Follow [PyInstaller's macOS signing guidance](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing) and Apple's notarization process. Developer ID builds must use the hardened runtime; check the resulting signature and add only justified entitlements if native runtime testing demonstrates they are required.

Configure an Apple notarytool Keychain profile locally using Apple's instructions; never commit credentials. With a configured profile called `GRAILD-notary`, submit the generated ZIP (substitute `x86_64` on Intel):

```bash
xcrun notarytool submit dist/releases/v0.9.2/macOS-arm64/GRAILD-0.9.2-macOS-arm64.zip --keychain-profile GRAILD-notary --wait
xcrun stapler staple dist/releases/v0.9.2/macOS-arm64/GRAILD.app
codesign --verify --deep --strict --verbose=2 dist/releases/v0.9.2/macOS-arm64/GRAILD.app
spctl --assess --type execute --verbose=2 dist/releases/v0.9.2/macOS-arm64/GRAILD.app
```

After successful notarization/stapling, re-create the ZIP from the stapled bundle so friends receive the ticket:

```bash
ditto -c -k --sequesterRsrc --keepParent dist/releases/v0.9.2/macOS-arm64/GRAILD.app dist/releases/v0.9.2/macOS-arm64/GRAILD-0.9.2-macOS-arm64-notarized.zip
```

Upload the final ZIP for each architecture and its matching `graild-update-macos-ARCH.json` to the v0.9.2 GitHub prerelease, alongside separately prepared Windows assets. Generate Mac manifests locally with the existing pinned publisher key after final distribution signing/notarization and ZIP creation. Publisher signatures do not replace Apple Developer ID signing and notarization.

## Mac release acceptance checklist

On **both** Intel and Apple Silicon, using the actual packaged app:

1. Download the final ZIP through a browser, unpack and move `GRAILD.app` to Applications or another user-writable folder. Launch through Finder, without the source folder or a Python installation.
2. Confirm the native window, fonts, styles, and account menu render correctly, including with the network disconnected.
3. Choose an external music folder, including a path with spaces; play Classic and Random modes. Check macOS folder-access permissions if needed.
4. Complete fresh Discord browser consent, close the app, reopen, and confirm persistent login. Check profile and leaderboard loading/sync.
5. Search Canary, view artwork, match a local recording, filter by era, and check cached metadata while offline.
6. Quit before manually replacing the app with another identically signed build. Confirm login, profiles, music-folder preferences, settings and Canary cache remain intact outside the bundle.
7. Confirm Settings checks for signed Mac updates of the correct architecture and never offers a Windows `.exe`. Test a higher signed version, cancellation, restart/install, and rollback after a failed startup.
8. Verify Gatekeeper accepts the downloaded notarized artifact. Do not bypass Gatekeeper as a substitute for this release test.

Record Mac model/architecture, macOS version, artifact checksum, and results before calling the Mac release ready. These native checks have not run on this Windows computer.
