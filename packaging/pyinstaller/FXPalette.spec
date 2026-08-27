# -*- mode: python ; coding: utf-8 -*-
# Ports EffectPalette's own EffectPalette.spec (read-only CEP reference) - same build shape, just
# pointed at this project's companion/app.py instead of the CEP product's top-level entrypoint.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).parents[1]
ENTRYPOINT = ROOT / "companion" / "app.py"

hiddenimports = []
# These packages select platform backends dynamically, so PyInstaller cannot
# discover them from static imports alone.
for package in ("pynput", "pystray", "watchdog"):
    hiddenimports += collect_submodules(package)
hiddenimports += [
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageTk",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtWebSockets",
]

companion_assets = ROOT / "companion" / "assets"

a = Analysis(
    [str(ENTRYPOINT)],
    pathex=[str(ROOT / "companion")],
    binaries=[],
    datas=[(str(companion_assets), "assets")] if companion_assets.exists() else [],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FX.palette",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FXPalette",
)
