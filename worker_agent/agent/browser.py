"""Playwright Chromium bootstrap — one browser, many contexts."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

_log = logging.getLogger("agent.browser")

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

_STEALTH_INIT = """
// Remove the automation flag.
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// window.chrome stub — headless Chromium ships without it.
if (!window.chrome) {
    window.chrome = { runtime: {}, app: { isInstalled: false } };
}

// Plugins array — bot detectors check .length > 0.
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        { name: 'Chrome PDF Plugin' },
        { name: 'Chrome PDF Viewer' },
        { name: 'Native Client' },
    ],
});

// Languages — headless reports just ['en-US'] or empty.
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
});

// Notification.permission — headless returns 'denied' which is flagged.
const origQuery = navigator.permissions && navigator.permissions.query;
if (origQuery) {
    navigator.permissions.query = (params) =>
        params && params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery.call(navigator.permissions, params);
}

// WebGL vendor + renderer — headless returns SwiftShader which is a giveaway.
const getParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return getParam.call(this, p);
};
"""


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
        await ctx.add_init_script(_STEALTH_INIT)
        return ctx

    @staticmethod
    async def first_page(ctx: BrowserContext) -> Page:
        pages = ctx.pages
        if pages:
            return pages[0]
        return await ctx.new_page()
