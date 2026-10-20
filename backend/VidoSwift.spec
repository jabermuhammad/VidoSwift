# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH)
ui_dist = root.parent / "ui" / "dist"
datas = [(str(ui_dist), "ui/dist"), (str(root.parent / "THIRD-PARTY-NOTICES.md"), ".")]
build_info = os.environ.get("VIDOSWIFT_BUILD_INFO_PATH")
if build_info:
    build_info_path = Path(build_info).resolve()
    if not build_info_path.is_file():
        raise FileNotFoundError(f"VidoSwift build metadata not found: {build_info_path}")
    datas.append((str(build_info_path), "."))
binaries = []
ffmpeg = root / "bin" / "ffmpeg.exe"
if ffmpeg.exists():
    binaries.append((str(ffmpeg), "bin"))
node = root / "bin" / "node.exe"
if node.exists():
    binaries.append((str(node), "bin"))
node_license = root / "bin" / "node-LICENSE.txt"
if node_license.exists():
    datas.append((str(node_license), "bin"))

hidden = ["webview.platforms.edgechromium", "webview.platforms.mshtml", "clr_loader", "pythonnet"]
for package in ("webview", "bottle", "clr_loader", "pythonnet", "yt_dlp_ejs"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hidden += package_hidden
hidden += collect_submodules("yt_dlp")

a = Analysis([str(root / "app.py")], pathex=[str(root / "vendor")], binaries=binaries, datas=datas,
    hiddenimports=hidden, hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=["pytest", "setuptools._vendor"],
    noarchive=False, optimize=1)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], [], [], name="VidoSwift", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None, icon=str(root / "VidoSwift.ico"))
app = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VidoSwift")
