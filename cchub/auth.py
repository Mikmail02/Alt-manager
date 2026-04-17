"""Token-based auth middleware for the Flask API.

All /api/* routes require header `X-Alt-Token` matching the per-install token,
except for a small allowlist used by the worker handshake and the local panel.
Requests from loopback that already carry the token via query string (e.g.
WebSocket upgrades) are also allowed.
"""
from functools import wraps
from typing import Callable

from flask import Flask, jsonify, request

from . import config

_ALLOWLIST = {
    "/api/ping",  # cheap reachability probe for workers before they have a token
}


def _extract_token() -> str:
    header = request.headers.get("X-Alt-Token")
    if header:
        return header.strip()
    qs = request.args.get("token")
    if qs:
        return qs.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def install(app: Flask) -> None:
    @app.before_request
    def _check_token():
        path = request.path or ""
        if not path.startswith("/api/"):
            return None
        if path in _ALLOWLIST:
            return None
        expected = config.token()
        provided = _extract_token()
        if provided and provided == expected:
            return None
        return jsonify({"status": "error", "error": "unauthorized"}), 401


def require_token(fn: Callable) -> Callable:
    """Optional decorator for non-/api/ routes that still need auth."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected = config.token()
        provided = _extract_token()
        if not provided or provided != expected:
            return jsonify({"status": "error", "error": "unauthorized"}), 401
        return fn(*args, **kwargs)

    return wrapper
