# PyInstaller spec for Case Clicker Hub (macOS).
# Build with:  pyinstaller build_mac.spec --noconfirm --clean
# Output:      dist/CCHub.app  (bundle, no console window)

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path.cwd()

datas = [
    (str(ROOT / "cases.json"), "."),
    (str(ROOT / "assets" / "icon.png"), "assets"),
]
_icns = ROOT / "assets" / "icon.icns"
if _icns.exists():
    datas.append((str(_icns), "assets"))

hiddenimports = []
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
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CCHub",
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CCHub",
)

app = BUNDLE(
    coll,
    name="CCHub.app",
    icon=str(ROOT / "assets" / "icon.icns") if (ROOT / "assets" / "icon.icns").exists() else None,
    bundle_identifier="com.mikmail.cchub",
    info_plist={
        "CFBundleName": "Case Clicker Hub",
        "CFBundleDisplayName": "Case Clicker Hub",
        "CFBundleShortVersionString": "1.0.12",
        "CFBundleVersion": "1.0.12",
        "NSHighResolutionCapable": True,
        # Tray-only apps can hide the Dock icon with LSUIElement=1, but we keep
        # the window so a regular app (no LSUIElement) is the right behaviour.
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "© Mikmail",
    },
)
