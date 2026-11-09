"""Prepare a secret-free native Mac build handoff without claiming a native build."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import zipfile

FILES = ['mac_launcher.py', 'web_app.py', 'platform_support.py', 'app_version.py',
         'auto_update.py', 'update_security.py', 'update_health.py', 'update_trust.py',
         'achievements.py', 'random_snippets.py', 'audio_files.py', 'canary_client.py',
         'canary_integration.py', 'index.html', 'canary_ui.js', 'update_ui.js',
         'requirements.txt', 'GRAILD-mac.spec', 'build_macos.py', 'test_mac_support.py',
         'MACOS_SUPPORT.md', 'test_account_tags.py', 'test_achievements.py',
         'test_mac_build_assets.py', 'test_audio_files.py', 'test_game_modes.py', 'test_round_resume.py', 'test_brand_assets.py',
         'mac_updater.py', 'mac_update_security.py', 'test_mac_updater.py', 'MAC_AUTO_UPDATES.md',
         'tls_support.py', 'test_tls_support.py', 'oauth_config.py', 'test_oauth_config.py',
         'GOOGLE_OAUTH.md', 'prepare_mac_release.py']
COMMAND = '''#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "x86_64" ]]; then
  echo "Run this Intel build kit on an Intel Mac."; exit 1
fi
if ! command -v python3.13 >/dev/null 2>&1; then
  echo "Install native Intel Python 3.13 from python.org, then run this again."; exit 1
fi
python3.13 -m venv .venv-mac
source .venv-mac/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m unittest test_mac_support test_mac_updater test_mac_build_assets test_brand_assets test_tls_support test_oauth_config test_account_tags test_achievements test_audio_files test_game_modes test_round_resume -v
python build_macos.py --expected-arch x86_64 --output-dir ../macOS-x86_64
echo "Built in ../macOS-x86_64. Native testing and Developer ID notarization are still required."
'''
README = '''# GRAIL'D 0.9.0 — Intel Mac build handoff

This folder contains source and build tools, NOT a compiled Mac application.
No Mac app could be built on the Windows development computer.

On the Intel Mac:

1. Extract GRAILD-0.9.0-macOS-x86_64-build-kit.zip into a writable folder.
2. Install native Intel Python 3.13 and Xcode Command Line Tools (`xcode-select --install`).
3. Open Terminal in the extracted GRAILD-macOS-x86_64-build-kit folder.
4. Run `bash Build.command`.
5. Find GRAILD.app and its ZIP in the sibling macOS-x86_64 folder.

The local test app is ad-hoc signed unless GRAILD_MAC_SIGNING_IDENTITY is set
before building. For distribution, follow MACOS_SUPPORT.md for Developer ID
signing, notarization, and testing. Do not publish this build kit as the playable
Mac download. Friends will only need the final notarized app ZIP, not Python.

This build supports signed Mac updates. See MAC_AUTO_UPDATES.md for the per-architecture manifests and release signing procedure. Existing manual-only Mac builds require this build to be installed once.
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch', choices=['x86_64', 'arm64'], default='x86_64')
    args = parser.parse_args()
    label = 'Apple Silicon' if args.arch == 'arm64' else 'Intel'
    command = COMMAND.replace('x86_64', args.arch).replace('Intel', label)
    root = Path(__file__).resolve().parent
    from app_version import APP_VERSION
    readme = README.replace('0.9.0', APP_VERSION).replace('x86_64', args.arch).replace('Intel', label)
    release = root / 'dist/releases' / ('v' + APP_VERSION)
    kit = release / ('GRAILD-macOS-' + args.arch + '-build-kit')
    archive = release / ('GRAILD-' + APP_VERSION + '-macOS-' + args.arch + '-build-kit.zip')
    if kit.exists() or archive.exists():
        raise SystemExit('Build handoff already exists; archive it first. Nothing was overwritten.')
    for filename in FILES:
        if not (root / filename).is_file():
            raise SystemExit('Missing required source: ' + filename)
    kit.mkdir(parents=True)
    for filename in FILES:
        shutil.copyfile(root / filename, kit / filename)
    shutil.copytree(root / 'assets', kit / 'assets')
    (kit / 'Build.command').write_text(command, encoding='utf-8', newline='\n')
    (kit / 'README.md').write_text(readme, encoding='utf-8')
    hashes = {str(p.relative_to(kit)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(kit.rglob('*')) if p.is_file()}
    (kit / 'source-checksums.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in sorted(kit.rglob('*')):
            if not path.is_file(): continue
            name = path.relative_to(release).as_posix()
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            mode = 0o755 if path.name == 'Build.command' else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(info, path.read_bytes())
    (release / ('MACOS-' + args.arch + '-STATUS.md')).write_text(readme, encoding='utf-8')
    shutil.copyfile(root / 'release-notes' / ('v' + APP_VERSION + '.md'), release / 'release-notes.md')
    print('Prepared ' + label + ' Mac source handoff:', archive)
    print('Native GRAILD.app status: pending build and testing on the ' + label + ' Mac.')


if __name__ == '__main__': main()
