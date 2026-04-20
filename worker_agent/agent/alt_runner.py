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
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

        # Telemetry surfaced to the hub via heartbeats.
        self.online = False
        self.last_check: float = 0.0
        self.last_error: str = ""
        self.last_url: str = ""

    async def start(self) -> None:
        self._ctx = await self._pool.new_context(self.alt_id)
        cookies = load_cookie_file(self._cookies_file)
        if cookies:
            await self._ctx.add_cookies(cookies)
            _log.info("[%s] loaded %d cookies", self.alt_id, len(cookies))
        self._page = await BrowserPool.first_page(self._ctx)
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
        """Heuristic: look for a 'logged in' signal in the DOM.

        We don't rely on any single selector (the site evolves). Instead we grab
        the current URL and the <title>, and also probe a few likely signals.
        Good enough for phase 1; phase 2 will inject proper worker-JS that
        reports real account state.
        """
        assert self._page is not None
        self.last_url = self._page.url
        try:
            title = await self._page.title()
        except Exception:
            title = ""

        # Ask the page what it thinks. These selectors are best-effort — if
        # they miss, we fall back to "page loaded without bouncing to /login".
        evidence = await self._page.evaluate(
            """() => {
                const has = (sel) => !!document.querySelector(sel);
                return {
                    hasLoginBtn: has('a[href*="login" i]') || has('button[data-action="login"]'),
                    hasLogout:  has('a[href*="logout" i]') || has('button[data-action="logout"]'),
                    hasUserMenu: has('[class*="user" i][class*="menu" i]'),
                    pathname: location.pathname || '/',
                };
            }"""
        )
        path = (evidence or {}).get("pathname", "/")
        logged_in_hint = (evidence or {}).get("hasLogout") or (evidence or {}).get("hasUserMenu")
        logged_out_hint = (evidence or {}).get("hasLoginBtn") and not logged_in_hint

        if logged_in_hint:
            self.online = True
            self.last_error = ""
        elif logged_out_hint or path.startswith("/login"):
            self.online = False
            self.last_error = "not logged in"
        else:
            # Ambiguous — assume online if the page loaded something meaningful.
            self.online = bool(title)
            if not title:
                self.last_error = "blank page"

        self.last_check = time.time()
        _log.info(
            "[%s] check: online=%s path=%s title=%r",
            self.alt_id,
            self.online,
            path,
            title[:60],
        )

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.alt_id,
            "username": self.username,
            "online": self.online,
            "last_check": self.last_check,
            "last_error": self.last_error,
            "last_url": self.last_url,
        }
