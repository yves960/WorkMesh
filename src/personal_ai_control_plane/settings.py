import os
from pathlib import Path
from typing import Any

import yaml


def root_dir() -> Path:
    return Path(os.getenv("AICP_ROOT", Path.cwd())).resolve()


def data_dir() -> Path:
    path = root_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return data_dir() / "control-plane.db"


def load_yaml(relative: str) -> dict[str, Any]:
    path = root_dir() / relative
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_routing_config() -> dict[str, Any]:
    return load_yaml("config/routing.yaml")


def openwebui_url() -> str | None:
    value = os.getenv("OPEN_WEBUI_URL", "").strip().rstrip("/")
    return value or None


def openwebui_api_key() -> str | None:
    value = os.getenv("OPEN_WEBUI_API_KEY", "").strip()
    return value or None
