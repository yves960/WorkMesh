"""Workflow Runner (Phase 1 MVP): state, loader, steps, idempotency, runner."""

from __future__ import annotations

from . import loader, steps

# Wire the loader's step-type defense to the live registry (register_step
# additions are visible immediately; no import cycle: steps does not import
# loader).
loader.set_known_step_types(lambda: frozenset(steps.STEP_REGISTRY))
