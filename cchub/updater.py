"""Mandatory auto-update against GitHub Releases.

On startup we poll the latest release. If its tag is newer than the installed
version, the tray UI blocks everything with a modal whose only action is to
download + launch the new installer. On Windows the Inno Setup installer
runs silently; on macOS we open the .dmg in Finder and quit so the user can
drag-replace the .app. Network failures are non-fatal so users aren't locked
out by GitHub outages.
"""
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .version import GITHUB_OWNER, GITHUB_REPO, __version__

# Release-asset suffix we look for on the current platform.
_ASSET_EXT = ".dmg" if sys.platform == "darwin" else ".exe"

RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass
class ReleaseInfo:
    tag: str
    version: Tuple[int, int, int]
    installer_url: Optional[str]
    body: str


def _parse_version(s: str) -> Optional[Tuple[int, int, int]]:
    m = _VERSION_RE.search(s or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def current_version() -> Tuple[int, int, int]:
    return _parse_version(__version__) or (0, 0, 0)


def fetch_latest(timeout: float = 10.0) -> Optional[ReleaseInfo]:
    req = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"CCHub/{__version__}",
        },
    )
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    tag = payload.get("tag_name") or ""
    version = _parse_version(tag)
    if version is None:
        return None

    installer_url = None
    # Prefer an asset whose name contains "setup" for Windows or "cchub" for mac.
    preferred_kw = "setup" if _ASSET_EXT == ".exe" else "cchub"
    for asset in payload.get("assets") or []:
        name = (asset.get("name") or "").lower()
        if name.endswith(_ASSET_EXT) and preferred_kw in name:
            installer_url = asset.get("browser_download_url")
            break
    if installer_url is None:
        for asset in payload.get("assets") or []:
            if (asset.get("name") or "").lower().endswith(_ASSET_EXT):
                installer_url = asset.get("browser_download_url")
                break

    return ReleaseInfo(
        tag=tag,
        version=version,
        installer_url=installer_url,
        body=payload.get("body") or "",
    )


def is_update_required(release: Optional[ReleaseInfo]) -> bool:
    if release is None or release.installer_url is None:
        return False
    return release.version > current_version()


def fetch_commits_between(old_tag: str, new_tag: str, timeout: float = 10.0) -> list:
    """Return commit subjects (first line only) between two tags, oldest first."""
    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/compare/{old_tag}...{new_tag}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"CCHub/{__version__}",
        },
    )
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    subjects = []
    for entry in payload.get("commits") or []:
        msg = ((entry.get("commit") or {}).get("message") or "").strip()
        if not msg:
            continue
        first = msg.splitlines()[0].strip()
        if first:
            subjects.append(first)
    return subjects


def download_installer(release: ReleaseInfo) -> Optional[Path]:
    if not release.installer_url:
        return None
    filename = f"CCHub-Setup-{release.tag}{_ASSET_EXT}"
    tmp = Path(tempfile.gettempdir()) / filename
    req = urllib.request.Request(release.installer_url, headers={"User-Agent": f"CCHub/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1024 * 64)
                if not chunk:
                    break
                f.write(chunk)
    except Exception:
        return None
    return tmp


def launch_installer_and_exit(installer: Path) -> None:
    """Launch the installer asset, then terminate current process.

    Windows: Inno Setup installer runs silently and restarts the app.
    macOS:   `open` the .dmg in Finder so the user drag-replaces CCHub.app;
             the app quits so the old copy isn't held open.
    """
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(installer)], close_fds=True)
        else:
            subprocess.Popen(
                [str(installer), "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
                close_fds=True,
                creationflags=0x00000008 if sys.platform == "win32" else 0,  # DETACHED_PROCESS
            )
    finally:
        # On mac we want to fully exit so a newly-dragged app can launch cleanly.
        os._exit(0) if sys.platform == "darwin" else sys.exit(0)
