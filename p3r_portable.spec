# -*- mode: python ; coding: utf-8 -*-
"""Portable Windows build: one folder, two executables, one ``_internal``.

    py -3 -m PyInstaller --noconfirm p3r_portable.spec

Produces ``dist/P3R-Save-Transfer/`` with

    P3R-Save-Transfer.exe       windowed GUI (no console)
    p3r-save-transfer-cli.exe   console CLI for scripts
    _internal/                  Python runtime, Tk, assets
"""

import os

block_cipher = None

version_file = os.path.join("build", "version_info.txt")
if not os.path.isfile(version_file):
    version_file = None

a = Analysis(
    ["ui_entry.py"],
    pathex=["src", "."],
    binaries=[],
    datas=[("assets", "assets")],
    hiddenimports=["ui.app", "ui.bridge", "ui.config", "p3r_transfer.cli"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pydoc", "doctest", "unittest", "tkinter.test", "test", "pdb"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="P3R-Save-Transfer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version=version_file,
    manifest="tools/app.manifest",
)

cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="p3r-save-transfer-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version=version_file,
    manifest="tools/app.manifest",
)

coll = COLLECT(
    gui,
    cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="P3R-Save-Transfer",
)
