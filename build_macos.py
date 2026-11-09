"""Build a native macOS app; run separately on Apple Silicon and Intel Macs."""
import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import uuid


UI_ASSET_FILES = (
    'tailwind.css', 'fonts.css', 'inter-400.ttf', 'inter-600.ttf',
    'inter-700.ttf', 'inter-900.ttf', 'Inter-OFL.txt', 'Tailwind-LICENSE.txt',
    'graild-logo.png', 'graild.ico', 'graild.icns',
    'graild-ui.css', 'graild-ui.js', 'graild-challenges.js',
)


def ensure_ui_assets(root):
    """Repair browser uploads that flattened assets/ into the repository root.

    Only the known UI resources are copied, and existing assets are preserved.
    Validate the whole list before creating anything so incomplete uploads fail
    with an actionable message rather than a PyInstaller traceback.
    """
    root = Path(root).resolve()
    assets = root / 'assets'
    missing = []
    repairs = []
    for name in UI_ASSET_FILES:
        target = assets / name
        if target.is_file():
            continue
        source = root / name
        if source.is_file():
            repairs.append((source, target))
        else:
            missing.append('assets/' + name)
    if missing:
        raise SystemExit('Missing UI resources: ' + ', '.join(missing) +
                         '. Upload the assets folder, or its files to the repository root; the builder can restore the folder.')
    if repairs:
        assets.mkdir(exist_ok=True)
        for source, target in repairs:
            shutil.copyfile(source, target)
            print('Restored uploaded resource: assets/' + target.name, flush=True)
    return assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--expected-arch', choices=['arm64', 'x86_64'])
    args = parser.parse_args()
    if sys.platform != 'darwin':
        raise SystemExit('Build this app on a Mac. PyInstaller cannot produce a macOS app from this Windows installation.')
    from app_version import APP_VERSION
    import imageio_ffmpeg
    root = Path(__file__).resolve().parent
    arch = platform.machine()
    if arch not in ('arm64', 'x86_64'):
        raise SystemExit('Use native arm64 or x86_64 Python.')
    if args.expected_arch and arch != args.expected_arch:
        raise SystemExit('This build kit requires native ' + args.expected_arch + ' Python.')
    ffmpeg_arch = subprocess.check_output(['lipo', '-archs', imageio_ffmpeg.get_ffmpeg_exe()], text=True)
    if arch not in ffmpeg_arch.split():
        raise SystemExit('The installed FFmpeg binary does not match this Python architecture.')
    output = args.output_dir.resolve() if args.output_dir else root / 'dist' / 'releases' / ('v' + APP_VERSION) / ('macOS-' + arch)
    if output.exists():
        raise SystemExit('Output already exists; archive it before rebuilding. Nothing was overwritten.')
    ensure_ui_assets(root)
    stage = root / 'build' / ('mac-' + uuid.uuid4().hex)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--distpath', str(stage / 'dist'), '--workpath', str(stage / 'work'),
                    str(root / 'GRAILD-mac.spec')], cwd=root, check=True)
    output.mkdir(parents=True)
    app = output / 'GRAILD.app'
    shutil.move(str(stage / 'dist' / 'GRAILD.app'), app)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    archive = output / ('GRAILD-' + APP_VERSION + '-macOS-' + arch + '.zip')
    subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(archive)], check=True)
    from mac_update_security import archive_bundle_digest, bundle_digest, verify_bundle
    digest = archive_bundle_digest(archive)
    if digest != bundle_digest(app):
        raise SystemExit('The archived Mac app does not match the native bundle. Do not publish it.')
    verify_bundle(app, {'bundle_sha256':digest,'version':APP_VERSION,'architecture':arch})
    print('Built locally (not published):', archive)
    print('Distribution still requires Developer ID signing/notarization and testing on this Mac.')


if __name__ == '__main__': main()
