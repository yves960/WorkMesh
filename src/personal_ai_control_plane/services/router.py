from __future__ import annotations

import re
from typing import Any

from ..errors import ContextNotFound
from ..settings import load_routing_config
from .contexts import resolve_context

ISSUE_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def route_intent(text: str) -> dict[str, Any]:
    cfg = load_routing_config()
    lowered = text.casefold()
    selected = None

    # Execute first because words like "处理" may coexist with planning language.
    order = ["execute", "plan", "capture", "read", "ask"]
    for intent in order:
        spec = (cfg.get("intents") or {}).get(intent, {})
        hits = [
            str(k) for k in spec.get("keywords", []) if str(k).casefold() in lowered
        ]
        if hits:
            selected = {
                "intent": intent,
                "target": spec.get("target"),
                "matched_keywords": hits,
            }
            break

    if not selected:
        default = cfg.get("default", {"intent": "ask", "target": "open_webui"})
        selected = {**default, "matched_keywords": []}

    try:
        ctx = resolve_context(text)
    except ContextNotFound:
        ctx = None

    issue_keys = ISSUE_RE.findall(text.upper())

    if selected["intent"] == "execute":
        next_action = "resolve_or_find_multica_issue_then_require_explicit_write_action"
    elif selected["intent"] == "plan":
        next_action = "read_multica_context_and_prepare_plan"
    elif selected["intent"] in ("ask", "read"):
        next_action = "answer_in_open_webui_using_context_if_needed"
    else:
        next_action = "capture_in_open_webui_or_configured_inbox"

    return {
        "text": text,
        **selected,
        "context": ctx,
        "issue_keys": issue_keys,
        "next_action": next_action,
        "write_allowed": False,
    }
