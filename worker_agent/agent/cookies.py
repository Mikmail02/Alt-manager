"""Load/convert exported cookies into Playwright's expected format."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List

_log = logging.getLogger("agent.cookies")

# Playwright requires: name, value, and either (url) or (domain + path).
_REQUIRED = {"name", "value"}
_ALLOWED_SAMESITE = {"Strict", "Lax", "None"}


def _normalize(raw: Dict) -> Dict:
    """Coerce cookie shape from common export formats into Playwright's spec."""
    out: Dict = {
        "name": raw["name"],
        "value": raw["value"],
    }
    # Domain: EditThisCookie drops leading dot, Playwright accepts it either way.
    domain = raw.get("domain") or raw.get("host") or ""
    if domain:
        out["domain"] = domain
    out["path"] = raw.get("path", "/")

    # Expires: EditThisCookie uses 'expirationDate' (float), Playwright wants int.
    expires = raw.get("expires")
    if expires is None:
        expires = raw.get("expirationDate")
    if expires is not None:
        try:
            out["expires"] = int(expires)
        except (TypeError, ValueError):
            pass

    if "httpOnly" in raw:
        out["httpOnly"] = bool(raw["httpOnly"])
    if "secure" in raw:
        out["secure"] = bool(raw["secure"])

    same = raw.get("sameSite")
    if isinstance(same, str):
        cap = same.capitalize()
        if cap == "No_restriction":
            cap = "None"
        if cap == "Unspecified":
            cap = "Lax"
        if cap in _ALLOWED_SAMESITE:
            out["sameSite"] = cap

    return out


def load_cookie_file(path: Path) -> List[Dict]:
    if not path.exists():
        raise FileNotFoundError(f"cookie file missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"cookie file must be a JSON array, got {type(data).__name__}")
    cookies: List[Dict] = []
    skipped = 0
    for entry in data:
        if not isinstance(entry, dict) or not _REQUIRED.issubset(entry.keys()):
            skipped += 1
            continue
        cookies.append(_normalize(entry))
    if skipped:
        _log.warning("%d cookie entries skipped (missing required fields)", skipped)
    return cookies
