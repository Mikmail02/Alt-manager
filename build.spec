# PyInstaller spec for Case Clicker Hub.
# Build with:  pyinstaller build.spec --noconfirm --clean
# Output:      dist/CCHub.exe  (single-file, no console window)

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path.cwd()

datas = [
    (str(ROOT / "cases.json"), "."),
    (str(ROOT / "assets" / "icon.ico"), "assets"),
]

hiddenimports = ["certifi"]
hiddenimports += collect_submodules("cryptography")
hiddenimports += collect_submodules("pystray")
hiddenimports += collect_submodules("webview")

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter.test", "test", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="CCHub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.ico"),
    version=str(ROOT / "build_version.txt"),
)
