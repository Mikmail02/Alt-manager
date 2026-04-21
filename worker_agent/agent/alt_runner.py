"""One alt's lifecycle: own context, own page, own login state."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.async_api import BrowserContext, Page, TimeoutError as PWTimeout

from .browser import BrowserPool
from .cookies import load_cookie_file
from .game_api import GameAPI, GameAPIError

_log = logging.getLogger("agent.alt")

CC_URL = "https://case-clicker.com/"
CHECK_INTERVAL_SEC = 30


class AltRunner:
    def __init__(
        self,
        *,
        alt_id: str,
        username: str,
        cookies_file: Path,
        pool: BrowserPool,
    ) -> None:
        self.alt_id = alt_id
        self.username = username
        self._cookies_file = cookies_file
        self._pool = pool
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._api: Optional[GameAPI] = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

        # Telemetry surfaced to the hub via heartbeats.
        self.online = False
        self.last_check: float = 0.0
        self.last_error: str = ""
        self.last_url: str = ""
        self.user_id: str = ""
        self.money: float = 0.0
        self.networth: float = 0.0
        self.case_count: int = 0

    async def start(self) -> None:
        self._ctx = await self._pool.new_context(self.alt_id)
        cookies = load_cookie_file(self._cookies_file)
        if cookies:
            await self._ctx.add_cookies(cookies)
            _log.info("[%s] loaded %d cookies", self.alt_id, len(cookies))
        self._page = await BrowserPool.first_page(self._ctx)
        self._api = GameAPI(self._ctx, alt_id=self.alt_id)
        self._task = asyncio.create_task(self._loop(), name=f"alt-{self.alt_id}")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ctx:
            try:
                await self._ctx.close()
            except Exception:
                pass
            self._ctx = None

    async def _loop(self) -> None:
        try:
            await self._navigate()
        except Exception as exc:
            self.last_error = f"initial nav: {exc}"
            _log.exception("[%s] initial navigation failed", self.alt_id)

        while not self._stopping:
            try:
                await self._check_login()
            except Exception as exc:
                self.last_error = f"check: {exc}"
                _log.warning("[%s] login check failed: %s", self.alt_id, exc)
            await asyncio.sleep(CHECK_INTERVAL_SEC)

    async def _navigate(self) -> None:
        assert self._page is not None
        _log.info("[%s] navigating to %s", self.alt_id, CC_URL)
        try:
            await self._page.goto(CC_URL, wait_until="domcontentloaded", timeout=30_000)
        except PWTimeout:
            self.last_error = "nav timeout"
            return
        self.last_url = self._page.url

    async def _check_login(self) -> None:
        """Authoritative: call /api/me. If it returns user data, we're logged in."""
        assert self._page is not None and self._api is not None
        self.last_url = self._page.url
        self.last_check = time.time()

        try:
            me = await self._api.me()
        except GameAPIError as exc:
            self.online = False
            self.last_error = f"api/me: {exc}"
            _log.info("[%s] check: online=False err=%s", self.alt_id, exc)
            return
        except Exception as exc:
            self.online = False
            self.last_error = f"api/me crash: {exc}"
            _log.warning("[%s] /api/me crashed: %s", self.alt_id, exc)
            return

        uid = me.get("_id") or ""
        if not uid:
            self.online = False
            self.last_error = "api/me: no _id"
            return

        self.online = True
        self.last_error = ""
        self.user_id = str(uid)
        self.money = float(me.get("money") or 0)
        self.networth = float(me.get("networth") or 0)

        try:
            cases = await self._api.cases()
            self.case_count = sum(int(c.get("amount") or 0) for c in cases)
        except Exception as exc:
            _log.debug("[%s] cases fetch failed: %s", self.alt_id, exc)

        _log.info(
            "[%s] check: online=True money=%.0f nw=%.0f cases=%d",
            self.alt_id,
            self.money,
            self.networth,
            self.case_count,
        )

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.alt_id,
            "username": self.username,
            "online": self.online,
            "last_check": self.last_check,
            "last_error": self.last_error,
            "last_url": self.last_url,
            "user_id": self.user_id,
            "money": self.money,
            "networth": self.networth,
            "case_count": self.case_count,
        }
