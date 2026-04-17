"""Per-install config: API token + auto-start preference."""
import json
import secrets
from typing import Any, Dict

from . import paths


def _default() -> Dict[str, Any]:
    return {
        "api_token": secrets.token_urlsafe(32),
        "auto_start": False,
        "first_run_completed": False,
    }


def load() -> Dict[str, Any]:
    paths.ensure_dirs()
    if not paths.CONFIG_FILE.exists():
        data = _default()
        save(data)
        return data
    try:
        data = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = _default()
        save(data)
        return data

    changed = False
    for key, value in _default().items():
        if key not in data:
            data[key] = value
            changed = True
    if changed:
        save(data)
    return data


def save(data: Dict[str, Any]) -> None:
    paths.ensure_dirs()
    paths.CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def token() -> str:
    return load()["api_token"]
