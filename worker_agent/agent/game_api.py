"""Thin wrapper around case-clicker.com's JSON API.

We use the Playwright BrowserContext's request client so cookies and
origin headers line up with what the page itself sends.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from playwright.async_api import APIResponse, BrowserContext

_log = logging.getLogger("agent.api")

BASE = "https://case-clicker.com"


class GameAPIError(Exception):
    pass


class GameAPI:
    def __init__(self, ctx: BrowserContext, *, alt_id: str) -> None:
        self._ctx = ctx
        self.alt_id = alt_id

    async def _json(self, resp: APIResponse) -> Any:
        if not resp.ok:
            raise GameAPIError(f"{resp.status} {resp.url}")
        try:
            return await resp.json()
        except Exception as exc:
            raise GameAPIError(f"bad json from {resp.url}: {exc}") from exc

    async def me(self) -> Dict[str, Any]:
        r = await self._ctx.request.get(f"{BASE}/api/me")
        return await self._json(r)

    async def cases(self) -> List[Dict[str, Any]]:
        r = await self._ctx.request.get(f"{BASE}/api/cases")
        data = await self._json(r)
        return data if isinstance(data, list) else []

    async def all_cases(self) -> List[Dict[str, Any]]:
        """Catalogue of every case (for price lookup)."""
        r = await self._ctx.request.get(f"{BASE}/api/cases/cases")
        data = await self._json(r)
        return data if isinstance(data, list) else []

    async def click(self, clicks: int = 500) -> bool:
        r = await self._ctx.request.post(
            f"{BASE}/api/click",
            data=json.dumps({"clicks": clicks}),
            headers={"Content-Type": "application/json"},
        )
        return r.ok

    async def vault(self) -> bool:
        r = await self._ctx.request.post(f"{BASE}/api/vault")
        return r.ok
