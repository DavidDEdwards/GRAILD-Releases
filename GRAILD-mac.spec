# -*- mode: python ; coding: utf-8 -*-
import os
import runpy
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH)
version = runpy.run_path(str(root / "app_version.py"))["APP_VERSION"]
datas = [(str(root / name), '.') for name in ('index.html', 'canary_ui.js', 'update_ui.js')]
datas += [(str(root / 'assets'), 'assets')]
datas += collect_data_files('certifi')
binaries = []
hiddenimports = ['keyring.backends.macOS']
for package in ('pygame', 'webview', 'imageio_ffmpeg', 'keyring'):
    data, binary, hidden = collect_all(package)
    # pywebview ships Windows SDK DLLs alongside Cocoa resources. Do not ship
    # or attempt to Mach-O-sign those PE files in a native Mac bundle.
    data = [item for item in data if not item[0].lower().endswith(('.dll', '.exe', '.pyd'))]
    binary = [item for item in binary if not item[0].lower().endswith(('.dll', '.exe', '.pyd'))]
    datas += data; binaries += binary; hiddenimports += hidden

a = Analysis([str(root / 'mac_launcher.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports,
             excludes=['tkinter', 'webview.platforms.winforms', 'webview.platforms.edgechromium',
                       'webview.platforms.mshtml', 'webview.platforms.gtk', 'webview.platforms.qt'],
             noarchive=False)
pyz = PYZ(a.pure)
identity = os.environ.get('GRAILD_MAC_SIGNING_IDENTITY') or None
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='GRAILD', console=False,
          codesign_identity=identity, target_arch=None)
coll = COLLECT(exe, a.binaries, a.datas, name='GRAILD')
app = BUNDLE(coll, name='GRAILD.app', bundle_identifier='com.graild.desktop',
             icon=str(root / 'assets' / 'graild.icns'),
             codesign_identity=identity,
             info_plist={'CFBundleShortVersionString': version, 'CFBundleVersion': version,
                         'NSHighResolutionCapable': True,
                         'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True}})
