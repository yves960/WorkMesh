"""ForgeLoop global config editor (read-modify-write, key-preserving).

Edits ``<CTR_LOOP_CONFIG>`` (config.json):
  - hooks.onComplete   — webhook URL (http/https)
  - globalMaxIterations — top-level convenience key (int, default cap)
  - dryRunDefault      — top-level convenience key (bool)
All other existing keys (agent, delivery, ...) are preserved untouched.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from . import settings

_WEBHOOK_RE = re.compile(r"^https?://[^\s]+$")


class ForgeConfigError(ValueError):
    """User-facing config validation error."""


def load_config() -> dict[str, Any]:
    path = settings.loop_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ForgeConfigError(f"读取 config.json 失败: {error}") from error
    if not isinstance(data, dict):
        raise ForgeConfigError("config.json 顶层必须是对象")
    return data


def save_settings(
    webhook_url: str, global_max_iterations: int, dry_run_default: bool
) -> dict[str, Any]:
    webhook_url = (webhook_url or "").strip()
    if webhook_url and not _WEBHOOK_RE.match(webhook_url):
        raise ForgeConfigError("webhook URL 必须以 http:// 或 https:// 开头且不含空白")
    parsed = urlparse(webhook_url)
    if webhook_url and not parsed.netloc:
        raise ForgeConfigError("webhook URL 缺少主机名")
    if isinstance(global_max_iterations, bool) or not isinstance(
        global_max_iterations, int
    ):
        raise ForgeConfigError("globalMaxIterations 必须是整数")
    if global_max_iterations < 1:
        raise ForgeConfigError("globalMaxIterations 必须是正整数")

    config = load_config()
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    if webhook_url:
        hooks["onComplete"] = webhook_url
    else:
        hooks.pop("onComplete", None)
    config["hooks"] = hooks
    config["globalMaxIterations"] = global_max_iterations
    config["dryRunDefault"] = bool(dry_run_default)

    path = settings.loop_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return config


def current_settings() -> dict[str, Any]:
    config = load_config()
    hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
    max_iterations = config.get("globalMaxIterations")
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
        max_iterations = 10
    dry_run = config.get("dryRunDefault")
    return {
        "webhookUrl": (hooks or {}).get("onComplete", ""),
        "globalMaxIterations": max_iterations,
        "dryRunDefault": bool(dry_run),
        "configPath": str(settings.loop_config_path()),
    }
