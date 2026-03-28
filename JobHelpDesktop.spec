# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

project_root = Path(SPECPATH).resolve()
frontend_dist = project_root / "frontend_dist"
desktop_assets = project_root / "desktop_assets"
app_icon = desktop_assets / "app_icon.ico"

datas = []
if frontend_dist.exists():
    datas.append((str(frontend_dist), "frontend_dist"))
if desktop_assets.exists():
    datas.append((str(desktop_assets), "desktop_assets"))

binaries = []
hiddenimports = []
hiddenimports += collect_submodules("app")
for package_name in ("uvicorn", "playwright", "webview", "pydantic_settings", "sqlalchemy"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package_name)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports


a = Analysis(
    [str(project_root / "run_desktop.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
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
    name="JobHelpDesktop",
    icon=str(app_icon) if app_icon.exists() else None,
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
    name="JobHelpDesktop",
)
