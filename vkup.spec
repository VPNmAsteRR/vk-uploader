# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-сборка Windows-exe для VK Video Uploader.

Собирает один файл: launcher.py + пакет vkup + веб-интерфейс (web/).

Сборка запускается из build_exe.py, вручную обычно не нужна.
"""

from pathlib import Path

ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # веб-интерфейс кладём внутрь бандла: config.py ищет его через _MEIPASS
        (str(ROOT / "web"), "web"),
    ],
    hiddenimports=[
        "vkup",
        "vkup.server",
        "vkup.browser",
        "vkup.engine",
        "vkup.db",
        "vkup.config",
        "vkup.selectors",
        "selenium",
        "selenium.webdriver.chrome.service",
        "selenium.webdriver.chrome.options",
        "selenium.webdriver.common.by",
        "selenium.webdriver.support.ui",
        "selenium.webdriver.support.expected_conditions",
        "selenium.webdriver.common.action_chains",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "test"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VK-Video-Uploader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
