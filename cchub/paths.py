"""Filesystem paths for runtime data and bundled resources.

Runtime data lives in %APPDATA%\\CCHub\\ so it survives upgrades and reinstalls
and stays out of OneDrive-synced folders. Read-only bundled data (cases.json,
panel.html, icon) lives next to the exe when frozen, or in the repo during dev.
"""
import os
import sys
from pathlib import Path


def _appdata_root() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "CCHub"
    return Path.home() / ".cchub"


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
ICON_FILE = RESOURCE_DIR / "assets" / "icon.ico"


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
