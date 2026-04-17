"""Auto-detect the local Tailscale IP so workers can reach CC Hub without config.

Tailscale assigns every node a stable IPv4 in the CGNAT range 100.64.0.0/10.
We try the official CLI first (works even if Tailscale isn't in PATH on
standard Windows installs), then fall back to enumerating local interfaces.
"""
import ipaddress
import os
import socket
import subprocess
from typing import Optional

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW — prevents console flash on Windows.

_TS_PATHS = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
)


def _in_cgnat(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in _CGNAT
    except ValueError:
        return False


def _tailscale_binary() -> Optional[str]:
    for p in _TS_PATHS:
        if os.path.exists(p):
            return p
    return "tailscale"  # fall back to PATH lookup


def detect_tailscale_ip() -> Optional[str]:
    """Return the local Tailscale IPv4, or None if unavailable."""
    binary = _tailscale_binary()
    try:
        result = subprocess.run(
            [binary, "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                ip = line.strip()
                if _in_cgnat(ip):
                    return ip
    except (OSError, subprocess.TimeoutExpired):
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if _in_cgnat(ip):
                return ip
    except (OSError, socket.gaierror):
        pass
    return None
