"""Filesystem paths for runtime data and bundled resources.

Runtime data lives in a per-user, OS-appropriate location so it survives
upgrades and stays out of cloud-synced folders:
  - Windows: %APPDATA%\\CCHub
  - macOS:   ~/Library/Application Support/CCHub
  - Linux:   $XDG_CONFIG_HOME/cchub (or ~/.config/cchub)

Read-only bundled data (cases.json, panel.html, icon) lives next to the exe
when frozen, or in the repo during dev.
"""
import os
import sys
from pathlib import Path


def _appdata_root() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "CCHub"
        return Path.home() / "AppData" / "Roaming" / "CCHub"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "CCHub"
    # Linux / other Unix.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "cchub"
    return Path.home() / ".config" / "cchub"


def _resource_root() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys._MEIPASS for --onefile; for --onedir use exe dir.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


APP_DATA_DIR = _appdata_root()
CERT_DIR = APP_DATA_DIR / "cert"
LOG_DIR = APP_DATA_DIR / "logs"

CONFIG_FILE = APP_DATA_DIR / "config.json"
ACCOUNTS_FILE = APP_DATA_DIR / "accounts.json"
SETTINGS_FILE = APP_DATA_DIR / "settings.json"

RESOURCE_DIR = _resource_root()
CASES_FILE = RESOURCE_DIR / "cases.json"
TEMPLATES_DIR = RESOURCE_DIR / "cchub" / "templates"
if not TEMPLATES_DIR.exists():
    TEMPLATES_DIR = RESOURCE_DIR / "templates"

# Tray/window icon. On macOS we prefer the PNG because .ico loads fine in PIL
# but Tk/pywebview on mac can't use .icns for the window icon directly.
_ICON_CANDIDATES = (
    RESOURCE_DIR / "assets" / "icon.ico",
    RESOURCE_DIR / "assets" / "icon.png",
)
ICON_FILE = next((p for p in _ICON_CANDIDATES if p.exists()), _ICON_CANDIDATES[0])


def ensure_dirs() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def migrate_legacy_data(legacy_dir: Path) -> None:
    """Copy accounts.json / settings.json from an old install location once."""
    ensure_dirs()
    for name, target in (("accounts.json", ACCOUNTS_FILE), ("settings.json", SETTINGS_FILE)):
        src = legacy_dir / name
        if src.exists() and not target.exists():
            try:
                target.write_bytes(src.read_bytes())
            except OSError:
                pass
