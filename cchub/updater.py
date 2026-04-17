"""Mandatory auto-update against GitHub Releases.

On startup we poll the latest release. If its tag is newer than the installed
version, the tray UI blocks everything with a modal whose only action is to
download + launch the new installer silently. Network failures are non-fatal so
users aren't locked out by GitHub outages.
"""
import json
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
    for asset in payload.get("assets") or []:
        name = (asset.get("name") or "").lower()
        if name.endswith(".exe") and "setup" in name:
            installer_url = asset.get("browser_download_url")
            break
    if installer_url is None:
        for asset in payload.get("assets") or []:
            if (asset.get("name") or "").lower().endswith(".exe"):
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


def download_installer(release: ReleaseInfo) -> Optional[Path]:
    if not release.installer_url:
        return None
    tmp = Path(tempfile.gettempdir()) / f"CCHub-Setup-{release.tag}.exe"
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
    """Fire-and-forget launch of the installer, then terminate current process.

    Inno Setup installer flags:
      /SILENT        — minimal UI, progress only
      /CLOSEAPPLICATIONS — ask the installer to close running instances
      /RESTARTAPPLICATIONS — relaunch after install
    """
    try:
        subprocess.Popen(
            [str(installer), "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
            close_fds=True,
            creationflags=0x00000008 if sys.platform == "win32" else 0,  # DETACHED_PROCESS
        )
    finally:
        sys.exit(0)
