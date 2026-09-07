# -*- mode: python ; coding: utf-8 -*-
# Ports EffectPalette's own EffectPalette.spec (read-only CEP reference) - same build shape, just
# pointed at this project's companion/app.py instead of the CEP product's top-level entrypoint.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


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
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtWebSockets",
]

companion_assets = ROOT / "companion" / "assets"
APP_ICON = companion_assets / "fx_palette.ico"

# Written into the exe's Properties -> Details tab. Without this the file shows up as a
# nameless PyInstaller bootloader.
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=(0, 54, 0, 0), prodvers=(0, 54, 0, 0)),
    kids=[
        StringFileInfo([
            StringTable("040904B0", [
                StringStruct("CompanyName", "paulo.edits"),
                StringStruct("FileDescription", "FX.palette - search palette for Adobe Premiere Pro"),
                StringStruct("FileVersion", "0.54.0"),
                StringStruct("InternalName", "FX.palette"),
                StringStruct("LegalCopyright", "Copyright (c) paulo.edits"),
                StringStruct("OriginalFilename", "FX.palette.exe"),
                StringStruct("ProductName", "FX.palette"),
                StringStruct("ProductVersion", "0.54.0"),
            ]),
        ]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

a = Analysis(
    [str(ENTRYPOINT)],
    pathex=[str(ROOT / "companion")],
    binaries=[],
    datas=[(str(companion_assets), "assets")] if companion_assets.exists() else [],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The companion is Qt-only since the 2026-09 audit; keep tkinter out of the bundle.
    excludes=["tkinter", "_tkinter", "PIL.ImageTk"],
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
    icon=str(APP_ICON),
    version=version_info,
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
