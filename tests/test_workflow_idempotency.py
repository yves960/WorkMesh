"""Idempotency keys: determinism, discrimination, one registration per key."""

from __future__ import annotations

import asyncio

from workflow_testkit import wf_env  # noqa: F401

from personal_ai_control_plane.workflow.idempotency import (
    check_and_register,
    make_idempotency_key,
)


def test_key_is_deterministic(wf_env):  # noqa: F811
    trigger = {"source": "open_webui", "user_message": "UT 挂了", "project_path": "/x"}
    assert make_idempotency_key("java-ut-repair", trigger) == \
        make_idempotency_key("java-ut-repair", trigger)


def test_different_triggers_produce_different_keys(wf_env):  # noqa: F811
    a = {"source": "open_webui", "user_message": "UT 挂了", "project_path": "/x"}
    b = {"source": "open_webui", "user_message": "UT 又挂了", "project_path": "/x"}
    assert make_idempotency_key("java-ut-repair", a) != make_idempotency_key("java-ut-repair", b)
    # same trigger, different workflow -> different key too
    assert make_idempotency_key("w1", a) != make_idempotency_key("w2", a)


def test_check_and_register_second_call_returns_first_execution_id(wf_env):  # noqa: F811
    key = make_idempotency_key("java-ut-repair", {"user_message": "UT 挂了"})

    first = asyncio.run(check_and_register(key, "wf-20260818-aaaaaa"))
    assert first is None  # new key accepted

    second = asyncio.run(check_and_register(key, "wf-20260818-bbbbbb"))
    assert second == "wf-20260818-aaaaaa"  # duplicate: existing id returned
