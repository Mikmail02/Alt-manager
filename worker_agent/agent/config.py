"""Load config.toml and validate with pydantic."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field, field_validator

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


class AgentSection(BaseModel):
    name: str
    hub_url: str
    token: str
    heartbeat_interval_sec: int = 10

    @field_validator("hub_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return (v or "").rstrip("/")


class BrowserSection(BaseModel):
    headless: bool = True
    user_data_base: str = "./user_data"


class AltEntry(BaseModel):
    id: str
    username: str
    cookies_file: str


class AgentConfig(BaseModel):
    agent: AgentSection
    browser: BrowserSection = Field(default_factory=BrowserSection)
    alts: List[AltEntry] = Field(default_factory=list)


def load_config(path: Path) -> AgentConfig:
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return AgentConfig(**raw)
