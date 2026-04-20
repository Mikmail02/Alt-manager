"""HTTP client talking to the CC Hub's /api/agent/* endpoints."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

_log = logging.getLogger("agent.hub")


class HubClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 8.0) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"X-Alt-Token": token, "User-Agent": "cchub-agent/0.1"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> Optional[Dict[str, Any]]:
        try:
            r = await self._client.get(f"{self._base}/api/ping")
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            _log.warning("ping failed: %s", exc)
            return None

    async def heartbeat(
        self,
        *,
        agent_name: str,
        agent_version: str,
        alts: List[Dict[str, Any]],
    ) -> bool:
        payload = {
            "agent_name": agent_name,
            "agent_version": agent_version,
            "alts": alts,
        }
        try:
            r = await self._client.post(f"{self._base}/api/agent/heartbeat", json=payload)
            r.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            _log.warning("heartbeat failed: %s", exc)
            return False
