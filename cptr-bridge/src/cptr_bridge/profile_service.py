"""Profile CRUD against ForgeLoop profile.json files.

Reads/writes ``<CTR_PROFILES_DIR>/<name>/profile.json`` directly. Validation
mirrors ForgeLoop ``src/profile_store.py`` parsing rules (read-only reuse —
the upstream module is imported when CTR_FORGELOOP_SRC is available).
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import settings

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_VERIFIER_TYPES = ("java-ut", "maven")
_LIST_FIELDS = (
    "allowedPathSuffixes",
    "forbiddenAddedPatterns",
    "requiredPaths",
)


class ProfileError(ValueError):
    """User-facing validation/storage error."""


@dataclass
class ProfileMeta:
    name: str
    path: Path
    data: dict[str, Any]
    error: str | None = None


def _real_parser():
    """Import ForgeLoop's parse_engineering_profile if CTR_FORGELOOP_SRC is set."""
    src = settings.forgeloop_src_dir()
    if src is None or not src.is_dir():
        return None
    src_str = str(src)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)
    try:
        module = importlib.import_module("profile_store")
    except Exception:  # pragma: no cover - ForgeLoop src not importable
        return None
    return getattr(module, "parse_engineering_profile", None)


def validate_profile(name: str, data: Any) -> dict[str, Any]:
    """Validate and normalise a profile document. Raises ProfileError."""
    if not _NAME_RE.match(name or ""):
        raise ProfileError(
            "name 只能是小写字母/数字/连字符（2-64 字符），且以字母或数字开头"
        )
    if not isinstance(data, dict) or not data:
        raise ProfileError("profile 必须是非空 JSON 对象")

    profile: dict[str, Any] = {}
    for key in ("name", "description"):
        value = data.get(key)
        if isinstance(value, str) and value:
            profile[key] = value
    if profile.get("name", name) != name:
        raise ProfileError(f'文档内 name（{profile.get("name")}）与目录名（{name}）不一致')

    for key in ("maxIterations", "defaultMaxIterations"):
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ProfileError(f"{key} 必须是正整数")
        profile[key] = value

    for key in _LIST_FIELDS:
        value = data.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ProfileError(f"{key} 必须是字符串列表")
        profile[key] = value

    verifier = data.get("verifier")
    if verifier is not None:
        if not isinstance(verifier, dict):
            raise ProfileError("verifier 必须是对象")
        parsed: dict[str, Any] = {}
        verifier_type = verifier.get("type")
        if verifier_type is not None:
            if verifier_type not in _VERIFIER_TYPES:
                raise ProfileError(
                    f"不支持的 verifier type: {verifier_type!r}（可选: {' / '.join(_VERIFIER_TYPES)}）"
                )
            parsed["type"] = verifier_type
        for list_key in ("arguments", "requiredOutputPatterns"):
            values = verifier.get(list_key)
            if values is None:
                continue
            if not isinstance(values, list) or not all(
                isinstance(v, str) for v in values
            ):
                raise ProfileError(f"verifier.{list_key} 必须是字符串列表")
            parsed[list_key] = values
        for str_key in ("passReason", "failReason"):
            value = verifier.get(str_key)
            if isinstance(value, str) and value:
                parsed[str_key] = value
        profile["verifier"] = parsed

    completion = data.get("completion")
    if completion is not None:
        if not isinstance(completion, dict):
            raise ProfileError("completion 必须是对象")
        required = completion.get("requireVerifierPass")
        if required is not None:
            if not isinstance(required, bool):
                raise ProfileError("completion.requireVerifierPass 必须是布尔值")
            profile["completion"] = {"requireVerifierPass": required}

    # Cross-check with the real ForgeLoop parser when available.
    parse = _real_parser()
    if parse is not None:
        import ast

        try:
            expression = ast.Dict(
                keys=[ast.Constant(key) for key in profile],
                values=[ast.Constant(profile[key]) for key in profile],
            )
            parse(expression, Path(f"{name}/profile.json"))
        except Exception as error:  # real parser rejected the document
            raise ProfileError(str(error)) from error
    return profile


def list_profiles() -> list[ProfileMeta]:
    """All profiles in the configured dir (invalid ones flagged, not raised)."""
    root = settings.profiles_dir()
    result: list[ProfileMeta] = []
    if not root.is_dir():
        return result
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        path = child / "profile.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            error = None
        except (OSError, ValueError) as read_error:
            data, error = {}, str(read_error)
        result.append(ProfileMeta(name=child.name, path=path, data=data, error=error))
    return result


def load_profile(name: str) -> dict[str, Any]:
    path = _profile_path(name)
    if not path.exists():
        raise ProfileError(f"profile 不存在: {name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProfileError(f"读取 {name} 失败: {error}") from error


def save_profile(name: str, data: dict[str, Any]) -> dict[str, Any]:
    profile = validate_profile(name, data)
    path = _profile_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return profile


def delete_profile(name: str) -> None:
    path = _profile_path(name)
    if not path.exists():
        raise ProfileError(f"profile 不存在: {name}")
    parent = path.parent
    path.unlink()
    try:  # remove the now-empty profile dir (only if empty)
        parent.rmdir()
    except OSError:
        pass


def _profile_path(name: str) -> Path:
    if not _NAME_RE.match(name or "") or "/" in name or ".." in name:
        raise ProfileError(f"非法 profile 名: {name!r}")
    return settings.profiles_dir() / name / "profile.json"
