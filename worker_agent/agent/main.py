"""Orchestrator: start browser pool, spin up alts, pulse hub."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import List

from . import __version__
from .alt_runner import AltRunner
from .browser import BrowserPool
from .config import AgentConfig, load_config
from .hub_client import HubClient

_log = logging.getLogger("agent.main")

DEFAULT_CONFIG_PATH = Path(os.environ.get("AGENT_CONFIG", "config.toml"))


async def _heartbeat_loop(
    hub: HubClient,
    *,
    cfg: AgentConfig,
    runners: List[AltRunner],
    stop: asyncio.Event,
) -> None:
    interval = max(3, cfg.agent.heartbeat_interval_sec)
    while not stop.is_set():
        alts = [r.snapshot() for r in runners]
        ok = await hub.heartbeat(
            agent_name=cfg.agent.name,
            agent_version=__version__,
            alts=alts,
        )
        if ok:
            _log.debug("heartbeat ok (%d alts)", len(alts))
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def run() -> None:
    cfg = load_config(DEFAULT_CONFIG_PATH)
    _log.info(
        "agent %s starting — name=%s hub=%s alts=%d",
        __version__,
        cfg.agent.name,
        cfg.agent.hub_url,
        len(cfg.alts),
    )

    pool = BrowserPool(
        headless=cfg.browser.headless,
        user_data_base=Path(cfg.browser.user_data_base).resolve(),
    )
    await pool.start()

    runners: List[AltRunner] = []
    for alt in cfg.alts:
        r = AltRunner(
            alt_id=alt.id,
            username=alt.username,
            cookies_file=Path(alt.cookies_file).resolve(),
            pool=pool,
        )
        try:
            await r.start()
            runners.append(r)
        except Exception:
            _log.exception("failed to start alt %s", alt.id)

    hub = HubClient(cfg.agent.hub_url, cfg.agent.token)
    ping = await hub.ping()
    if ping:
        _log.info("hub reachable: version=%s", ping.get("version"))
    else:
        _log.warning("hub ping failed — will keep retrying via heartbeats")

    stop = asyncio.Event()

    def _signal_stop(*_args) -> None:
        _log.info("signal received, shutting down")
        stop.set()

    # Windows only supports SIGINT/SIGTERM through KeyboardInterrupt; Linux
    # needs explicit handlers so systemd stop works cleanly.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_stop)
        except NotImplementedError:
            pass  # Windows

    hb_task = asyncio.create_task(
        _heartbeat_loop(hub, cfg=cfg, runners=runners, stop=stop),
        name="heartbeat",
    )

    await stop.wait()

    hb_task.cancel()
    try:
        await hb_task
    except asyncio.CancelledError:
        pass

    for r in runners:
        await r.stop()
    await pool.stop()
    await hub.aclose()
    _log.info("agent stopped cleanly")
