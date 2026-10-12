"""Workflow YAML loader + Pydantic schema validation + trigger matching.

Layout: workflows/<name>.yaml under the app root (settings.root_dir(), so
tests get isolation via AICP_ROOT). Load results are hot-cached per
(name, root) pair; clear_loader_cache() invalidates after editing YAML.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from ..settings import root_dir

WORKFLOWS_DIR = "workflows"

#: Workflow names are file stems: keep them path-traversal-proof.
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

#: Injected by workflow/__init__.py (steps.py imports nothing from here, so
#: a live callable avoids import cycles and sees register_step() additions).
_get_step_types = None


def set_known_step_types(getter) -> None:
    global _get_step_types
    _get_step_types = getter
    clear_loader_cache()


def _known_step_types() -> frozenset[str]:
    return frozenset(_get_step_types()) if _get_step_types else frozenset()


class WorkflowError(Exception):
    """Base class for workflow loading problems."""


class WorkflowLoadError(WorkflowError):
    """YAML/schema problem. `path` is the dotted field path (may be '')."""

    def __init__(self, message: str, path: str = ""):
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


class UserMessageTrigger(BaseModel):
    keywords_any: list[str] = Field(default_factory=list)
    min_intent_confidence: float = 0.0


class IssueStatusTrigger(BaseModel):
    from_: list[str] = Field(default=[], alias="from")
    to: list[str] = Field(default_factory=list)
    issue_type: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


_KNOWN_TRIGGERS = ("on_user_message", "on_issue_status_change")


class StepDef(BaseModel):
    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=list)
    timeout_seconds: int | None = Field(default=None, gt=0)
    optional: bool = False
    requires_approval: bool = False


class WorkflowDef(BaseModel):
    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    description: str = ""
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[StepDef] = Field(min_length=1)


def workflow_path(name: str) -> Any:
    if not _NAME_RE.match(name):
        raise WorkflowLoadError(f"invalid workflow name: {name!r}", path="name")
    return root_dir() / WORKFLOWS_DIR / f"{name}.yaml"


def _fmt_loc(loc: tuple[Any, ...]) -> str:
    """('steps', 0, 'timeout_seconds') -> 'steps[0].timeout_seconds'."""
    parts: list[str] = []
    for part in loc:
        if isinstance(part, int):
            parts[-1] = f"{parts[-1]}[{part}]" if parts else str(part)
        else:
            parts.append(str(part))
    return ".".join(parts)


def parse_workflow(raw: dict[str, Any]) -> WorkflowDef:
    """Validate one decoded YAML document into a WorkflowDef."""
    if not isinstance(raw, dict):
        raise WorkflowLoadError("workflow YAML must be a mapping at top level")
    try:
        defn = WorkflowDef(**raw)
    except ValidationError as exc:
        # Surface the first offending field path (e.g. steps[0].timeout_seconds).
        err = exc.errors()[0]
        loc = _fmt_loc(err.get("loc", ()))
        raise WorkflowLoadError(str(err.get("msg", "invalid field")), path=loc) from exc
    # Trigger kinds: single-key maps {on_user_message: {...}} / {on_issue_status_change: {...}}.
    for i, trig in enumerate(defn.triggers):
        unknown = set(trig) - set(_KNOWN_TRIGGERS)
        if len(trig) != 1 or unknown:
            raise WorkflowLoadError(
                f"trigger must be exactly one of {_KNOWN_TRIGGERS}, got {sorted(trig)}",
                path=f"triggers[{i}]",
            )
        kind = next(iter(trig))
        try:
            model = (
                UserMessageTrigger(**trig[kind])
                if kind == "on_user_message"
                else IssueStatusTrigger(**trig[kind])
            )
        except ValidationError as exc:
            err = exc.errors()[0]
            loc = _fmt_loc(err.get("loc", ()))
            raise WorkflowLoadError(
                str(err.get("msg", "invalid field")),
                path=f"triggers[{i}].{kind}.{loc}",
            ) from exc
        defn.triggers[i] = {kind: model.model_dump(by_alias=True)}
    # Defense: every step type must exist in the registry (live lookup so
    # register_step() additions count).
    known = _known_step_types()
    if known:
        for i, step in enumerate(defn.steps):
            if step.type not in known:
                raise WorkflowLoadError(
                    f"unknown step type {step.type!r}; known: {sorted(known)}",
                    path=f"steps[{i}].type",
                )
    return defn


@lru_cache(maxsize=128)
def _load_cached(name: str, root: str) -> WorkflowDef:
    path = root_dir() / WORKFLOWS_DIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"workflow not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise WorkflowLoadError(f"YAML parse error: {exc}") from exc
    return parse_workflow(raw or {})


def load_workflow(name: str) -> WorkflowDef:
    """Load + validate workflows/<name>.yaml (hot-cached per name+root)."""
    if not _NAME_RE.match(name):
        raise WorkflowLoadError(f"invalid workflow name: {name!r}", path="name")
    return _load_cached(name, str(root_dir()))


def clear_loader_cache() -> None:
    _load_cached.cache_clear()


def list_workflows() -> list[WorkflowDef]:
    """Every parseable workflow under workflows/ (bad files are skipped)."""
    directory = root_dir() / WORKFLOWS_DIR
    if not directory.is_dir():
        return []
    found: list[WorkflowDef] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            found.append(load_workflow(path.stem))
        except (WorkflowError, FileNotFoundError):
            continue
    return found


def triggers_summary(defn: WorkflowDef) -> str:
    """Human-readable one-liner of trigger conditions."""
    parts: list[str] = []
    for trig in defn.triggers:
        kind = next(iter(trig))
        spec = trig[kind]
        if kind == "on_user_message":
            parts.append(f"on_user_message(keywords_any={spec.get('keywords_any', [])})")
        else:
            parts.append(
                f"on_issue_status_change({spec.get('from', [])}"
                f"->{spec.get('to', [])})"
            )
    return "; ".join(parts) or "manual"


def matches_trigger(defn: WorkflowDef, trigger_context: dict[str, Any]) -> bool:
    """Does this trigger_context satisfy any declared trigger?

    - on_user_message: any keyword (case-insensitive) in trigger_context.user_message
    - on_issue_status_change: trigger_context.issue_id present AND
      trigger_context.issue_status in `to` (from is advisory)
    """
    ctx = trigger_context or {}
    user_message = str(ctx.get("user_message") or "")
    lowered = user_message.lower()
    for trig in defn.triggers:
        kind = next(iter(trig))
        spec = trig[kind]
        if kind == "on_user_message":
            keywords = spec.get("keywords_any") or []
            if not keywords or any(str(k).lower() in lowered for k in keywords):
                return True
        elif kind == "on_issue_status_change":
            to = spec.get("to") or []
            issue_id = str(ctx.get("issue_id") or "")
            status = str(ctx.get("issue_status") or ctx.get("status") or "")
            if issue_id and (not to or status in to):
                return True
    return not defn.triggers
