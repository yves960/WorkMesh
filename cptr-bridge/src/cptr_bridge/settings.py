"""Admin UI settings (env-driven; no hardcoded absolute paths).

Environment variables:
  CTR_PROFILES_DIR   — ForgeLoop profiles directory (contains <name>/profile.json)
  CTR_FORGELOOP_SRC  — ForgeLoop src/ directory (optional; enables real-parser validation)
  CTR_STATE_DIR      — bridge state directory (default: ~/.openclaw/cptr-bridge/state)
  CTR_LOOP_CONFIG    — ForgeLoop global config path (default: ~/.loop-engineering/config.json)
"""
from __future__ import annotations

import os
from pathlib import Path


def _from_env(key: str) -> Path | None:
    value = os.environ.get(key, "").strip()
    return Path(value).expanduser() if value else None


def profiles_dir() -> Path:
    """Profiles directory; raises if CTR_PROFILES_DIR is not set."""
    path = _from_env("CTR_PROFILES_DIR")
    if path is None:
        raise RuntimeError("CTR_PROFILES_DIR is not configured")
    return path


def forgeloop_src_dir() -> Path | None:
    return _from_env("CTR_FORGELOOP_SRC")


def state_dir() -> Path:
    path = _from_env("CTR_STATE_DIR")
    if path is None:
        path = Path.home() / ".openclaw" / "cptr-bridge" / "state"
    return path


def routing_store_path() -> Path:
    return state_dir() / "routing.json"


def loop_config_path() -> Path:
    path = _from_env("CTR_LOOP_CONFIG")
    if path is None:
        path = Path.home() / ".loop-engineering" / "config.json"
    return path
