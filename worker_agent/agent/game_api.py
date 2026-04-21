"""Thin wrapper around case-clicker.com's JSON API.

We call fetch() from inside the page (via page.evaluate), so every request
inherits the exact cookies, origin, referer, and sec-fetch headers the site
expects. This is the same trick the old Tampermonkey workers relied on.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from playwright.async_api import Page

_log = logging.getLogger("agent.api")


class GameAPIError(Exception):
    pass


class GameAPI:
    def __init__(self, page: Page, *, alt_id: str) -> None:
        self._page = page
        self.alt_id = alt_id

    async def _fetch(
        self,
        path: str,
        *,
        method: str = "GET",
        body: Optional[Any] = None,
    ) -> Any:
        js = """
        async ({path, method, body}) => {
            const opts = { method, headers: {} };
            if (body !== null) {
                opts.headers['Content-Type'] = 'application/json';
                opts.body = JSON.stringify(body);
            }
            const r = await fetch(path, opts);
            const text = await r.text();
            let parsed = null;
            try { parsed = text ? JSON.parse(text) : null; } catch(_) {}
            return { ok: r.ok, status: r.status, text, json: parsed };
        }
        """
        try:
            res = await self._page.evaluate(js, {"path": path, "method": method, "body": body})
        except Exception as exc:
            raise GameAPIError(f"evaluate failed for {path}: {exc}") from exc
        if not res.get("ok"):
            body = (res.get("text") or "")[:200]
            raise GameAPIError(f"{res.get('status')} {path} body={body!r}")
        return res.get("json")

    async def me(self) -> Dict[str, Any]:
        data = await self._fetch("/api/me")
        return data if isinstance(data, dict) else {}

    async def cases(self) -> List[Dict[str, Any]]:
        data = await self._fetch("/api/cases")
        return data if isinstance(data, list) else []

    async def all_cases(self) -> List[Dict[str, Any]]:
        data = await self._fetch("/api/cases/cases")
        return data if isinstance(data, list) else []

    async def click(self, clicks: int = 500) -> bool:
        try:
            await self._fetch("/api/click", method="POST", body={"clicks": clicks})
            return True
        except GameAPIError:
            return False

    async def vault(self) -> bool:
        try:
            await self._fetch("/api/vault", method="POST")
            return True
        except GameAPIError:
            return False
