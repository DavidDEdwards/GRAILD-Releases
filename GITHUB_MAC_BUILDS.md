# Build the Mac apps using GitHub

The workflow is `.github/workflows/build-macos.yml`. It builds Intel (`x86_64`, macos-15-intel) and Apple Silicon (`arm64`, macos-15) using native Python 3.13 on GitHub-hosted Macs. It runs platform tests, uses the existing Mac builder/spec, verifies bundle signatures, and uploads the resulting ZIPs plus checksums/build information. It never creates/publishes a GitHub Release or modifies Windows release assets.

## Upload and run

1. Extract `dist/releases/v0.9.2/GRAILD-GitHub-Mac-Build-no-auto-updates.zip` on your computer. This bundle disables automatic startup checks on macOS but keeps manual update checks and signed installs.
2. Upload the **contents** to the root of a GitHub source repository. Uploading the ZIP itself is insufficient: GitHub needs the Python files, `assets/`, and `.github/workflows/build-macos.yml` as actual repository files.
3. You can use DavidDEdwards/GRAILD-Releases if you want the app source in that public repository, or use a separate private source repository. Do not upload your entire development folder, signing keys, user data, old EXEs, or broker secrets. The prepared ZIP contains only an explicit list of build inputs.
4. Ensure `.github/workflows/build-macos.yml` exists on the repository's default branch. If the browser upload omitted the hidden `.github` folder, use **Add file > Create new file**, enter `.github/workflows/build-macos.yml`, and paste the included workflow file. Commit it.
5. Open **Actions > Build GRAILD for macOS > Run workflow**, select the branch, then run it. Enable Actions if the repository asks.
6. Wait for both jobs. Open the completed run and download `GRAILD-macOS-arm64` for Apple Silicon or `GRAILD-macOS-x86_64` for Intel from **Artifacts**.
7. Extract GitHub's artifact ZIP first. Inside is the actual `GRAILD-0.9.2-macOS-ARCH.zip`. Copy that inner ZIP to `dist/releases/v0.9.2/macOS-ARCH/`; extracting it produces `GRAILD.app`. They do not need Python or Xcode to run this compiled app.

Artifacts are retained for 14 days. Download and keep the builds you need. Private-repository workflows may consume your plan's Actions allowance; check the repository/account billing settings before running if relevant.

## What this first workflow verifies

It checks platform unit tests and compilation, correct Python/FFmpeg architecture, native bundle creation, and the bundle's ad-hoc signature. It does not automate interactive Discord consent, real audio devices, or GUI gameplay. A build passing on GitHub is not yet a completed Mac runtime acceptance test.

Your friend should test launch, folder selection, both audio modes, Discord login/reopening, Canary metadata/artwork/era filtering, leaderboard loading, and settings persistence. If a build fails, send the failed step's log. If the app fails, send the exact macOS error and their OS version/architecture.

These are **ad-hoc signed test builds, not Developer ID-notarized releases**. macOS Gatekeeper may prevent normal downloaded-app launch. Normal distribution still needs Apple Developer ID signing/notarization as described in MACOS_SUPPORT.md. No Apple or GRAILD private signing secrets are used by this workflow. Mac updates use separate architecture-specific publisher-signed manifests, generated locally after downloading the final ZIPs. See MAC_AUTO_UPDATES.md.

The updater-enabled 0.9.1 workflow succeeded for Intel and Apple Silicon in [run 41418669746](https://github.com/DavidDEdwards/GRAILD-Releases/actions/runs/41418669746), using commit 96c2fb2. The 0.9.2 no-automatic-check source is committed on branch `codex/graild-0.9.2-mac-no-auto-updates`. Both native jobs passed in [run 41474228704](https://github.com/DavidDEdwards/GRAILD-Releases/actions/runs/41474228704), and their ZIPs and signed manifests are in `dist/releases/v0.9.2/`. No GitHub Release was published. Developer ID notarization and interactive Mac runtime tests remain pending.

References: https://docs.github.com/en/actions/reference/runners/github-hosted-runners and https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts .

## Browser uploads that flatten assets

If the CSS/font files appear at the repository root, the updated `build_macos.py` restores the known files into `assets/` before calling PyInstaller. Existing files inside `assets/` are preserved. Incomplete uploads fail with a list of missing files. To repair the original GitHub upload, replace only `build_macos.py` with the updated project file, commit to main, and start a new workflow run; rerunning an old run uses its old commit.
