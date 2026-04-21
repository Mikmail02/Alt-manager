"""Playwright Chromium bootstrap — one browser, many contexts."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

_log = logging.getLogger("agent.browser")

try:
    from playwright_stealth import Stealth  # tf-playwright-stealth ≥1.0
    _stealth_instance = Stealth()

    async def _apply_stealth(page: "Page") -> None:
        try:
            await _stealth_instance.apply_stealth_async(page)
        except Exception as exc:
            _log.warning("stealth patch failed: %s", exc)
except ImportError:
    try:
        from playwright_stealth import stealth_async as _sa  # older 0.x API

        async def _apply_stealth(page: "Page") -> None:
            try:
                await _sa(page)
            except Exception as exc:
                _log.warning("stealth patch failed: %s", exc)
    except ImportError:  # pragma: no cover
        async def _apply_stealth(page: "Page") -> None:
            _log.warning("playwright-stealth not installed — skipping")

# Minimal anti-fingerprint flags. Real stealth comes in phase 2; these just stop
# the most obvious "I'm a bot" signals.
_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",  # needed on many VPS images
    "--disable-gpu",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-first-run",
    "--no-default-browser-check",
    "--window-size=1280,800",
]

class BrowserPool:
    """Owns the single headless Chromium process and hands out contexts."""

    def __init__(self, *, headless: bool, user_data_base: Path) -> None:
        self._headless = headless
        self._user_data_base = user_data_base
        self._user_data_base.mkdir(parents=True, exist_ok=True)
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self._headless,
            args=_CHROMIUM_ARGS,
        )
        _log.info("Chromium launched (headless=%s)", self._headless)

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def new_context(self, alt_id: str) -> BrowserContext:
        assert self._browser is not None, "call start() first"
        storage_dir = self._user_data_base / alt_id
        storage_dir.mkdir(parents=True, exist_ok=True)
        ctx = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="Europe/Oslo",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
            },
        )
        return ctx

    @staticmethod
    async def first_page(ctx: BrowserContext) -> Page:
        pages = ctx.pages
        page = pages[0] if pages else await ctx.new_page()
        await _apply_stealth(page)
        return page
