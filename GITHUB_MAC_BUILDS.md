# Build the Mac apps using GitHub

The workflow is `.github/workflows/build-macos.yml`. It builds Intel (`x86_64`, macos-15-intel) and Apple Silicon (`arm64`, macos-15) using native Python 3.13 on GitHub-hosted Macs. It runs platform tests, uses the existing Mac builder/spec, verifies bundle signatures, and uploads the resulting ZIPs plus checksums/build information. It never creates/publishes a GitHub Release or modifies Windows release assets.

## Upload and run

1. Extract `dist/releases/v0.9.0/GRAILD-GitHub-Mac-Build.zip` on your computer.
2. Upload the **contents** to the root of a GitHub source repository. Uploading the ZIP itself is insufficient: GitHub needs the Python files, `assets/`, and `.github/workflows/build-macos.yml` as actual repository files.
3. You can use DavidDEdwards/GRAILD-Releases if you want the app source in that public repository, or use a separate private source repository. Do not upload your entire development folder, signing keys, user data, old EXEs, or broker secrets. The prepared ZIP contains only an explicit list of build inputs.
4. Ensure `.github/workflows/build-macos.yml` exists on the repository's default branch. If the browser upload omitted the hidden `.github` folder, use **Add file > Create new file**, enter `.github/workflows/build-macos.yml`, and paste the included workflow file. Commit it.
5. Open **Actions > Build GRAILD for macOS > Run workflow**, select the branch, then run it. Enable Actions if the repository asks.
6. Wait for both jobs. Open the completed run and download `GRAILD-macOS-arm64` for Apple Silicon or `GRAILD-macOS-x86_64` for Intel from **Artifacts**.
7. Extract GitHub's artifact ZIP first. Inside is the actual `GRAILD-0.9.0-macOS-ARCH.zip`. Send that inner ZIP to your friend; extracting it produces `GRAILD.app`. They do not need Python or Xcode to run this compiled app.

Artifacts are retained for 14 days. Download and keep the builds you need. Private-repository workflows may consume your plan's Actions allowance; check the repository/account billing settings before running if relevant.

## What this first workflow verifies

It checks platform unit tests and compilation, correct Python/FFmpeg architecture, native bundle creation, and the bundle's ad-hoc signature. It does not automate interactive Discord consent, real audio devices, or GUI gameplay. A build passing on GitHub is not yet a completed Mac runtime acceptance test.

Your friend should test launch, folder selection, both audio modes, Discord login/reopening, Canary metadata/artwork/era filtering, leaderboard loading, and settings persistence. If a build fails, send the failed step's log. If the app fails, send the exact macOS error and their OS version/architecture.

These are **ad-hoc signed test builds, not Developer ID-notarized releases**. macOS Gatekeeper may prevent normal downloaded-app launch. Normal distribution still needs Apple Developer ID signing/notarization as described in MACOS_SUPPORT.md. No Apple or GRAILD private signing secrets are used by this workflow. Windows update manifests are not used for the Mac ZIPs; Mac updates remain manual.

This workflow has been prepared locally; no remote Actions run or release publication has occurred.

References: https://docs.github.com/en/actions/reference/runners/github-hosted-runners and https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts .
