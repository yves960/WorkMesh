"""WorkMesh intent-keyword → profile routing store.

Persisted as ``<CTR_STATE_DIR>/routing.json``:

    {"rules": [{"keywords": ["修复", "codecheck"], "profile": "codecheck-fixer"}]}
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import settings

_KEYWORD_RE = re.compile(r"^[^\s]{1,64}$")
DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "keywords": ["修复", "修复失败", "codecheck", "代码质量", "静态检查"],
        "profile": "codecheck-fixer",
    },
    {
        "keywords": ["单测", "单元测试", "UT失败", "java-ut", "测试失败"],
        "profile": "java-ut-fixer",
    },
    {
        "keywords": ["安全漏洞", "SAST", "安全扫描", "安全告警"],
        "profile": "sast-fixer",
    },
    {
        "keywords": ["依赖漏洞", "SCA", "组件升级", "漏洞版本"],
        "profile": "sca-upgrader",
    },
    {
        "keywords": ["依赖更新", "升级依赖", "dependency", "版本升级"],
        "profile": "dependency-upgrader",
    },
]


class RoutingError(ValueError):
    """User-facing routing validation error."""


def _validate_rule(rule: Any, known_profiles: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(rule, dict):
        raise RoutingError("每条规则必须是对象")
    keywords = rule.get("keywords")
    profile = rule.get("profile")
    if not isinstance(profile, str) or not profile:
        raise RoutingError("profile 不能为空")
    if not isinstance(keywords, list) or not keywords:
        raise RoutingError("keywords 不能为空")
    cleaned = []
    for keyword in keywords:
        if not isinstance(keyword, str) or not _KEYWORD_RE.match(keyword):
            raise RoutingError(f"非法关键词: {keyword!r}（1-64 字符，不含空白）")
        cleaned.append(keyword)
    if known_profiles is not None and profile not in known_profiles:
        raise RoutingError(f"profile 不存在: {profile}")
    return {"keywords": cleaned, "profile": profile}


def load_routing() -> dict[str, Any]:
    path = settings.routing_store_path()
    if not path.exists():
        return {"rules": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rules": []}
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        return {"rules": []}
    return {"rules": rules}


def save_routing(rules: list[dict[str, Any]]) -> dict[str, Any]:
    path = settings.routing_store_path()
    payload = {"rules": [_validate_rule(rule) for rule in rules]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def add_rule(rule: dict[str, Any], known_profiles: set[str] | None = None) -> dict[str, Any]:
    validated = _validate_rule(rule, known_profiles)
    routing = load_routing()
    routing["rules"].append(validated)
    return save_routing(routing["rules"])


def delete_rule(index: int) -> None:
    routing = load_routing()
    rules = routing["rules"]
    if index < 0 or index >= len(rules):
        raise RoutingError(f"规则序号不存在: {index}")
    rules.pop(index)
    save_routing(rules)


def ensure_default() -> Path:
    """Seed the default 5-rule template if the store does not exist yet."""
    path = settings.routing_store_path()
    if not path.exists():
        save_routing(DEFAULT_RULES)
    return path
